"""工具：精确替换文件中的文本."""

from __future__ import annotations

from pathlib import Path

from dezhu_agent.tools import tool


@tool(
    name="edit_file",
    description="在指定文件中查找唯一的一段文本并将其替换为新内容。"
    "适用于修改已有文件中的特定部分，而不影响文件其他内容。"
    "old_string 必须在文件中唯一出现，否则操作失败。",
)
def edit_file(path: str, old_string: str, new_string: str) -> str:
    """查找文件中的 old_string 并替换为 new_string.

    如果 old_string 在文件中出现多次或未出现，操作失败。
    """
    p = Path(path).resolve()
    if not p.exists():
        return f"Error: file not found: {path}"
    if not p.is_file():
        return f"Error: not a file: {path}"

    content = p.read_text(encoding="utf-8", errors="replace")

    count = content.count(old_string)
    if count == 0:
        return f"Error: old_string not found in {path}"
    if count > 1:
        return f"Error: old_string found {count} times in {path}, expected exactly 1 occurrence"

    new_content = content.replace(old_string, new_string, 1)
    p.write_text(new_content, encoding="utf-8")
    return f"Successfully replaced 1 occurrence in {path}"
