"""System prompt 组装."""

from __future__ import annotations

from typing import Any

from dezhu_agent.prompt_assembler import assemble_system_prompt


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
    """构建完整的 system prompt，从多来源组装（SOUL.md + AGENTS.md + 技能预留）.

    工具定义不写入 system prompt 文本，通过 build_tools_for_api() 单独传递。
    """
    return assemble_system_prompt(tools)


def build_tools_for_api(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """将内部工具列表转换为 OpenAI API 的 tools 参数."""
    return [_tool_to_openai_tool(t) for t in tools]
