"""LLM API 客户端基座，封装 openai SDK 调用."""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass
from typing import Any

from openai import OpenAI

from dezhu_agent.config import (
    API_TIMEOUT,
    MODEL_NAME,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    REASONING_EFFORT,
    THINKING_ENABLED,
    _getenv_required,
)

_client: OpenAI | None = None


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


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        api_key = OPENAI_API_KEY
        if not api_key:
            api_key = _getenv_required("OPENAI_API_KEY")
        _client = OpenAI(
            api_key=api_key,
            base_url=OPENAI_BASE_URL,
            timeout=API_TIMEOUT,
        )
    return _client


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
        以及 prompt_cache_hit/miss_tokens.

    Raises:
        openai.APIError: API 调用失败时向上抛出（fail-fast）。
    """
    client = _get_client()
    kwargs = _build_kwargs(messages, tools, model, stream=False)
    response = client.chat.completions.create(**kwargs)
    choice = response.choices[0]
    message = choice.message

    finish_reason: str = choice.finish_reason or "stop"
    tool_calls = _parse_tool_calls(message)
    reasoning_content: str | None = getattr(message, "reasoning_content", None)
    hit, miss = _extract_cache_tokens(getattr(response, "usage", None))

    return LLMResponse(
        content=message.content,
        finish_reason=finish_reason,
        tool_calls=tool_calls,
        reasoning_content=reasoning_content,
        prompt_cache_hit_tokens=hit,
        prompt_cache_miss_tokens=miss,
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
    """
    client = _get_client()
    kwargs = _build_kwargs(messages, tools, model, stream=True)
    response = client.chat.completions.create(**kwargs)

    accumulated_content = ""
    accumulated_reasoning = ""
    accumulated_tool_calls: list[dict[str, Any]] | None = None
    finish_reason: str = "stop"
    cache_hit: int = 0
    cache_miss: int = 0

    for chunk in response:
        # 从 usage chunk（choices 为空）提取缓存统计
        if not chunk.choices and chunk.usage:
            cache_hit, cache_miss = _extract_cache_tokens(chunk.usage)
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
                        {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
                    )
                if tc.id:
                    accumulated_tool_calls[idx]["id"] = tc.id
                if tc.function and tc.function.name:
                    accumulated_tool_calls[idx]["function"]["name"] = tc.function.name
                if tc.function and tc.function.arguments:
                    accumulated_tool_calls[idx]["function"]["arguments"] += tc.function.arguments

        if finish:
            sc.finish_reason = finish
            sc.tool_calls = accumulated_tool_calls
            sc.accumulated_reasoning = accumulated_reasoning
            sc.accumulated_content = accumulated_content
            sc.prompt_cache_hit_tokens = cache_hit
            sc.prompt_cache_miss_tokens = cache_miss

        yield sc

    return LLMResponse(
        content=accumulated_content,
        finish_reason=finish_reason,
        tool_calls=accumulated_tool_calls,
        reasoning_content=accumulated_reasoning,
        prompt_cache_hit_tokens=cache_hit,
        prompt_cache_miss_tokens=cache_miss,
    )
