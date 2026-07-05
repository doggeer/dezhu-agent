"""Memory 子系统：跨会话记忆管理."""

from dezhu_agent.memory.store import (
    MEMORY_MAX_CHARS,
    USER_MAX_CHARS,
    MemoryLimitError,
    MemoryLockError,
    MemoryStore,
    MemoryStoreError,
    MemoryIndexError,
)
from dezhu_agent.memory.snapshot import MemorySnapshot
from dezhu_agent.memory.external import ExternalMemoryProvider, NoopExternalProvider


class MemorySource:
    """实现 PromptSource 协议，将 MemorySnapshot 注入 system prompt."""

    name = "MEMORY"

    def __init__(self, snapshot: MemorySnapshot) -> None:
        self._snapshot = snapshot

    def render(self) -> str:
        return self._snapshot.render()


__all__ = [
    "MemoryStore",
    "MemoryStoreError",
    "MemoryLockError",
    "MemoryLimitError",
    "MemoryIndexError",
    "MemorySnapshot",
    "MemorySource",
    "ExternalMemoryProvider",
    "NoopExternalProvider",
    "MEMORY_MAX_CHARS",
    "USER_MAX_CHARS",
]
