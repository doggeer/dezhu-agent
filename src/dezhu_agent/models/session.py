"""会话持久化相关模型."""

from __future__ import annotations

from pydantic import BaseModel


class SessionInfo(BaseModel):
    """会话元信息, 用于列表展示."""

    id: str
    source: str
    model: str
    createtime: str
    message_count: int = 0
    parent_session_id: str | None = None
