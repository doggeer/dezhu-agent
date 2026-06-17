"""Agent 核心循环 —— 对话管理、工具调用调度、上下文压缩与错误恢复."""

from __future__ import annotations

from typing import Any

import structlog

from dezhu_agent.config import Settings, get_config
from dezhu_agent.core.compression import CompressionStuckError, ContextCompressor
from dezhu_agent.core.model_client import ModelClient
from dezhu_agent.core.prompt_builder import build_system_prompt
from dezhu_agent.core.slash_commands import CommandResult, handle_command
from dezhu_agent.core.task_state import get_task_state_manager, set_current_session_id
from dezhu_agent.models.error import ErrorCategory
from dezhu_agent.models.message import ConversationResult, Message
from dezhu_agent.services.session_store import get_session_store
from dezhu_agent.services.tool_registry import ToolRegistry, get_tool_registry

logger = structlog.get_logger(__name__)


def agent_loop() -> None:
    """交互式 REPL 对话循环, 支持会话持久化、上下文压缩与错误恢复."""

    store = get_session_store()
    store.init_db()

    config = get_config()
    registry = get_tool_registry()
    model_client = ModelClient(config)
    compressor = ContextCompressor(config)

    # ---- 健康检查 ----
    if not model_client.check_health():
        logger.warning("API 健康检查失败, 对话可能无法正常工作")

    sessions = store.list_sessions(10)

    print("=== Agent Loop ===")
    print(f"Model: {config.MODEL}")
    print(f"Base URL: {config.BASE_URL}")
    print()

    session_id: str | None = None
    messages: list[dict[str, Any]] = []
    system_prompt: str = ""

    if sessions:
        print("Recent sessions:")
        for i, s in enumerate(sessions, 1):
            parent = f" <- {s.parent_session_id[:4]}" if s.parent_session_id else ""
            print(f"  [{i}] {s.id[:4]}  {s.createtime}  {s.model}  {s.message_count} messages{parent}")
        print("  [n] New session")
        print()

        while True:
            choice = input("Select: ").strip()
            if choice.lower() == "n":
                break
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(sessions):
                    session_id = sessions[idx].id
                    messages = [m.to_dict() for m in store.load_messages(session_id)]
                    print(f"Restored {len(messages)} messages from session {session_id[:8]}...\n")
                    break
            except ValueError:
                pass
            print("Invalid choice, try again.")

    if session_id is None:
        session_id = store.create_session("cli", config.MODEL)
        system_prompt = build_system_prompt(model=config.MODEL)
        store.store_system_prompt(session_id, system_prompt)
        print(f"Created new session: {session_id[:8]}...\n")
    else:
        cached = store.get_system_prompt(session_id)
        if cached:
            system_prompt = cached
        else:
            system_prompt = build_system_prompt(model=config.MODEL)
            store.store_system_prompt(session_id, system_prompt)

    print("Type /help for commands, quit to exit.\n")

    while True:
        user_input = input("You: ").strip()
        cmd_result = handle_command(user_input, messages, compressor, config)
        if cmd_result == CommandResult.QUIT:
            break
        if cmd_result == CommandResult.HANDLED:
            continue

        # ---- 主模型恢复 ----
        model_client.try_recover_main()

        result = run_conversation(
            user_input,
            messages,
            system_prompt,
            session_id,
            model_client=model_client,
            compressor=compressor,
            registry=registry,
            config=config,
        )

        if result.error:
            print(f"\n[错误] {result.error}\n")
        else:
            print(f"\nAssistant: {result.final_response}\n")

        if result.compression_triggered:
            new_id = store.create_session("cli", config.MODEL, parent_session_id=session_id)
            store.store_system_prompt(new_id, build_system_prompt(model=config.MODEL))
            store.append_messages(new_id, [Message.from_dict(m) for m in result.messages])
            print(f"  [compression] New session {new_id[:8]}... created (parent: {session_id[:8]}...)\n")
            session_id = new_id
            messages = result.messages


def run_conversation(
    user_message: str,
    messages: list[dict[str, Any]],
    system_prompt: str,
    session_id: str,
    *,
    model_client: ModelClient,
    compressor: ContextCompressor,
    registry: ToolRegistry,
    config: Settings,
) -> ConversationResult:
    """同步 agent 循环: 压缩守卫 → 模型调用 (含错误恢复) → 工具执行.

    错误恢复流程:
    - finish_reason=length → ModelClient 自动续写
    - 400 上下文溢出 → 压缩后重试一次
    - 401/404 → 故障转移到备用模型
    - 403/thinking-budget → 放弃, 返回错误
    """
    compression_triggered = False
    messages.append({"role": "user", "content": user_message})
    get_session_store().store_message(session_id, Message.from_dict(messages[-1]))

    # ---- Task State 初始化 ----
    set_current_session_id(session_id)
    task_state = get_task_state_manager().get_or_create(session_id)

    # ---- Preflight 压缩 ----
    try:
        if compressor.estimate_tokens(messages) > config.COMPRESSION_THRESHOLD:
            messages, ok = compressor.compress(messages)
            compression_triggered = ok
    except CompressionStuckError:
        return ConversationResult(
            final_response="会话上下文已满，无法继续压缩。请新开会话。",
            messages=messages,
            compression_triggered=True,
        )

    for _ in range(config.MAX_ITERATIONS):
        task_state.increment_round()

        # ---- 循环内 Layer 1: 清理旧工具输出 ----
        compressor.clear_old_tool_outputs(messages)

        # ---- 循环内 Layer 2+3: 仍超阈值则完整压缩 ----
        try:
            if compressor.estimate_tokens(messages) > config.COMPRESSION_THRESHOLD:
                messages, ok = compressor.compress(messages)
                compression_triggered = compression_triggered or ok
        except CompressionStuckError:
            return ConversationResult(
                final_response="会话上下文已满，无法继续压缩。请新开会话。",
                messages=messages,
                compression_triggered=True,
            )

        # 活跃任务期间: 将 task_state 拼到 system prompt 末尾
        augmented_system = system_prompt
        if task_state.is_active:
            augmented_system = system_prompt + "\n\n" + task_state.render()

        api_messages = [{"role": "system", "content": augmented_system}, *messages]
        tools = registry.get_tools_for_openai() or None

        # ---- 模型调用 (含续写) ----
        result = model_client.call_with_continuation(api_messages, tools)

        # ---- 错误恢复 ----
        if not result.success:
            result = _recover_from_error(
                result,
                api_messages,
                messages,
                tools,
                augmented_system,
                model_client,
                compressor,
                task_state.is_active,
            )
            if not result.success:
                return ConversationResult(
                    final_response="",
                    messages=messages,
                    compression_triggered=compression_triggered,
                    error=result.error_message,
                )

        # ---- 自适应校准 ----
        response = result.response
        assert response is not None
        if response.usage:
            compressor.calibrate(compressor._raw_estimate(api_messages), response.usage.prompt_tokens)

        assistant_msg = response.choices[0].message

        msg: dict[str, Any] = {
            "role": "assistant",
            "content": assistant_msg.content or "",
        }
        if assistant_msg.tool_calls:
            tool_calls_list: list[dict[str, Any]] = []
            for tc in assistant_msg.tool_calls:
                tool_calls_list.append(
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                )
            msg["tool_calls"] = tool_calls_list
        messages.append(msg)
        get_session_store().store_message(session_id, Message.from_dict(messages[-1]))

        if not assistant_msg.tool_calls:
            # 模型返回最终回复, 但任务活跃且未完成: 注入提醒
            if task_state.is_active and not task_state.is_completed:
                task_reminder = (
                    "[System Reminder] You have an active task "
                    f"(goal: '{task_state.goal}') but replied without marking it as completed. "
                    "Use todo_update to mark remaining steps as completed, "
                    "or explicitly mark the task as done."
                )
                messages.append({"role": "user", "content": str(task_reminder)})
                get_session_store().store_message(session_id, Message.from_dict(messages[-1]))
            return ConversationResult(
                final_response=assistant_msg.content or "",
                messages=messages,
                compression_triggered=compression_triggered,
            )

        for tc in assistant_msg.tool_calls:
            name = tc.function.name
            args = tc.function.arguments
            print(f"  [tool] {name}: {args}")
            output = registry.execute(name, args)
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": output})
            get_session_store().store_message(session_id, Message.from_dict(messages[-1]))

        # ---- 轮次提醒: 超过阈值轮未更新 TODO 时提醒 ----
        reminder = task_state.check_reminder(config.TODO_REMINDER_ROUNDS)
        if reminder:
            messages.append({"role": "user", "content": str(reminder)})
            get_session_store().store_message(session_id, Message.from_dict(messages[-1]))

    return ConversationResult(
        final_response="(max iterations reached)",
        messages=messages,
        compression_triggered=compression_triggered,
    )


# ---- 错误恢复辅助函数 ----


def _recover_from_error(
    result: Any,
    api_messages: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    augmented_system: str,
    model_client: ModelClient,
    compressor: ContextCompressor,
    is_active: bool,
) -> Any:
    """根据错误分类执行对应的恢复策略.

    Returns:
        恢复后的 ApiCallResult, 若恢复失败则保持 success=False.
    """
    if result.category == ErrorCategory.CONTEXT_OVERFLOW:
        logger.warning("上下文溢出 (400), 尝试压缩后重试")
        try:
            compressed, _ok = compressor.compress(messages)
            messages[:] = compressed
            new_api_messages = [{"role": "system", "content": augmented_system}, *messages]
            retry_result = model_client.call(new_api_messages, tools)
            if retry_result.success:
                return retry_result
            logger.error("压缩后重试仍失败: %s", retry_result.error_message)
        except CompressionStuckError as exc:
            logger.error("压缩无效: %s", exc)
        # 压缩后仍失败，当作 FATAL
        result.category = ErrorCategory.FATAL

    if result.category in (ErrorCategory.AUTH_FAILURE, ErrorCategory.MODEL_NOT_FOUND):
        logger.warning("认证/模型错误, 尝试故障转移")
        if model_client.try_fallback():
            new_api_messages = [{"role": "system", "content": augmented_system}, *messages]
            retry_result = model_client.call_with_continuation(new_api_messages, tools)
            if retry_result.success:
                return retry_result
            logger.error("故障转移后调用仍失败: %s", retry_result.error_message)
        result.category = ErrorCategory.FATAL

    return result
