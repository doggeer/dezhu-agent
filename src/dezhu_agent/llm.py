"""LLM API 客户端基座，封装 openai SDK 调用."""

from __future__ import annotations

import random
import time
import traceback
from collections.abc import Generator
from dataclasses import dataclass
from typing import Any

from openai import OpenAI

from dezhu_agent.config import (
    API_TIMEOUT,
    BACKOFF_BASE_DELAY,
    BACKOFF_MAX_DELAY,
    MODEL_NAME,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    REASONING_EFFORT,
    RETRY_TIMEOUT,
    THINKING_ENABLED,
    _getenv_required,
)
from dezhu_agent.error_classifier import ErrorCategory, classify_error
from dezhu_agent.logging_config import get_logger

logger = get_logger(__name__)


class ClientFactory:
    """按 (api_key, base_url) 创建/缓存 OpenAI client。"""

    _clients: dict[tuple[str, str], OpenAI] = {}

    @classmethod
    def get_client(cls, api_key: str, base_url: str) -> OpenAI:
        key = (api_key, base_url)
        if key not in cls._clients:
            cls._clients[key] = OpenAI(
                api_key=api_key,
                base_url=base_url,
                timeout=API_TIMEOUT,
            )
        return cls._clients[key]

    @classmethod
    def reset(cls) -> None:
        """清除所有缓存的 client（连接健康检查用）。"""
        cls._clients.clear()


# 模块级凭证状态（故障转移时可切换）
_current_api_key: str | None = None
_current_base_url: str | None = None


def _get_client() -> OpenAI:
    """获取当前凭证对应的 OpenAI client（懒加载）。"""
    api_key = _current_api_key or OPENAI_API_KEY
    if not api_key:
        api_key = _getenv_required("OPENAI_API_KEY")
    base_url = _current_base_url or OPENAI_BASE_URL
    return ClientFactory.get_client(api_key, base_url)


def set_client_credentials(api_key: str | None, base_url: str | None) -> None:
    """切换当前 API 凭证（故障转移时调用）。"""
    global _current_api_key, _current_base_url
    _current_api_key = api_key
    _current_base_url = base_url


def reset_client_connection() -> None:
    """清除所有缓存的 client 连接（连接健康检查用）。"""
    ClientFactory.reset()


@dataclass
class LLMResponse:
    """LLM 调用的结构化返回."""

    content: str | None
    finish_reason: str
    tool_calls: list[dict[str, Any]] | None
    reasoning_content: str | None = None
    # DeepSeek 硬盘缓存统计
    prompt_cache_hit_tokens: int = 0
    prompt_cache_miss_tokens: int = 0
    # completion token 数（用于 thinking-budget 检测）
    completion_tokens: int = 0


@dataclass
class StreamChunk:
    """流式输出的单个 chunk."""

    content_delta: str = ""
    reasoning_delta: str = ""
    finish_reason: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    """仅在最后一个 chunk 携带完整的 tool_calls 列表 (accumulated)。"""
    accumulated_reasoning: str = ""
    """该请求累积的完整 reasoning_content (仅在 Finish chunk 填充)。"""
    accumulated_content: str = ""
    """该请求累积的完整 content (仅在 Finish chunk 填充)。"""
    # DeepSeek 硬盘缓存统计（仅在 Finish chunk 填充）
    prompt_cache_hit_tokens: int = 0
    prompt_cache_miss_tokens: int = 0


class UnrecoverableError(RuntimeError):
    """不可恢复错误 —— 退避超时或所有备用模型耗尽时抛出。"""

    def __init__(self, message: str, original_error: Exception | None = None) -> None:
        super().__init__(message)
        self.original_error = original_error


def _build_kwargs(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    model: str | None = None,
    stream: bool = False,
) -> dict[str, Any]:
    """构建共用的 API 参数字典."""
    kwargs: dict[str, Any] = {
        "model": model or MODEL_NAME,
        "messages": messages,
    }
    if tools:
        kwargs["tools"] = tools
    if stream:
        kwargs["stream"] = True
        # 流式模式下请求 usage 信息（含缓存统计）
        kwargs["stream_options"] = {"include_usage": True}
    if THINKING_ENABLED:
        kwargs["reasoning_effort"] = REASONING_EFFORT
        kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
    return kwargs


def _parse_tool_calls(message: Any) -> list[dict[str, Any]] | None:
    """从 API 响应消息中提取 tool_calls (dict 格式)."""
    if not message.tool_calls:
        return None
    return [
        {
            "id": tc.id,
            "type": tc.type,
            "function": {
                "name": tc.function.name,
                "arguments": tc.function.arguments,
            },
        }
        for tc in message.tool_calls
    ]


def _extract_cache_tokens(usage: Any) -> tuple[int, int]:
    """从 API usage 对象提取缓存命中/未命中 token 数."""
    if usage is None:
        return 0, 0
    hit = getattr(usage, "prompt_cache_hit_tokens", 0) or 0
    miss = getattr(usage, "prompt_cache_miss_tokens", 0) or 0
    return int(hit), int(miss)


def call_llm(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    model: str | None = None,
) -> LLMResponse:
    """非流式调用 LLM API.

    Returns:
        LLMResponse 包含 content / finish_reason / tool_calls / reasoning_content
        以及 prompt_cache_hit/miss_tokens 和 completion_tokens.

    Raises:
        openai.APIError: API 调用失败时向上抛出（fail-fast）。
    """
    model_name = model or MODEL_NAME
    tools_count = len(tools) if tools else 0
    logger.info(
        "LLM 非流式调用: model=%s, messages=%d 条, tools=%d 个",
        model_name,
        len(messages),
        tools_count,
    )
    logger.debug("LLM 请求 payload:\nmodel=%s\nmessages=%s\ntools=%s", model_name, messages, tools)

    try:
        client = _get_client()
        kwargs = _build_kwargs(messages, tools, model, stream=False)
        response = client.chat.completions.create(**kwargs)
    except Exception as e:
        logger.error(
            "LLM API 调用异常: model=%s, messages=%d 条, exception=%s: %s",
            model_name,
            len(messages),
            type(e).__name__,
            e,
        )
        logger.debug("LLM API 异常 traceback:\n%s", traceback.format_exc())
        raise

    choice = response.choices[0]
    message = choice.message

    finish_reason: str = choice.finish_reason or "stop"
    tool_calls = _parse_tool_calls(message)
    reasoning_content: str | None = getattr(message, "reasoning_content", None)
    hit, miss = _extract_cache_tokens(getattr(response, "usage", None))

    total_tokens = getattr(response, "usage", None)
    prompt_tokens = getattr(total_tokens, "prompt_tokens", 0) if total_tokens else 0
    completion_tokens = getattr(total_tokens, "completion_tokens", 0) if total_tokens else 0

    logger.info(
        "LLM 响应: finish_reason=%s, prompt_tokens=%d (hit=%d, miss=%d), completion_tokens=%d",
        finish_reason,
        prompt_tokens,
        hit,
        miss,
        completion_tokens,
    )
    if tool_calls:
        tool_names = [tc["function"]["name"] for tc in tool_calls]
        logger.info("LLM tool_calls: %s", tool_names)
    logger.debug("LLM 响应全文: content=%s, reasoning=%s", message.content, reasoning_content)

    return LLMResponse(
        content=message.content,
        finish_reason=finish_reason,
        tool_calls=tool_calls,
        reasoning_content=reasoning_content,
        prompt_cache_hit_tokens=hit,
        prompt_cache_miss_tokens=miss,
        completion_tokens=completion_tokens,
    )


def call_llm_stream(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    model: str | None = None,
) -> Generator[StreamChunk, None, LLMResponse]:
    """流式调用 LLM API，逐 chunk 产出。

    Yields:
        StreamChunk: 每个包含 content_delta / reasoning_delta / finish_reason.

    Returns:
        最后一个 chunk 通过 StopIteration.value 返回完整的 LLMResponse.
        若流式中断，返回已累积的部分内容（finish_reason="length"）。
    """
    model_name = model or MODEL_NAME
    tools_count = len(tools) if tools else 0
    logger.info(
        "LLM 流式调用: model=%s, messages=%d 条, tools=%d 个",
        model_name,
        len(messages),
        tools_count,
    )
    logger.debug(
        "LLM 流式请求 payload:\nmodel=%s\nmessages=%s\ntools=%s", model_name, messages, tools
    )

    try:
        client = _get_client()
        kwargs = _build_kwargs(messages, tools, model, stream=True)
        response = client.chat.completions.create(**kwargs)
    except Exception as e:
        logger.error(
            "LLM 流式 API 调用异常: model=%s, messages=%d 条, exception=%s: %s",
            model_name,
            len(messages),
            type(e).__name__,
            e,
        )
        logger.debug("LLM 流式 API 异常 traceback:\n%s", traceback.format_exc())
        raise

    accumulated_content = ""
    accumulated_reasoning = ""
    accumulated_tool_calls: list[dict[str, Any]] | None = None
    finish_reason: str = "stop"
    cache_hit: int = 0
    cache_miss: int = 0
    completion_tokens: int = 0

    try:
        for chunk in response:
            # 从 usage chunk（choices 为空）提取缓存统计
            if not chunk.choices and chunk.usage:
                cache_hit, cache_miss = _extract_cache_tokens(chunk.usage)
                ct = getattr(chunk.usage, "completion_tokens", 0) or 0
                if ct:
                    completion_tokens = int(ct)
                continue

            delta = chunk.choices[0].delta if chunk.choices else None
            finish = chunk.choices[0].finish_reason if chunk.choices else None

            if finish:
                finish_reason = finish or "stop"

            if delta is None:
                continue

            reasoning_delta = getattr(delta, "reasoning_content", None)
            content_delta = delta.content

            sc = StreamChunk()

            if reasoning_delta:
                accumulated_reasoning += reasoning_delta
                sc.reasoning_delta = reasoning_delta

            if content_delta:
                accumulated_content += content_delta
                sc.content_delta = content_delta

            if delta.tool_calls:
                if accumulated_tool_calls is None:
                    accumulated_tool_calls = []
                for tc in delta.tool_calls:
                    idx = tc.index
                    while len(accumulated_tool_calls) <= idx:
                        accumulated_tool_calls.append(
                            {
                                "id": "",
                                "type": "function",
                                "function": {"name": "", "arguments": ""},
                            }
                        )
                    if tc.id:
                        accumulated_tool_calls[idx]["id"] = tc.id
                    if tc.function and tc.function.name:
                        accumulated_tool_calls[idx]["function"]["name"] = tc.function.name
                    if tc.function and tc.function.arguments:
                        accumulated_tool_calls[idx]["function"]["arguments"] += (
                            tc.function.arguments
                        )

            if finish:
                sc.finish_reason = finish
                sc.tool_calls = accumulated_tool_calls
                sc.accumulated_reasoning = accumulated_reasoning
                sc.accumulated_content = accumulated_content
                sc.prompt_cache_hit_tokens = cache_hit
                sc.prompt_cache_miss_tokens = cache_miss

            yield sc

    except Exception as e:
        logger.warning(
            "流式响应中断: %s, 已累积 content=%d chars",
            e,
            len(accumulated_content),
        )
        # E4: 有累积内容时设 finish_reason="length"，确保上层续写触发
        if accumulated_content:
            finish_reason = "length"

    logger.info(
        "LLM 流式响应完成: finish_reason=%s, content_len=%d, cache_hit=%d, cache_miss=%d",
        finish_reason,
        len(accumulated_content),
        cache_hit,
        cache_miss,
    )
    if accumulated_tool_calls:
        tool_names = [tc["function"]["name"] for tc in accumulated_tool_calls]
        logger.info("LLM 流式 tool_calls: %s", tool_names)
    logger.debug(
        "LLM 流式响应全文: finish_reason=%s, content=%s, reasoning=%s",
        finish_reason,
        accumulated_content,
        accumulated_reasoning,
    )

    return LLMResponse(
        content=accumulated_content,
        finish_reason=finish_reason,
        tool_calls=accumulated_tool_calls,
        reasoning_content=accumulated_reasoning,
        prompt_cache_hit_tokens=cache_hit,
        prompt_cache_miss_tokens=cache_miss,
        completion_tokens=completion_tokens,
    )


def with_retry(
    fn,
    *args,
    max_retry_seconds: int | None = None,
    **kwargs,
) -> LLMResponse:
    """对 LLM 调用添加退避重试包装。

    仅对临时故障（rate_limit / timeout / server_error）退避重试，
    使用指数退避 + 随机抖动。其他异常直接传播。

    Args:
        fn: 要调用的函数（call_llm 或 _run_streaming_call）。
        *args: 传给 fn 的位置参数。
        max_retry_seconds: 最大重试总时间（秒），默认使用 RETRY_TIMEOUT。
        **kwargs: 传给 fn 的关键字参数。

    Returns:
        LLMResponse: fn 的成功返回值。

    Raises:
        UnrecoverableError: 退避超时。
        Exception: 非临时故障的异常直接传播。
    """
    timeout = max_retry_seconds if max_retry_seconds is not None else RETRY_TIMEOUT
    deadline = time.monotonic() + timeout
    attempt = 0

    while True:
        try:
            return fn(*args, **kwargs)
        except UnrecoverableError:
            # 已经是不可恢复错误，直接传播
            raise
        except Exception as e:
            category = classify_error(e)

            if category in (
                ErrorCategory.rate_limit,
                ErrorCategory.timeout,
                ErrorCategory.server_error,
            ):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise UnrecoverableError(
                        f"退避重试超时（{timeout}s），最后错误: {e}",
                        original_error=e,
                    ) from e

                # 指数退避: min(base_delay * 2^attempt + random_jitter, max_delay)
                base = BACKOFF_BASE_DELAY
                max_delay = BACKOFF_MAX_DELAY
                delay = min(base * (2**attempt) + random.uniform(0, base), max_delay)
                # 不超过剩余时间
                delay = min(delay, remaining)
                attempt += 1

                logger.warning(
                    "退避重试: attempt=%d, delay=%.1fs, category=%s, error=%s",
                    attempt,
                    delay,
                    category.value,
                    e,
                )
                time.sleep(delay)
            else:
                # 非临时故障，直接抛出
                raise
