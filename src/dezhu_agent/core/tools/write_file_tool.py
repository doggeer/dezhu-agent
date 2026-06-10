"""文件写入工具."""

from __future__ import annotations

from typing import Any

from dezhu_agent.models.tool import BaseTool, tool_error
from dezhu_agent.utils.path_utils import validate_path
from dezhu_agent.utils.tool_decorator import register_tool


@register_tool(
    name="write_file",
    description="Write content to a file, creating it if it does not exist "
    "or overwriting it if it does. Parent directories are created as needed.",
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the file to write.",
            },
            "content": {
                "type": "string",
                "description": "Content to write to the file.",
            },
        },
        "required": ["path", "content"],
    },
)
class WriteFileTool(BaseTool):
    """写入或覆盖文件."""

    def execute(self, **kwargs: Any) -> str:
        path_raw: str = kwargs.get("path", "")
        content: str = kwargs.get("content", "")

        try:
            path = validate_path(path_raw)
        except ValueError as e:
            return tool_error(str(e))

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        except Exception as e:
            return tool_error(f"Failed to write file: {e}")

        return f"File written successfully: {path}"
