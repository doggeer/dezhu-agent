"""Memory 文件存储：MEMORY.md + USER.md 的读写、文件锁、字符上限校验."""

from __future__ import annotations

import fcntl
import time
from contextlib import contextmanager
from pathlib import Path

from dezhu_agent.logging_config import get_logger

logger = get_logger(__name__)

# 字符上限
MEMORY_MAX_CHARS = 2200
USER_MAX_CHARS = 1375

# 文件锁超时（秒）
_LOCK_TIMEOUT = 2.0


class MemoryStoreError(Exception):
    """Memory 存储操作错误基类."""


class MemoryLockError(MemoryStoreError):
    """文件锁获取超时."""


class MemoryLimitError(MemoryStoreError):
    """写入超过字符上限."""


class MemoryIndexError(MemoryStoreError):
    """删除索引越界."""


class MemoryStore:
    """管理 MEMORY.md / USER.md 的读写。

    条目以 § 分隔。写操作使用 fcntl.flock 排他锁保证并发安全。
    """

    def __init__(self, base_dir: str | Path = ".dezhu-agent") -> None:
        self._dir = Path(base_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._memory_path = self._dir / "MEMORY.md"
        self._user_path = self._dir / "USER.md"

    # ---- 公共 API ----

    def read(self, target: str) -> str:
        """读取指定文件的完整内容。

        Args:
            target: "memory" 或 "user".
        Returns:
            文件内容字符串。文件不存在时返回 ""。
        """
        path = self._resolve(target)
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""

    def write(self, target: str, content: str) -> str:
        """追加条目到指定文件。

        Args:
            target: "memory" 或 "user".
            content: 要追加的条目文本（单条，不含 § 分隔符）。
        Returns:
            成功消息（含当前使用量和上限）。
        Raises:
            MemoryLimitError: 追加后超过字符上限。
            MemoryLockError: 获取文件锁超时。
        """
        path = self._resolve(target)
        limit = self._get_limit(target)
        entry = content.strip()

        if not entry:
            raise MemoryStoreError("条目内容不能为空")

        # 确保以 § 分隔
        new_entry = f"\n§ {entry}\n"

        with self._locked_file(path, mode="a+") as f:
            f.seek(0)
            current = f.read()
            new_total = len(current) + len(new_entry)

            if new_total > limit:
                raise MemoryLimitError(
                    f"写入后字符数 {new_total} 超过上限 {limit}"
                    f"（当前 {len(current)}，可用 {limit - len(current)}）"
                )

            f.write(new_entry)

        current_after = self._read_usage(path)
        logger.info(
            "Memory 写入: target=%s, 新增 %d 字符, 当前 %d/%d",
            target,
            len(new_entry),
            current_after,
            limit,
        )
        return f"已写入 {target} 记忆。当前 {current_after}/{limit} 字符。"

    def delete(self, target: str, index: int) -> str:
        """删除第 N 条条目（0-based）。

        Args:
            target: "memory" 或 "user".
            index: 条目索引（0-based）。
        Returns:
            被删除的条目内容（文本）。
        Raises:
            MemoryIndexError: 索引越界。
            MemoryLockError: 获取文件锁超时。
        """
        path = self._resolve(target)

        # 读-改-写全过程在锁内完成，防止 TOCTOU 竞态
        with self._locked_file(path, mode="r+") as f:
            text = f.read()
            entries = self._parse_entries(text)

            if index < 0 or index >= len(entries):
                raise MemoryIndexError(f"索引 {index} 越界，有效范围 0-{len(entries) - 1}")

            removed = entries[index]

            # 截断并重写（不含被删除的条目）
            f.seek(0)
            f.truncate()
            for i, e in enumerate(entries):
                if i != index:
                    f.write(f"§ {e}\n")

        logger.info(
            "Memory 删除: target=%s, index=%d, 条目='%s'",
            target,
            index,
            removed[:80],
        )
        return removed

    def get_usage(self, target: str) -> tuple[int, int]:
        """返回 (当前字符数, 上限)。"""
        path = self._resolve(target)
        return self._read_usage(path), self._get_limit(target)

    def get_entry_count(self, target: str) -> int:
        """返回当前条目数。"""
        path = self._resolve(target)
        return len(self._get_entries(path))

    # ---- 内部方法 ----

    def _resolve(self, target: str) -> Path:
        if target == "memory":
            return self._memory_path
        if target == "user":
            return self._user_path
        raise MemoryStoreError(f"无效的 target: {target}，有效值: memory, user")

    @staticmethod
    def _get_limit(target: str) -> int:
        if target == "memory":
            return MEMORY_MAX_CHARS
        if target == "user":
            return USER_MAX_CHARS
        raise MemoryStoreError(f"无效的 target: {target}")

    @staticmethod
    def _read_usage(path: Path) -> int:
        try:
            return len(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return 0

    @staticmethod
    def _get_entries(path: Path) -> list[str]:
        """从文件读取所有条目（去掉 § 前缀和空行）。"""
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        return MemoryStore._parse_entries(text)

    @staticmethod
    def _parse_entries(text: str) -> list[str]:
        """从文本解析条目列表（去掉 § 前缀和空行）。"""
        entries: list[str] = []
        for line in text.strip().split("\n"):
            line = line.strip()
            if line.startswith("§ "):
                entries.append(line[2:])
            elif line == "§" or line.startswith("§"):
                entries.append(line[1:].strip())
        return entries

    @contextmanager
    def _locked_file(self, path: Path, mode: str):
        """获取排他锁的文件句柄，用作上下文管理器。

        使用 fcntl.flock 排他锁，超时 _LOCK_TIMEOUT 秒。
        """
        # 确保文件存在
        if "r" not in mode and not path.exists():
            path.touch()

        f = open(path, mode, encoding="utf-8")
        deadline = time.monotonic() + _LOCK_TIMEOUT

        while True:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except (BlockingIOError, OSError):
                if time.monotonic() >= deadline:
                    f.close()
                    raise MemoryLockError(f"无法获取 {path.name} 的文件锁（超时 {_LOCK_TIMEOUT}s）")
                time.sleep(0.05)

        try:
            yield f
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            f.close()
