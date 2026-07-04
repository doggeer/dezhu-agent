"""消息持久化存储：StorageBackend 抽象 + SQLiteBackend 实现."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path

from dezhu_agent.config import DEZHU_DB_PATH
from dezhu_agent.messages import Message

# SQLITE_BUSY 重试参数
_MAX_RETRIES = 3
_RETRY_DELAYS = [0.01, 0.05, 0.2]  # 秒


class StorageBackend(ABC):
    """消息存储抽象接口。"""

    @abstractmethod
    def create_session(self, parent_session_id: str = "") -> str:
        """创建新会话，返回 session_id (UUID4 字符串)。

        Args:
            parent_session_id: 父会话 ID（压缩分裂时使用），默认空字符串表示无父会话。
        """
        ...

    @abstractmethod
    def save_messages(self, session_id: str, messages: list[Message]) -> None:
        """保存消息列表到指定会话。新消息追加，已有消息不重复。"""
        ...

    @abstractmethod
    def load_messages(self, session_id: str) -> list[Message]:
        """加载指定会话的全部消息，按 sequence 排序。"""
        ...

    @abstractmethod
    def list_sessions(self, limit: int = 10) -> list[dict]:
        """列出最近 N 个会话。返回 dict 列表，每项含 session_id、created_at、message_count、ended_at。"""
        ...

    @abstractmethod
    def search_messages(self, query: str) -> list[dict]:
        """全文搜索消息内容。返回匹配的 session_id、created_at、匹配行文本、前后上下文。"""
        ...

    @abstractmethod
    def save_system_prompt(self, session_id: str, system_prompt: str) -> None:
        """保存系统提示词到指定会话。"""
        ...

    @abstractmethod
    def load_system_prompt(self, session_id: str) -> str:
        """加载指定会话的系统提示词，不存在则返回空字符串。"""
        ...

    @abstractmethod
    def update_session_meta(
        self, session_id: str, ended_at: str = "", message_count: int = 0
    ) -> None:
        """更新会话元数据（结束时间、消息总数）。"""
        ...

    @abstractmethod
    def list_session_chain(self, session_id: str, max_depth: int = 10) -> list[dict]:
        """查询 session 链（沿 parent_session_id 递归）。

        返回从给定 session_id 向上追溯到根 session 的列表，按时间升序。
        max_depth 防止循环引用导致无限递归。
        """
        ...


class SQLiteBackend(StorageBackend):
    """SQLite 存储实现 — WAL 模式 + 写入重试 + FTS5 全文索引。"""

    def __init__(self, db_path: str = "") -> None:
        """初始化数据库连接并自动创建 schema。

        Args:
            db_path: 数据库文件路径。为空时使用 config.DEZHU_DB_PATH。
        """
        self._db_path = db_path or DEZHU_DB_PATH
        self._conn: sqlite3.Connection | None = None
        self._ensure_db()

    # ---- 内部 ----

    def _ensure_db(self) -> None:
        """确保数据库文件所在目录存在，创建连接并初始化 schema。"""
        db_dir = Path(self._db_path).parent
        db_dir.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(str(self._db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row

        conn.executescript(_SCHEMA_SQL)
        conn.commit()

        # ---- 向后兼容迁移 ----
        try:
            conn.execute(
                "ALTER TABLE sessions ADD COLUMN system_prompt TEXT NOT NULL DEFAULT ''"
            )
            conn.commit()
        except sqlite3.OperationalError as e:
            # 仅列已存在时跳过；其他错误（磁盘 I/O、锁等）向上传播
            if "duplicate column name" not in str(e).lower():
                raise

        # ---- 向后兼容迁移：parent_session_id ----
        try:
            conn.execute(
                "ALTER TABLE sessions ADD COLUMN parent_session_id TEXT"
            )
            conn.commit()
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e).lower():
                raise

        self._conn = conn

    @property
    def conn(self) -> sqlite3.Connection:
        """获取当前数据库连接（lazy init）。"""
        if self._conn is None:
            self._ensure_db()
        assert self._conn is not None
        return self._conn

    def _execute_with_retry(self, sql: str, params: tuple | list = ()) -> sqlite3.Cursor:
        """执行 SQL，SQLITE_BUSY 时自动重试最多 3 次。"""
        last_error: Exception | None = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                return self.conn.execute(sql, params)
            except sqlite3.OperationalError as e:
                if "database is locked" in str(e).lower() and attempt < _MAX_RETRIES:
                    last_error = e
                    time.sleep(_RETRY_DELAYS[attempt])
                    continue
                raise
        raise last_error  # type: ignore[misc]

    # ---- 公开 API ----

    def create_session(self, parent_session_id: str = "") -> str:
        session_id = uuid.uuid4().hex
        now = _now_iso()
        parent = parent_session_id if parent_session_id else None
        self._execute_with_retry(
            "INSERT INTO sessions (session_id, created_at, parent_session_id) VALUES (?, ?, ?)",
            (session_id, now, parent),
        )
        self.conn.commit()
        return session_id

    def save_messages(self, session_id: str, messages: list[Message]) -> None:
        if not messages:
            return

        # 获取当前最大 sequence
        cur = self._execute_with_retry(
            "SELECT COALESCE(MAX(sequence), -1) AS max_seq FROM messages WHERE session_id = ?",
            (session_id,),
        )
        row = cur.fetchone()
        max_seq = row["max_seq"] if row else -1

        now = _now_iso()
        for i, msg in enumerate(messages):
            seq = max_seq + 1 + i
            row_data = _message_to_row(msg, session_id, seq, now)
            self._execute_with_retry(
                """INSERT INTO messages
                   (session_id, sequence, role, content, tool_calls, tool_call_id,
                    name, reasoning_content, prompt_cache_hit_tokens,
                    prompt_cache_miss_tokens, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    row_data["session_id"],
                    row_data["sequence"],
                    row_data["role"],
                    row_data["content"],
                    row_data["tool_calls"],
                    row_data["tool_call_id"],
                    row_data["name"],
                    row_data["reasoning_content"],
                    row_data["prompt_cache_hit_tokens"],
                    row_data["prompt_cache_miss_tokens"],
                    row_data["created_at"],
                ),
            )

        self.conn.commit()

    def load_messages(self, session_id: str) -> list[Message]:
        cur = self._execute_with_retry(
            "SELECT * FROM messages WHERE session_id = ? ORDER BY sequence",
            (session_id,),
        )
        return [_row_to_message(dict(row)) for row in cur.fetchall()]

    def list_sessions(self, limit: int = 10) -> list[dict]:
        cur = self._execute_with_retry(
            """SELECT session_id, created_at, parent_session_id, message_count, ended_at
               FROM sessions
               ORDER BY created_at DESC
               LIMIT ?""",
            (limit,),
        )
        return [dict(row) for row in cur.fetchall()]

    def search_messages(self, query: str) -> list[dict]:
        # 混合搜索策略：
        # 1. FTS5 MATCH 用于英文/空格分词语言（精确匹配）
        # 2. LIKE 用于中文等无空格分词语言（子串匹配）
        # 两者结果合并去重，FTS5 无法处理时仅用 LIKE
        safe_query = _escape_fts5(query)
        like_pattern = f"%{query}%"

        result_map: dict[tuple, dict] = {}  # (session_id, sequence) -> result, 用于去重

        # 尝试 FTS5 搜索
        try:
            cur = self._execute_with_retry(
                """SELECT m.session_id, s.created_at, m.content,
                          m.sequence, m.id
                   FROM messages_fts fts
                   JOIN messages m ON fts.rowid = m.id
                   JOIN sessions s ON m.session_id = s.session_id
                   WHERE messages_fts MATCH ?
                   ORDER BY s.created_at DESC, m.sequence
                   LIMIT 100""",
                (safe_query,),
            )
            for row in cur.fetchall():
                d = dict(row)
                key = (d["session_id"], d["sequence"])
                if key not in result_map:
                    d["context_before"] = ""
                    d["context_after"] = ""
                    result_map[key] = d
        except sqlite3.OperationalError:
            pass  # FTS5 查询语法错误时降级到 LIKE

        # LIKE 搜索（兜底 + 中文支持）
        cur = self._execute_with_retry(
            """SELECT m.session_id, s.created_at, m.content,
                      m.sequence
               FROM messages m
               JOIN sessions s ON m.session_id = s.session_id
               WHERE m.content LIKE ?
               ORDER BY s.created_at DESC, m.sequence
               LIMIT 100""",
            (like_pattern,),
        )
        for row in cur.fetchall():
            d = dict(row)
            key = (d["session_id"], d["sequence"])
            if key not in result_map:
                d["context_before"] = ""
                d["context_after"] = ""
                result_map[key] = d

        # 按 session + sequence 排序
        results = sorted(result_map.values(), key=lambda r: (r.get("session_id", ""), r.get("sequence", 0)))

        # 填充上下文
        results = _enrich_context(self.conn, results)
        return results

    def update_session_meta(
        self, session_id: str, ended_at: str = "", message_count: int = 0
    ) -> None:
        updates: list[str] = []
        params: list[str | int] = []

        if ended_at:
            updates.append("ended_at = ?")
            params.append(ended_at)
        if message_count > 0:
            updates.append("message_count = ?")
            params.append(message_count)
        if not updates:
            return

        params.append(session_id)
        self._execute_with_retry(
            f"UPDATE sessions SET {', '.join(updates)} WHERE session_id = ?",
            tuple(params),
        )
        self.conn.commit()

    def save_system_prompt(self, session_id: str, system_prompt: str) -> None:
        """保存系统提示词到指定会话。"""
        self._execute_with_retry(
            "UPDATE sessions SET system_prompt = ? WHERE session_id = ?",
            (system_prompt, session_id),
        )
        self.conn.commit()

    def load_system_prompt(self, session_id: str) -> str:
        """加载指定会话的系统提示词，不存在则返回空字符串。"""
        cur = self._execute_with_retry(
            "SELECT system_prompt FROM sessions WHERE session_id = ?",
            (session_id,),
        )
        row = cur.fetchone()
        if row is None:
            return ""
        return row["system_prompt"] or ""

    def list_session_chain(self, session_id: str, max_depth: int = 10) -> list[dict]:
        """查询 session 链（沿 parent_session_id 递归）。"""
        chain: list[dict] = []
        current = session_id
        for _ in range(max_depth):
            cur = self._execute_with_retry(
                "SELECT session_id, created_at, parent_session_id FROM sessions WHERE session_id = ?",
                (current,),
            )
            row = cur.fetchone()
            if row is None:
                break
            d = dict(row)
            chain.append(d)
            current = d.get("parent_session_id") or ""
            if not current:
                break
        # 反转，使链从根 session 开始
        chain.reverse()
        return chain


# ---- 消息序列化 ----


def _message_to_row(msg: Message, session_id: str, sequence: int, created_at: str) -> dict:
    """将 Message 转换为数据库行 dict（排除内部字段）。"""
    return {
        "session_id": session_id,
        "sequence": sequence,
        "role": msg.role,
        "content": msg.content,
        "tool_calls": json.dumps(msg.tool_calls, ensure_ascii=False) if msg.tool_calls else None,
        "tool_call_id": msg.tool_call_id,
        "name": msg.name,
        "reasoning_content": msg.reasoning_content,
        "prompt_cache_hit_tokens": msg.prompt_cache_hit_tokens,
        "prompt_cache_miss_tokens": msg.prompt_cache_miss_tokens,
        "created_at": created_at,
    }


def _row_to_message(row: dict) -> Message:
    """将数据库行 dict 还原为 Message 对象。"""
    tool_calls = None
    if row.get("tool_calls"):
        try:
            tool_calls = json.loads(row["tool_calls"])
        except (json.JSONDecodeError, TypeError):
            tool_calls = None

    return Message(
        role=row["role"],
        content=row.get("content"),
        tool_calls=tool_calls,
        tool_call_id=row.get("tool_call_id"),
        name=row.get("name"),
        reasoning_content=row.get("reasoning_content"),
        prompt_cache_hit_tokens=row.get("prompt_cache_hit_tokens", 0),
        prompt_cache_miss_tokens=row.get("prompt_cache_miss_tokens", 0),
    )


# ---- 辅助函数 ----


def _now_iso() -> str:
    """返回当前时间的 ISO 8601 字符串（UTC）。"""
    return datetime.now(timezone.utc).isoformat()


def _escape_fts5(query: str) -> str:
    """转义 FTS5 特殊字符，返回安全查询字符串。"""
    # FTS5 语法特殊字符：* " ( ) + - 以及列名限定符 :
    # 简单策略：用双引号包裹整个查询
    # 如果查询本身包含双引号，将其翻倍转义
    escaped = query.replace('"', '""')
    return f'"{escaped}"'


def _enrich_context(conn: sqlite3.Connection, results: list[dict]) -> list[dict]:
    """为搜索结果添加上下文（前后各 1 条消息）。"""
    for r in results:
        seq = r.get("sequence", 0)
        sid = r.get("session_id", "")

        # 前一行
        if seq > 0:
            cur = conn.execute(
                "SELECT content FROM messages WHERE session_id = ? AND sequence = ?",
                (sid, seq - 1),
            )
            before = cur.fetchone()
            if before:
                r["context_before"] = before[0] or ""

        # 后一行
        cur = conn.execute(
            "SELECT content FROM messages WHERE session_id = ? AND sequence = ?",
            (sid, seq + 1),
        )
        after = cur.fetchone()
        if after:
            r["context_after"] = after[0] or ""

    return results


# ---- Schema DDL ----


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id   TEXT PRIMARY KEY,
    created_at   TEXT NOT NULL,
    parent_session_id TEXT,
    ended_at     TEXT,
    message_count INTEGER NOT NULL DEFAULT 0,
    system_prompt TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS messages (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id            TEXT NOT NULL REFERENCES sessions(session_id),
    sequence              INTEGER NOT NULL,
    role                  TEXT NOT NULL,
    content               TEXT,
    tool_calls            TEXT,
    tool_call_id          TEXT,
    name                  TEXT,
    reasoning_content     TEXT,
    prompt_cache_hit_tokens  INTEGER NOT NULL DEFAULT 0,
    prompt_cache_miss_tokens INTEGER NOT NULL DEFAULT 0,
    created_at            TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_session
    ON messages(session_id, sequence);

-- FTS5 全文索引（content 列）
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content,
    content=messages,
    content_rowid=id
);

-- FTS5 增量同步触发器
CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;

CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.id, old.content);
END;

CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.id, old.content);
    INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;
"""
