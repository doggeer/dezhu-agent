"""核心对话循环：run_conversation."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import TYPE_CHECKING

from dezhu_agent.config import ITERATION_BUDGET, STREAM_MODE
from dezhu_agent.llm import LLMResponse, call_llm, call_llm_stream
from dezhu_agent.messages import Message, messages_to_api_messages
from dezhu_agent.prompt import build_system_prompt, build_tools_for_api
from dezhu_agent.tools import registry

if TYPE_CHECKING:
    from dezhu_agent.storage import StorageBackend


def run_conversation(
    user_message: str,
    history: list[Message] | None = None,
    on_stream_chunk: Callable | None = None,
    storage: StorageBackend | None = None,
    session_id: str | None = None,
) -> tuple[str, list[Message]]:
    """运行对话循环，直到模型不再调用工具或 budget 耗尽.

    Args:
        user_message: 用户输入的消息文本。
        history: 可选的历史消息列表。
        on_stream_chunk: 流式输出回调，接收 StreamChunk 对象。
                        为 None 时使用非流式调用。
        storage: 可选的存储后端，用于持久化消息。
        session_id: 当前会话 ID（与 storage 配合使用）。

    Returns:
        (final_reply, messages) 元组：
        - final_reply: 模型的最终回复文本。
        - messages: 完整的内部消息历史（可用于后续轮次）。

    异常路径 E1：API 调用失败时向上抛出异常（fail-fast）。
    """
    # B1: 空消息直接返回提示
    if not user_message.strip():
        return "（消息为空，请输入有效内容）", history or []

    # 记录传入历史长度，用于计算本轮新增消息
    history_start_len = len(history) if history else 0

    # 初始化消息历史
    messages: list[Message] = list(history) if history else []
    messages.append(Message(role="user", content=user_message))

    # 获取工具列表
    tools = registry.get_tools()

    # 获取或组装 system prompt（session 内复用保证缓存稳定）
    if storage is not None and session_id is not None:
        system_prompt = storage.load_system_prompt(session_id)
        if not system_prompt:
            system_prompt = build_system_prompt(tools)
            storage.save_system_prompt(session_id, system_prompt)
    else:
        system_prompt = build_system_prompt(tools)

    api_tools = build_tools_for_api(tools) if tools else None

    iteration = 0
    use_stream = STREAM_MODE and on_stream_chunk is not None

    # 持久化辅助函数
    def _persist() -> None:
        if storage is not None and session_id is not None:
            new_msgs = messages[history_start_len:]
            if new_msgs:
                storage.save_messages(session_id, new_msgs)

    while iteration < ITERATION_BUDGET:
        iteration += 1

        # 组装 system prompt + 消息
        sys_msg = {"role": "system", "content": system_prompt}
        api_messages = [sys_msg] + messages_to_api_messages(messages)

        if use_stream:
            response = _run_streaming_call(api_messages, api_tools, on_stream_chunk)
        else:
            # 非流式调用（E1: 异常直接向上抛出）
            response = call_llm(api_messages, tools=api_tools)

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
            return response.content or "", messages

        elif response.finish_reason == "tool_calls":
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

                # 控制台显示工具执行信息
                _log_tool_execution(tool_name, tool_args, result)

                messages.append(
                    Message(
                        role="tool",
                        content=result,
                        tool_call_id=tc["id"],
                        name=tool_name,
                    )
                )

        elif response.finish_reason == "length":
            continue

        else:
            _persist()
            return response.content or "", messages

    # E4: iteration budget 耗尽
    _persist()
    last_assistant = ""
    for m in reversed(messages):
        if m.role == "assistant" and m.content:
            last_assistant = m.content
            break
    return last_assistant, messages


def _log_tool_execution(name: str, args: dict, result: str) -> None:
    """将工具执行信息打印到 stderr，方便控制台观察."""
    sep = "─" * 50
    args_str = json.dumps(args, ensure_ascii=False)
    # 结果截断到前 3 行 + 后 3 行
    lines = result.splitlines()
    if len(lines) > 6:
        result_display = "\n".join(lines[:3] + ["  …"] + lines[-3:])
    else:
        result_display = result
    print(f"\n{sep}", file=sys.stderr)
    print(f"  🔧 {name}({args_str})", file=sys.stderr)
    print(f"  📋 {result_display}", file=sys.stderr)
    print(f"{sep}\n", file=sys.stderr)


def _run_streaming_call(
    api_messages: list[dict],
    api_tools: list[dict] | None,
    on_chunk: Callable,
) -> LLMResponse:
    """执行一次流式 API 调用，通过回调逐 chunk 输出，返回完整 LLMResponse."""
    generator = call_llm_stream(api_messages, tools=api_tools)
    final_response: LLMResponse | None = None

    try:
        while True:
            chunk = next(generator)
            if on_chunk:
                on_chunk(chunk)
    except StopIteration as e:
        final_response = e.value

    return final_response or LLMResponse(content="", finish_reason="stop", tool_calls=None)
