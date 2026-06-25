"""工具：读取文件内容."""

from __future__ import annotations

from pathlib import Path

from dezhu_agent.tools import tool


@tool(
    name="read_file",
    description="读取指定文件的内容并返回。适用于查看源代码、配置文件、文档等。",
)
def read_file(path: str) -> str:
    """读取文件内容."""
    p = Path(path).resolve()
    if not p.exists():
        return f"Error: file not found: {path}"
    if not p.is_file():
        return f"Error: not a file: {path}"
    return p.read_text(encoding="utf-8", errors="replace")
