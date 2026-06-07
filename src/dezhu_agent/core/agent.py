"""Agent 核心循环 —— 对话管理、工具调用调度."""

from __future__ import annotations
from typing import Any, cast
from openai import OpenAI
from openai.types.chat import ChatCompletionMessageFunctionToolCall
from dezhu_agent.config import get_config
from dezhu_agent.services.tool_registry import ToolRegistry


_config = get_config()
_registry = ToolRegistry.get_instance()

SYSTEM_PROMPT = "You are a helpful assistant. You can run shell commands via the terminal tool."

client = OpenAI(base_url=_config.BASE_URL, api_key=_config.API_KEY)


def agent_loop() -> None:
    """交互式 REPL 对话循环."""

    print("=== Agent Loop ===")
    print(f"Model: {_config.MODEL}")
    print(f"Base URL: {_config.BASE_URL}")
    print("Type 'quit' to exit.\n")

    messages: list[dict[str, Any]] = []

    while True:
        user_input = input("You: ").strip()
        if not user_input or user_input.lower() in ("quit", "exit"):
            break

        result = run_conversation(user_input, messages)
        print(f"\nAssistant: {result['final_response']}\n")


def run_conversation(user_message: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
    """同步 agent 循环: 调用模型, 执行工具, 回传结果, 反复直到模型不再请求工具."""
    messages.append({"role": "user", "content": user_message})

    for _ in range(_config.MAX_ITERATIONS):
        api_messages = [{"role": "system", "content": SYSTEM_PROMPT}, *messages]

        response = client.chat.completions.create(
            model=_config.MODEL,
            messages=api_messages,  # type: ignore[arg-type]
            tools=_registry.get_tools_for_openai() if not _registry.is_empty() else None,  # type: ignore[arg-type]
        )

        assistant_msg = response.choices[0].message

        msg_dict: dict[str, Any] = {
            "role": "assistant",
            "content": assistant_msg.content or "",
        }
        if assistant_msg.tool_calls:
            msg_dict["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": cast(ChatCompletionMessageFunctionToolCall, tc).function.name,
                        "arguments": cast(ChatCompletionMessageFunctionToolCall, tc).function.arguments,
                    },
                }
                for tc in assistant_msg.tool_calls
            ]
        messages.append(msg_dict)

        if not assistant_msg.tool_calls:
            return {
                "final_response": assistant_msg.content,
                "messages": messages,
            }

        for tool_call in assistant_msg.tool_calls:
            tc = cast(ChatCompletionMessageFunctionToolCall, tool_call)
            print(f"  [tool] {tc.function.name}: {tc.function.arguments}")
            output = _registry.execute(tc.function.name, tc.function.arguments)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": output,
                }
            )

    return {
        "final_response": "(max iterations reached)",
        "messages": messages,
    }
