"""Agent 核心循环 —— 对话管理、工具调用调度、上下文压缩."""

from __future__ import annotations

from typing import Any

from openai import OpenAI

from dezhu_agent.config import get_config
from dezhu_agent.core.compression import CompressionStuckError, ContextCompressor
from dezhu_agent.core.prompt_builder import build_system_prompt
from dezhu_agent.models.message import ConversationResult
from dezhu_agent.services.session_store import get_session_store
from dezhu_agent.services.tool_registry import get_tool_registry

_config = get_config()
_registry = get_tool_registry()

client = OpenAI(base_url=_config.BASE_URL, api_key=_config.API_KEY)
_compressor = ContextCompressor(_config)


def agent_loop() -> None:
    """交互式 REPL 对话循环, 支持会话持久化与上下文压缩."""

    store = get_session_store()
    store.init_db()

    sessions = store.list_sessions(10)

    print("=== Agent Loop ===")
    print(f"Model: {_config.MODEL}")
    print(f"Base URL: {_config.BASE_URL}")
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
                    messages = store.load_messages(session_id)
                    print(f"Restored {len(messages)} messages from session {session_id[:8]}...\n")
                    break
            except ValueError:
                pass
            print("Invalid choice, try again.")

    if session_id is None:
        session_id = store.create_session("cli", _config.MODEL)
        system_prompt = build_system_prompt(model=_config.MODEL)
        store.store_system_prompt(session_id, system_prompt)
        print(f"Created new session: {session_id[:8]}...\n")
    else:
        cached = store.get_system_prompt(session_id)
        if cached:
            system_prompt = cached
        else:
            system_prompt = build_system_prompt(model=_config.MODEL)
            store.store_system_prompt(session_id, system_prompt)

    saved_count = len(messages)
    print("Type 'quit' to exit.\n")

    while True:
        user_input = input("You: ").strip()
        if not user_input or user_input.lower() in ("quit", "exit"):
            new_msgs = messages[saved_count:]
            if new_msgs:
                store.append_messages(session_id, new_msgs)
            break

        result = run_conversation(user_input, messages, system_prompt)
        print(f"\nAssistant: {result.final_response}\n")

        # 压缩信号处理：创建新 session 链接到旧 session
        if result.compression_triggered:
            new_id = store.create_session("cli", _config.MODEL, parent_session_id=session_id)
            store.store_system_prompt(new_id, build_system_prompt(model=_config.MODEL))
            store.append_messages(new_id, result.messages)
            print(f"  [compression] New session {new_id[:8]}... created (parent: {session_id[:8]}...)\n")
            session_id = new_id
            saved_count = 0
            messages = result.messages
        else:
            new_msgs = messages[saved_count:]
            if new_msgs:
                store.append_messages(session_id, new_msgs)
                saved_count = len(messages)


def run_conversation(
    user_message: str,
    messages: list[dict[str, Any]],
    system_prompt: str,
) -> ConversationResult:
    """同步 agent 循环：压缩守卫 → 模型调用 → 工具执行.

    集成上下文压缩：
    - Preflight: 进循环前检查 token，超阈值则执行完整压缩
    - 循环内: 每轮清理旧工具输出，仍超阈值则执行完整压缩
    - CompressionStuckError: 压缩无效时早退
    """
    compression_triggered = False
    messages.append({"role": "user", "content": user_message})

    # ---- Preflight 压缩 ----
    try:
        if _compressor.estimate_tokens(messages) > _config.COMPRESSION_THRESHOLD:
            messages, ok = _compressor.compress(messages)
            compression_triggered = ok
    except CompressionStuckError:
        return ConversationResult(
            final_response="会话上下文已满，无法继续压缩。请新开会话。",
            messages=messages,
            compression_triggered=True,
        )

    for _ in range(_config.MAX_ITERATIONS):
        # ---- 循环内 Layer 1: 清理旧工具输出 ----
        _compressor.clear_old_tool_outputs(messages)

        # ---- 循环内 Layer 2+3: 仍超阈值则完整压缩 ----
        try:
            if _compressor.estimate_tokens(messages) > _config.COMPRESSION_THRESHOLD:
                messages, ok = _compressor.compress(messages)
                compression_triggered = compression_triggered or ok
        except CompressionStuckError:
            return ConversationResult(
                final_response="会话上下文已满，无法继续压缩。请新开会话。",
                messages=messages,
                compression_triggered=True,
            )

        api_messages = [{"role": "system", "content": system_prompt}, *messages]

        response = client.chat.completions.create(
            model=_config.MODEL,
            messages=api_messages,  # type: ignore[arg-type]
            tools=_registry.get_tools_for_openai() or None,  # type: ignore[arg-type]
        )

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
                            "name": tc.function.name,  # type: ignore[union-attr]
                            "arguments": tc.function.arguments,  # type: ignore[union-attr]
                        },
                    }
                )
            msg["tool_calls"] = tool_calls
        messages.append(msg)

        if not assistant_msg.tool_calls:
            return ConversationResult(
                final_response=assistant_msg.content or "",
                messages=messages,
                compression_triggered=compression_triggered,
            )

        for tc in assistant_msg.tool_calls:
            name = tc.function.name  # type: ignore[union-attr]
            args = tc.function.arguments  # type: ignore[union-attr]
            print(f"  [tool] {name}: {args}")
            output = _registry.execute(name, args)
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": output})

    return ConversationResult(
        final_response="(max iterations reached)",
        messages=messages,
        compression_triggered=compression_triggered,
    )
