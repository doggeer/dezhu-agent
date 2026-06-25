"""System prompt 组装."""

from __future__ import annotations

from typing import Any

SYSTEM_PROMPT_TEMPLATE = """你是 DeZhu Agent，一个通过工具与系统交互的 AI 助手。

## 工具使用规则
1. 当你需要执行操作时，使用提供的工具。
2. 工具调用后你会收到执行结果，请基于结果继续推理。
3. 如果工具返回错误，分析错误原因并尝试修正参数后重试。
4. 任务完成后，用自然语言回复用户总结结果。"""


def _tool_to_openai_tool(tool: dict[str, Any]) -> dict[str, Any]:
    """将内部工具定义格式转换为 OpenAI API 的 tools 参数格式."""
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool["description"],
            "parameters": tool["parameters"],
        },
    }


def build_system_prompt(tools: list[dict[str, Any]] | None = None) -> str:
    """构建完整的 system prompt，包含人设和工具定义."""
    prompt = SYSTEM_PROMPT_TEMPLATE

    if tools:
        prompt += "\n\n## 可用工具\n"
        for tool in tools:
            prompt += f"\n### {tool['name']}\n{tool['description']}\n"
            prompt += f"参数：{tool['parameters']}\n"

    return prompt


def build_tools_for_api(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """将内部工具列表转换为 OpenAI API 的 tools 参数."""
    return [_tool_to_openai_tool(t) for t in tools]
