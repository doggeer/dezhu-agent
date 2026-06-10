"""会话持久化服务 —— 基于 SQLite + WAL 模式."""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from dezhu_agent.config import get_config
from dezhu_agent.models.session import SessionInfo


class SessionStore:
    """会话持久化服务, 封装 SQLite 读写操作."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @contextmanager
    def _get_conn(self) -> Generator[sqlite3.Connection, None, None]:
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_db(self) -> None:
        with self._get_conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    source TEXT NOT NULL DEFAULT 'cli',
                    model TEXT NOT NULL,
                    createtime TEXT NOT NULL,
                    system_prompt TEXT NOT NULL DEFAULT '',
                    parent_session_id TEXT
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL REFERENCES sessions(id),
                    role TEXT NOT NULL,
                    content TEXT,
                    tool_calls TEXT,
                    tool_call_id TEXT,
                    createtime TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
                """
            )
            # 兼容旧表迁移: 如果 system_prompt 列不存在则添加
            cols = {row[1] for row in conn.execute("PRAGMA table_info('sessions')").fetchall()}
            if "system_prompt" not in cols:
                conn.execute("ALTER TABLE sessions ADD COLUMN system_prompt TEXT NOT NULL DEFAULT ''")
            if "parent_session_id" not in cols:
                conn.execute("ALTER TABLE sessions ADD COLUMN parent_session_id TEXT")

    def create_session(self, source: str, model: str, parent_session_id: str | None = None) -> str:
        session_id = uuid.uuid4().hex
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO sessions (id, source, model, createtime, parent_session_id) VALUES (?, ?, ?, ?, ?)",
                (session_id, source, model, now, parent_session_id),
            )
        return session_id

    def list_sessions(self, limit: int = 10) -> list[SessionInfo]:
        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT s.id, s.source, s.model, s.createtime, s.parent_session_id, COUNT(m.id) as message_count
                FROM sessions s
                LEFT JOIN messages m ON s.id = m.session_id
                GROUP BY s.id
                ORDER BY s.createtime DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            SessionInfo(
                id=row["id"],
                source=row["source"],
                model=row["model"],
                createtime=row["createtime"],
                message_count=row["message_count"],
                parent_session_id=row["parent_session_id"],
            )
            for row in rows
        ]

    def store_system_prompt(self, session_id: str, system_prompt: str) -> None:
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE sessions SET system_prompt = ? WHERE id = ?",
                (system_prompt, session_id),
            )

    def get_system_prompt(self, session_id: str) -> str | None:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT system_prompt FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        prompt = row["system_prompt"]
        return prompt if prompt else None

    def load_messages(self, session_id: str) -> list[dict[str, Any]]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT role, content, tool_calls, tool_call_id FROM messages WHERE session_id = ? ORDER BY id",
                (session_id,),
            ).fetchall()
        messages: list[dict[str, Any]] = []
        for row in rows:
            msg: dict[str, Any] = {"role": row["role"], "content": row["content"]}
            if row["tool_calls"]:
                msg["tool_calls"] = json.loads(row["tool_calls"])
            if row["tool_call_id"]:
                msg["tool_call_id"] = row["tool_call_id"]
            messages.append(msg)
        return messages

    def store_message(self, session_id: str, msg: dict[str, Any]) -> int:
        """插入单条消息并返回自增 ID."""
        with self._get_conn() as conn:
            cursor = conn.execute(
                "INSERT INTO messages (session_id, role, content, tool_calls, tool_call_id, createtime) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    msg["role"],
                    msg.get("content"),
                    json.dumps(msg["tool_calls"]) if msg.get("tool_calls") else None,
                    msg.get("tool_call_id"),
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
            row_id = cursor.lastrowid
            return row_id if row_id is not None else -1

    def append_messages(self, session_id: str, messages: list[dict[str, Any]]) -> None:
        rows = [
            (
                session_id,
                msg["role"],
                msg.get("content"),
                json.dumps(msg["tool_calls"]) if msg.get("tool_calls") else None,
                msg.get("tool_call_id"),
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            )
            for msg in messages
        ]
        with self._get_conn() as conn:
            conn.executemany(
                "INSERT INTO messages (session_id, role, content, tool_calls, tool_call_id, createtime) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                rows,
            )


@lru_cache
def get_session_store() -> SessionStore:
    db_path = get_config().DB_PATH
    p = Path(db_path)
    if not p.is_absolute():
        project_root = Path(__file__).resolve().parents[3]
        p = project_root / p
    return SessionStore(str(p))
