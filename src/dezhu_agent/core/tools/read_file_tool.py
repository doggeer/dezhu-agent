"""文件读取工具."""

from __future__ import annotations

from typing import Any

from dezhu_agent.config import get_config
from dezhu_agent.models.tool import BaseTool, tool_error
from dezhu_agent.utils.path_utils import validate_path
from dezhu_agent.utils.tool_decorator import register_tool


@register_tool(
    name="read_file",
    description="Read the content of a file. Returns the file content as a string.",
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the file to read.",
            },
        },
        "required": ["path"],
    },
)
class ReadFileTool(BaseTool):
    """读取文件内容."""

    def execute(self, **kwargs: Any) -> str:
        path_raw: str = kwargs.get("path", "")
        try:
            path = validate_path(path_raw)
        except ValueError as e:
            return tool_error(str(e))

        if not path.exists():
            return tool_error(f"File not found: {path}")
        if not path.is_file():
            return tool_error(f"Not a file: {path}")

        try:
            content = path.read_text(encoding="utf-8")
        except Exception as e:
            return tool_error(f"Failed to read file: {e}")

        max_chars = get_config().PROMPT_MAX_FILE_CHARS
        if len(content) > max_chars:
            content = content[:max_chars] + f"\n... [truncated at {max_chars} chars]"

        return content
