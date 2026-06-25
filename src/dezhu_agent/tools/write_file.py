"""工具：写入文件内容."""

from __future__ import annotations

from pathlib import Path

from dezhu_agent.tools import tool


@tool(
    name="write_file",
    description="将内容写入指定文件（覆盖写）。适用于创建新文件或修改已有文件。",
)
def write_file(path: str, content: str) -> str:
    """写入文件内容（覆盖写）."""
    p = Path(path).resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"Successfully wrote {len(content)} bytes to {path}"
