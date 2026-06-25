"""核心对话循环：run_conversation."""

from __future__ import annotations

import json
from collections.abc import Callable

from dezhu_agent.config import ITERATION_BUDGET, STREAM_MODE
from dezhu_agent.llm import LLMResponse, call_llm, call_llm_stream
from dezhu_agent.messages import Message, messages_to_api_messages
from dezhu_agent.prompt import build_system_prompt, build_tools_for_api
from dezhu_agent.tools import execute_tool, get_tool_list


def run_conversation(
    user_message: str,
    history: list[Message] | None = None,
    on_stream_chunk: Callable | None = None,
) -> tuple[str, list[Message]]:
    """运行对话循环，直到模型不再调用工具或 budget 耗尽.

    Args:
        user_message: 用户输入的消息文本。
        history: 可选的历史消息列表。
        on_stream_chunk: 流式输出回调，接收 StreamChunk 对象。
                        为 None 时使用非流式调用。

    Returns:
        (final_reply, messages) 元组：
        - final_reply: 模型的最终回复文本。
        - messages: 完整的内部消息历史（可用于后续轮次）。

    异常路径 E1：API 调用失败时向上抛出异常（fail-fast）。
    """
    # B1: 空消息直接返回提示
    if not user_message.strip():
        return "（消息为空，请输入有效内容）", history or []

    # 初始化消息历史
    messages: list[Message] = list(history) if history else []
    messages.append(Message(role="user", content=user_message))

    # 获取工具列表
    tools = get_tool_list()
    system_prompt = build_system_prompt(tools)
    api_tools = build_tools_for_api(tools) if tools else None

    iteration = 0
    use_stream = STREAM_MODE and on_stream_chunk is not None

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

                result = execute_tool(tool_name, tool_args)

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
            return response.content or "", messages

    # E4: iteration budget 耗尽
    last_assistant = ""
    for m in reversed(messages):
        if m.role == "assistant" and m.content:
            last_assistant = m.content
            break
    return last_assistant, messages


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
