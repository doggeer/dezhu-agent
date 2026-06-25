"""工具执行器：硬编码的工具定义与执行."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _read_file(path: str) -> str:
    """读取文件内容."""
    p = Path(path).resolve()
    if not p.exists():
        return f"Error: file not found: {path}"
    if not p.is_file():
        return f"Error: not a file: {path}"
    return p.read_text(encoding="utf-8", errors="replace")


def _write_file(path: str, content: str) -> str:
    """写入文件内容（覆盖写）."""
    p = Path(path).resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"Successfully wrote {len(content)} bytes to {path}"


# 工具定义：name → {name, description, parameters, fn}
TOOL_REGISTRY: dict[str, dict[str, Any]] = {
    "read_file": {
        "name": "read_file",
        "description": "读取指定文件的内容并返回。适用于查看源代码、配置文件、文档等。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "要读取的文件路径（相对或绝对路径）",
                },
            },
            "required": ["path"],
        },
        "fn": _read_file,
    },
    "write_file": {
        "name": "write_file",
        "description": "将内容写入指定文件（覆盖写）。适用于创建新文件或修改已有文件。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "要写入的文件路径（相对或绝对路径）",
                },
                "content": {
                    "type": "string",
                    "description": "要写入的文件内容",
                },
            },
            "required": ["path", "content"],
        },
        "fn": _write_file,
    },
}


def get_tool_list() -> list[dict[str, Any]]:
    """返回工具定义列表（不含 fn），用于拼装 prompt 和 API tools 参数."""
    return [
        {
            "name": t["name"],
            "description": t["description"],
            "parameters": t["parameters"],
        }
        for t in TOOL_REGISTRY.values()
    ]


def execute_tool(name: str, args: dict[str, Any]) -> str:
    """执行指定工具，返回执行结果（字符串）。

    对应验收标准 E2（工具不存在）和 E3（工具执行异常）。
    """
    tool = TOOL_REGISTRY.get(name)
    if tool is None:
        return f"Tool '{name}' not found"

    try:
        return tool["fn"](**args)
    except Exception as e:
        return f"Error executing tool '{name}': {e}"
