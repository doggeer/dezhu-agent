"""核心对话循环：run_conversation."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import TYPE_CHECKING

from dezhu_agent.compression import (
    CompressionConfig,
    CompressionStuckError,
    compress,
    estimate_tokens,
    get_task_state,
)
from dezhu_agent.config import (
    COMPRESSION_AUX_API_KEY,
    COMPRESSION_AUX_BASE_URL,
    COMPRESSION_AUX_MODEL,
    COMPRESSION_ENABLED,
    COMPRESSION_PREFLIGHT_RATIO,
    COMPRESSION_TRIGGER_RATIO,
    COMPRESSION_WINDOW_SIZE,
    ITERATION_BUDGET,
    PROVIDER_CONFIGS,
    STREAM_MODE,
)
from dezhu_agent.error_classifier import ErrorCategory, classify_error
from dezhu_agent.llm import (
    LLMResponse,
    UnrecoverableError,
    call_llm,
    call_llm_stream,
    reset_client_connection,
    set_client_credentials,
    with_retry,
)
from dezhu_agent.logging_config import get_logger, set_session_context
from dezhu_agent.messages import Message, messages_to_api_messages
from dezhu_agent.prompt import build_system_prompt, build_tools_for_api
from dezhu_agent.tools import registry

if TYPE_CHECKING:
    from dezhu_agent.storage import StorageBackend

logger = get_logger(__name__)

# ---- 续写常量 ----

CONTINUE_MESSAGE = (
    "Your response was cut off. Continue EXACTLY from where you stopped. "
    "Do not restart, do not repeat, do not summarize what came before."
)
MAX_CONTINUATION_ATTEMPTS = 3


def _try_failover(
    current_provider_index: int,
    current_model_index: int,
) -> tuple[int, int, str, str | None, str | None] | None:
    """尝试故障转移到下一个可用模型。

    Returns:
        (provider_idx, model_idx, model_name, api_key, base_url) 或 None（全部耗尽）。
    """
    provider_configs = PROVIDER_CONFIGS

    # 先尝试同提供商的下一个模型
    if current_provider_index < len(provider_configs):
        provider = provider_configs[current_provider_index]
        next_model_idx = current_model_index + 1
        if next_model_idx < len(provider["models"]):
            model_name = provider["models"][next_model_idx]
            print(
                f"⚠️ 主模型不可用，已切换到备用模型 {model_name}",
                file=sys.stderr,
            )
            return (
                current_provider_index,
                next_model_idx,
                model_name,
                provider["api_key"],
                provider["base_url"],
            )

    # 同提供商耗尽，尝试下一个提供商
    for pi in range(current_provider_index + 1, len(provider_configs)):
        provider = provider_configs[pi]
        if provider["models"]:
            model_name = provider["models"][0]
            print(
                f"⚠️ 主模型不可用，已切换到备用模型 {model_name}",
                file=sys.stderr,
            )
            return (pi, 0, model_name, provider["api_key"], provider["base_url"])

    return None  # 全部耗尽


def run_conversation(
    user_message: str,
    history: list[Message] | None = None,
    on_stream_chunk: Callable | None = None,
    storage: StorageBackend | None = None,
    session_id: str | None = None,
) -> tuple[str, list[Message], str | None]:
    """运行对话循环，直到模型不再调用工具或 budget 耗尽.

    Args:
        user_message: 用户输入的消息文本。
        history: 可选的历史消息列表。
        on_stream_chunk: 流式输出回调，接收 StreamChunk 对象。
                        为 None 时使用非流式调用。
        storage: 可选的存储后端，用于持久化消息。
        session_id: 当前会话 ID（与 storage 配合使用）。

    Returns:
        (final_reply, messages, session_id) 元组：
        - final_reply: 模型的最终回复文本。
        - messages: 完整的内部消息历史（可用于后续轮次）。
        - session_id: 当前会话 ID（压缩分裂后可能已变更）。

    异常路径 E1：API 调用失败时向上抛出异常（fail-fast）。
    """
    # 同步 session 上下文到日志系统
    set_session_context(session_id or "")

    # B1: 空消息直接返回提示
    if not user_message.strip():
        return "（消息为空，请输入有效内容）", history or [], session_id

    # N2: 记录用户输入
    logger.info("用户输入: %s", user_message)

    # 记录传入历史长度，用于计算本轮新增消息
    history_start_len = len(history) if history else 0

    # 初始化消息历史
    messages: list[Message] = list(history) if history else []
    messages.append(Message(role="user", content=user_message))

    # 获取工具列表
    tools = registry.get_tools()

    # 获取或组装 system prompt（session 内复用保证缓存稳定）
    cache_hit = False
    if storage is not None and session_id is not None:
        system_prompt = storage.load_system_prompt(session_id)
        if not system_prompt:
            system_prompt = build_system_prompt(tools)
            storage.save_system_prompt(session_id, system_prompt)
        else:
            cache_hit = True
    else:
        system_prompt = build_system_prompt(tools)

    logger.info("System prompt 缓存%s", "命中" if cache_hit else "未命中，已重建")
    logger.debug("System prompt 全文:\n%s", system_prompt)

    api_tools = build_tools_for_api(tools) if tools else None

    iteration = 0
    use_stream = STREAM_MODE and on_stream_chunk is not None
    _accumulated_content = ""  # 续写累积内容
    _continuation_count = 0    # 续写尝试次数

    # 故障转移状态
    _current_provider_idx = 0
    _current_model_idx = 0
    _current_model: str | None = None  # None = 使用默认 MODEL_NAME

    # ---- 压缩配置 ----
    _compression_config = CompressionConfig(
        enabled=COMPRESSION_ENABLED,
        trigger_ratio=COMPRESSION_TRIGGER_RATIO,
        preflight_ratio=COMPRESSION_PREFLIGHT_RATIO,
        window_size=COMPRESSION_WINDOW_SIZE,
        aux_model=COMPRESSION_AUX_MODEL,
        aux_api_key=COMPRESSION_AUX_API_KEY,
        aux_base_url=COMPRESSION_AUX_BASE_URL,
    )

    # 持久化辅助函数
    def _persist() -> None:
        if storage is not None and session_id is not None:
            new_msgs = messages[history_start_len:]
            if new_msgs:
                storage.save_messages(session_id, new_msgs)

    # 重建 system prompt（压缩后复用，不含 TaskState——TaskState 每轮动态拼接）
    def _rebuild_prompt() -> str:
        return build_system_prompt(tools)

    # ---- 连接健康检查 + 主模型恢复 ----
    reset_client_connection()
    if _current_model is not None:
        # 当前运行在备用模型上，尝试恢复主模型
        logger.info("尝试恢复主模型...")
        try:
            _restore_primary_model()
            _current_model = None
            _current_provider_idx = 0
            _current_model_idx = 0
            _apply_provider_credentials(0)
            logger.info("主模型恢复成功")
        except Exception:
            logger.info("主模型恢复失败，继续使用备用模型 %s", _current_model)

    # ---- Preflight 压缩（步骤 11） ----
    if _compression_config.enabled and storage is not None and session_id is not None:
        _api_msgs_for_check = messages_to_api_messages(messages)
        preflight_tokens = estimate_tokens(_api_msgs_for_check)
        if preflight_tokens >= _compression_config.preflight_threshold:
            logger.info(
                "Preflight 压缩触发: %d tokens >= %d (threshold)",
                preflight_tokens,
                _compression_config.preflight_threshold,
            )
            try:
                result = compress(_api_msgs_for_check, _compression_config)
                # 将压缩后的 dict 列表转回 Message 列表
                messages = _dicts_to_messages(
                    result.after_dicts if hasattr(result, 'after_dicts') else _api_msgs_for_check
                )
                history_start_len = 0  # 压缩后重置基准，后续消息从 0 开始持久化
                if result.layers_applied:
                    # preflight 不创建新 session，直接在当前 session 继续
                    system_prompt = _rebuild_prompt()
                    storage.save_system_prompt(session_id, system_prompt)
                    logger.info(
                        "Preflight 压缩完成: %d → %d tokens (layers: %s)",
                        preflight_tokens,
                        result.after_tokens,
                        result.layers_applied,
                    )
            except CompressionStuckError as e:
                logger.error(
                    "Preflight 压缩 stuck: %d → %d tokens",
                    e.before, e.after,
                )
                print(
                    f"\n⚠️ 会话已无法压缩（{e.before} → {e.after} tokens），请新开 session。\n",
                    file=sys.stderr,
                )
                return "会话已无法压缩，请新开 session。", messages, session_id

    while iteration < ITERATION_BUDGET:
        iteration += 1

        # 组装 system prompt + 消息（TaskState 每轮动态拼接）
        task_state_text = get_task_state().render()
        sys_msg = {"role": "system", "content": system_prompt + "\n\n" + task_state_text}
        api_messages = [sys_msg] + messages_to_api_messages(messages)

        logger.debug("第 %d 轮迭代 - TaskState:\n%s", iteration, task_state_text)
        logger.debug("第 %d 轮 - token 估算: %d", iteration, estimate_tokens(api_messages))

        # ---- 主循环压缩检查（步骤 12） ----
        if (
            _compression_config.enabled
            and storage is not None
            and session_id is not None
            and estimate_tokens(api_messages) >= _compression_config.trigger_threshold
        ):
            current_tokens = estimate_tokens(api_messages)
            logger.info(
                "主循环压缩触发: %d tokens >= %d (threshold)",
                current_tokens,
                _compression_config.trigger_threshold,
            )
            try:
                # 压缩消息历史（不含 system prompt）
                history_dicts = messages_to_api_messages(messages)
                result = compress(history_dicts, _compression_config)

                if result.layers_applied:
                    old_session_id = session_id
                    # 创建新 session（分裂）
                    new_session_id = storage.create_session(
                        parent_session_id=session_id
                    )
                    logger.info(
                        "压缩导致 session 分裂: %s -> %s",
                        old_session_id[:8] if old_session_id else "-",
                        new_session_id[:8],
                    )

                    # 转换压缩后的消息并持久化
                    messages = _dicts_to_messages(result.after_dicts)
                    history_start_len = 0  # 压缩后重置基准
                    storage.save_messages(new_session_id, messages)

                    # 重建 system prompt
                    system_prompt = _rebuild_prompt()
                    storage.save_system_prompt(new_session_id, system_prompt)

                    # 切换到新 session
                    session_id = new_session_id
                    set_session_context(session_id)

                    # 重建 api_messages（已变更）
                    sys_msg = {"role": "system", "content": system_prompt}
                    api_messages = [sys_msg] + messages_to_api_messages(messages)

            except CompressionStuckError as e:
                logger.error(
                    "主循环压缩 stuck: %d → %d tokens",
                    e.before, e.after,
                )
                _persist()
                print(
                    f"\n⚠️ 会话已无法压缩（{e.before} → {e.after} tokens），请新开 session。\n",
                    file=sys.stderr,
                )
                return "会话已无法压缩，请新开 session。", messages, session_id

        # ---- API 调用（含上下文压缩恢复 + 故障转移） ----
        response = _make_api_call_with_recovery(
            use_stream=use_stream,
            api_messages=api_messages,
            api_tools=api_tools,
            on_stream_chunk=on_stream_chunk,
            messages=messages,
            system_prompt=system_prompt,
            compression_config=_compression_config,
            current_model=_current_model,
            current_provider_idx=_current_provider_idx,
            current_model_idx=_current_model_idx,
        )

        # 更新故障转移状态
        _current_model = response.get("_model", _current_model)
        _current_provider_idx = response.get("_provider_idx", _current_provider_idx)
        _current_model_idx = response.get("_model_idx", _current_model_idx)
        # 如果压缩重建了 messages，更新引用
        if "_messages" in response:
            messages = response["_messages"]
        response = response["response"]

        # 创建 assistant 消息并加入历史
        assistant_msg = Message(
            role="assistant",
            content=response.content,
            tool_calls=response.tool_calls,
            reasoning_content=response.reasoning_content,
            prompt_cache_hit_tokens=response.prompt_cache_hit_tokens,
            prompt_cache_miss_tokens=response.prompt_cache_miss_tokens,
        )
        messages.append(assistant_msg)

        if response.finish_reason == "stop":
            _persist()
            reply = (_accumulated_content or "") + (response.content or "")
            reply_preview = reply[:500] + ("…" if len(reply) > 500 else "")
            logger.info(
                "对话正常结束: %d 轮迭代, 回复长度 %d 字符, 回复=%s",
                iteration, len(reply), reply_preview,
            )
            logger.debug("最终回复全文:\n%s", reply)
            return reply, messages, session_id

        elif response.finish_reason == "tool_calls":
            # 重置续写状态
            _accumulated_content = ""
            _continuation_count = 0

            if not response.tool_calls:
                continue

            for tc in response.tool_calls:
                tool_name = tc["function"]["name"]
                tool_args_str = tc["function"]["arguments"]

                try:
                    tool_args = json.loads(tool_args_str) if tool_args_str else {}
                except json.JSONDecodeError:
                    tool_args = {}

                result = registry.execute(tool_name, tool_args)

                messages.append(
                    Message(
                        role="tool",
                        content=result,
                        tool_call_id=tc["id"],
                        name=tool_name,
                    )
                )

        elif response.finish_reason == "length":
            # thinking-budget 检测
            if (
                response.completion_tokens > 0
                and not response.content
                and not response.tool_calls
            ):
                print("⚠️ 思考模式占用了全部输出空间", file=sys.stderr)
                _persist()
                reply = _accumulated_content or ""
                return reply, messages, session_id

            _continuation_count += 1
            _accumulated_content += (response.content or "")

            if _continuation_count > MAX_CONTINUATION_ATTEMPTS:
                logger.warning(
                    "续写已达上限 %d 次，停止续写",
                    MAX_CONTINUATION_ATTEMPTS,
                )
                _persist()
                return _accumulated_content, messages, session_id

            # 注入续写提示（role="user"）
            messages.append(Message(role="user", content=CONTINUE_MESSAGE))
            continue

        else:
            _persist()
            return response.content or "", messages, session_id

    # E4: iteration budget 耗尽
    _persist()
    last_assistant = ""
    for m in reversed(messages):
        if m.role == "assistant" and m.content:
            last_assistant = m.content
            break
    reply_preview = last_assistant[:500] + ("…" if len(last_assistant) > 500 else "")
    logger.info(
        "对话 budget 耗尽: %d 轮后未完成, 最终回复=%s",
        ITERATION_BUDGET, reply_preview,
    )
    return last_assistant, messages, session_id


def _make_api_call_with_recovery(
    *,
    use_stream: bool,
    api_messages: list[dict],
    api_tools: list[dict] | None,
    on_stream_chunk: Callable | None,
    messages: list[Message],
    system_prompt: str,
    compression_config: CompressionConfig,
    current_model: str | None,
    current_provider_idx: int,
    current_model_idx: int,
) -> dict:
    """执行一次 API 调用，含压缩恢复和故障转移。

    Returns:
        dict with keys: "response" (LLMResponse), plus optional
        "_model", "_provider_idx", "_model_idx", "_messages" for state updates.
    """
    provider_idx = current_provider_idx
    model_idx = current_model_idx
    model = current_model
    local_messages = messages

    # 设置当前凭证
    _apply_provider_credentials(provider_idx)

    while True:
        try:
            if use_stream:
                resp = with_retry(_run_streaming_call, api_messages, api_tools, on_stream_chunk, model=model)
            else:
                resp = with_retry(call_llm, api_messages, tools=api_tools, model=model)
            return {
                "response": resp,
                "_model": model,
                "_provider_idx": provider_idx,
                "_model_idx": model_idx,
            }
        except UnrecoverableError as e:
            # 退避超时 → 故障转移
            logger.warning("退避超时，尝试故障转移: %s", e)
            failover = _try_failover(provider_idx, model_idx)
            if failover is None:
                print("⚠️ 所有备用模型已耗尽", file=sys.stderr)
                raise
            provider_idx, model_idx, model, api_key, base_url = failover
            set_client_credentials(api_key, base_url)
            continue
        except Exception as e:
            category = classify_error(e)

            if category == ErrorCategory.context_overflow:
                logger.warning(
                    "上下文超长，尝试压缩: %d tokens",
                    estimate_tokens(api_messages),
                )
                try:
                    history_dicts = messages_to_api_messages(local_messages)
                    result = compress(history_dicts, compression_config)
                    if result.layers_applied:
                        local_messages = _dicts_to_messages(result.after_dicts)
                        sys_msg = {"role": "system", "content": system_prompt}
                        api_messages = [sys_msg] + messages_to_api_messages(local_messages)
                        logger.info(
                            "上下文压缩完成: %d -> %d tokens, 重试",
                            result.before_tokens, result.after_tokens,
                        )
                    # 压缩后重试（含退避保护 + E3: 再次 overflow 时警告）
                    try:
                        if use_stream:
                            resp = with_retry(
                                _run_streaming_call,
                                api_messages, api_tools, on_stream_chunk, model=model,
                            )
                        else:
                            resp = with_retry(
                                call_llm, api_messages, tools=api_tools, model=model,
                            )
                        return {
                            "response": resp,
                            "_messages": local_messages,
                            "_model": model,
                            "_provider_idx": provider_idx,
                            "_model_idx": model_idx,
                        }
                    except Exception as retry_e:
                        if classify_error(retry_e) == ErrorCategory.context_overflow:
                            print(
                                "\u26a0\ufe0f 压缩后上下文仍然超长，请新开 session",
                                file=sys.stderr,
                            )
                            return {
                                "response": LLMResponse(content="", finish_reason="stop", tool_calls=None),
                                "_messages": local_messages,
                                "_model": model,
                                "_provider_idx": provider_idx,
                                "_model_idx": model_idx,
                            }
                        raise
                except CompressionStuckError as se:
                    logger.error("压缩 stuck: %s", se)
                    print(f"\n⚠️ 上下文压缩无法减小: {se}", file=sys.stderr)
                    # 尝试故障转移（备用模型可能有更大的上下文窗口）
                    failover = _try_failover(provider_idx, model_idx)
                    if failover is None:
                        raise UnrecoverableError(
                            f"上下文压缩 stuck 且所有备用模型已耗尽: {se}", original_error=e,
                        ) from e
                    provider_idx, model_idx, model, api_key, base_url = failover
                    set_client_credentials(api_key, base_url)
                    continue

            elif category in (
                ErrorCategory.auth,
                ErrorCategory.billing,
                ErrorCategory.model_not_found,
            ):
                failover = _try_failover(provider_idx, model_idx)
                if failover is None:
                    print("⚠️ 所有备用模型已耗尽", file=sys.stderr)
                    raise UnrecoverableError(
                        f"所有备用模型已耗尽: {e}", original_error=e,
                    ) from e
                provider_idx, model_idx, model, api_key, base_url = failover
                set_client_credentials(api_key, base_url)
                continue

            else:
                raise


def _apply_provider_credentials(provider_idx: int) -> None:
    """根据当前 provider index 设置 API 凭证。"""
    if provider_idx < len(PROVIDER_CONFIGS):
        provider = PROVIDER_CONFIGS[provider_idx]
        set_client_credentials(provider["api_key"], provider["base_url"])
    else:
        set_client_credentials(None, None)  # 使用默认


def _restore_primary_model() -> None:
    """尝试 ping 主模型恢复连接，失败则抛出异常。"""
    from dezhu_agent.config import MODEL_NAME

    # 保存当前凭证
    provider = PROVIDER_CONFIGS[0]
    set_client_credentials(provider["api_key"], provider["base_url"])
    primary_model = provider["models"][0] if provider["models"] else MODEL_NAME
    # 轻量 ping
    call_llm(
        [{"role": "user", "content": "ping"}],
        model=primary_model,
        tools=None,
    )


def _run_streaming_call(
    api_messages: list[dict],
    api_tools: list[dict] | None,
    on_chunk: Callable,
    model: str | None = None,
) -> LLMResponse:
    """执行一次流式 API 调用，通过回调逐 chunk 输出，返回完整 LLMResponse."""
    generator = call_llm_stream(api_messages, tools=api_tools, model=model)
    final_response: LLMResponse | None = None

    try:
        while True:
            chunk = next(generator)
            if on_chunk:
                on_chunk(chunk)
    except StopIteration as e:
        final_response = e.value

    return final_response or LLMResponse(content="", finish_reason="stop", tool_calls=None)


def _dicts_to_messages(dicts: list[dict]) -> list[Message]:
    """将 API 格式的 dict 列表转回 Message 对象列表。

    用于压缩后将 after_dicts 转回内部 Message 格式。
    """
    messages: list[Message] = []
    for d in dicts:
        # 跳过 system 消息（它不在 messages 列表中）
        if d.get("role") == "system":
            continue
        messages.append(Message(
            role=d.get("role", ""),
            content=d.get("content"),
            tool_calls=d.get("tool_calls"),
            tool_call_id=d.get("tool_call_id"),
            name=d.get("name"),
            reasoning_content=d.get("reasoning_content"),
        ))
    return messages
