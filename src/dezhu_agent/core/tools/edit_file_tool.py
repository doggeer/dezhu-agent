"""文件编辑工具 —— search-and-replace 模式."""

from __future__ import annotations

from typing import Any

from dezhu_agent.models.tool import BaseTool, tool_error
from dezhu_agent.utils.path_utils import validate_path
from dezhu_agent.utils.tool_decorator import register_tool


@register_tool(
    name="edit_file",
    description="Edit an existing file by replacing the first occurrence of old_str with new_str. "
    "The old_str must exactly match the file content, including whitespace and indentation.",
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the file to edit.",
            },
            "old_str": {
                "type": "string",
                "description": "The exact text to find and replace.",
            },
            "new_str": {
                "type": "string",
                "description": "The replacement text.",
            },
        },
        "required": ["path", "old_str", "new_str"],
    },
)
class EditFileTool(BaseTool):
    """编辑已有文件 —— 首次匹配替换."""

    def execute(self, **kwargs: Any) -> str:
        path_raw: str = kwargs.get("path", "")
        old_str: str = kwargs.get("old_str", "")
        new_str: str = kwargs.get("new_str", "")

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

        if old_str not in content:
            return tool_error("old_str not found in file. Ensure exact match including whitespace and indentation.")

        new_content = content.replace(old_str, new_str, 1)

        try:
            path.write_text(new_content, encoding="utf-8")
        except Exception as e:
            return tool_error(f"Failed to write file: {e}")

        return f"File edited successfully: {path}"
