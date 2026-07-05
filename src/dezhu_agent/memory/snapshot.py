"""冻结快照：会话开始时读取 MemoryStore 生成不可变快照，注入 system prompt."""

from __future__ import annotations

from dataclasses import dataclass

from dezhu_agent.memory.store import MemoryStore


@dataclass
class MemorySnapshot:
    """会话开始时的 memory 冻结快照。

    快照生成后不再改变，保护 prompt cache。
    """

    memory_text: str
    user_text: str

    @classmethod
    def from_store(cls, store: MemoryStore) -> MemorySnapshot:
        """从 MemoryStore 读取两个文件生成快照."""
        return cls(
            memory_text=store.read("memory"),
            user_text=store.read("user"),
        )

    def render(self) -> str:
        """输出注入 system prompt 的文本。

        格式：
            ## MEMORY (your personal notes)
            <MEMORY.md 内容>

            ## USER PROFILE (who the user is)
            <USER.md 内容>

        若对应文件为空则跳过对应的节；全空返回 ""。
        """
        parts: list[str] = []

        if self.memory_text.strip():
            parts.append("## MEMORY (your personal notes)")
            parts.append(self.memory_text.strip())

        if self.user_text.strip():
            parts.append("## USER PROFILE (who the user is)")
            parts.append(self.user_text.strip())

        return "\n\n".join(parts)
