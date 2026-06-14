"""Agent 核心循环 —— 对话管理、工具调用调度、上下文压缩."""

from __future__ import annotations

import time
from typing import Any

import structlog
from openai import APIError, APITimeoutError, OpenAI, RateLimitError

from dezhu_agent.config import Settings, get_config
from dezhu_agent.core.compression import CompressionStuckError, ContextCompressor
from dezhu_agent.core.task_state import get_task_state_manager, set_current_session_id
from dezhu_agent.core.prompt_builder import build_system_prompt
from dezhu_agent.models.message import ConversationResult, Message
from dezhu_agent.services.session_store import get_session_store
from dezhu_agent.services.tool_registry import ToolRegistry, get_tool_registry

logger = structlog.get_logger(__name__)

# ---- API 重试配置 ----
_MAX_RETRIES = 3
_RETRY_BACKOFF = 2.0  # seconds base
_RETRYABLE = (RateLimitError, APITimeoutError, APIError)


def _call_with_retry(
    client: OpenAI,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
) -> Any:
    """调用 OpenAI chat completion, 对可重试错误自动重试."""
    last_exc: Exception | None = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            return client.chat.completions.create(
                model=model,
                messages=messages,  # type: ignore[arg-type]
                tools=tools or None,  # type: ignore[arg-type]
            )
        except _RETRYABLE as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES:
                wait = _RETRY_BACKOFF**attempt
                logger.warning(
                    "API call failed (attempt %d/%d), retrying in %.1fs: %s", attempt, _MAX_RETRIES, wait, exc
                )
                time.sleep(wait)
        except Exception:
            raise
    raise last_exc  # type: ignore[misc]


def agent_loop() -> None:
    """交互式 REPL 对话循环, 支持会话持久化与上下文压缩."""

    store = get_session_store()
    store.init_db()

    config = get_config()
    registry = get_tool_registry()
    client = OpenAI(base_url=config.BASE_URL, api_key=config.API_KEY)
    compressor = ContextCompressor(config)

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

    print("Type 'quit' to exit.\n")

    while True:
        user_input = input("You: ").strip()
        if not user_input or user_input.lower() in ("quit", "exit"):
            break

        result = run_conversation(
            user_input,
            messages,
            system_prompt,
            session_id,
            client=client,
            compressor=compressor,
            registry=registry,
            config=config,
        )
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
    client: OpenAI,
    compressor: ContextCompressor,
    registry: ToolRegistry,
    config: Settings,
) -> ConversationResult:
    """同步 agent 循环：压缩守卫 → 模型调用 → 工具执行.

    集成上下文压缩：
    - Preflight: 进循环前检查 token，超阈值则执行完整压缩
    - 循环内: 每轮清理旧工具输出，仍超阈值则执行完整压缩
    - CompressionStuckError: 压缩无效时早退
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
            messages, ok = compressor.compress(messages, is_active=task_state.is_active)
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
        compressor.clear_old_tool_outputs(messages, is_active=task_state.is_active)

        # ---- 循环内 Layer 2+3: 仍超阈值则完整压缩 ----
        try:
            if compressor.estimate_tokens(messages) > config.COMPRESSION_THRESHOLD:
                messages, ok = compressor.compress(messages, is_active=task_state.is_active)
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

        response = _call_with_retry(
            client,
            config.MODEL,
            api_messages,
            registry.get_tools_for_openai() or None,
        )

        # 自适应校准: 用实际 prompt_tokens 修正 token 估算
        if response.usage:
            compressor.calibrate(compressor._raw_estimate(api_messages), response.usage.prompt_tokens)

        assistant_msg = response.choices[0].message

        msg: dict[str, Any] = {
            "role": "assistant",
            "content": assistant_msg.content or "",
        }
        if assistant_msg.tool_calls:
            tool_calls: list[dict[str, Any]] = []
            for tc in assistant_msg.tool_calls:
                tool_calls.append(
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                )
            msg["tool_calls"] = tool_calls
        messages.append(msg)
        get_session_store().store_message(session_id, Message.from_dict(messages[-1]))

        if not assistant_msg.tool_calls:
            # 模型返回最终回复, 但任务活跃且未完成: 注入提醒
            if task_state.is_active and not task_state.is_completed:
                reminder = (
                    "[System Reminder] You have an active task "
                    f"(goal: '{task_state.goal}') but replied without marking it as completed. "
                    "Use todo_update to mark remaining steps as completed, "
                    "or explicitly mark the task as done."
                )
                messages.append({"role": "user", "content": reminder})
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
            messages.append({"role": "user", "content": reminder})
            get_session_store().store_message(session_id, Message.from_dict(messages[-1]))

    return ConversationResult(
        final_response="(max iterations reached)",
        messages=messages,
        compression_triggered=compression_triggered,
    )
