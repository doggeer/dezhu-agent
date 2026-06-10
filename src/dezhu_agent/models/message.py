"""对话消息相关模型."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class ConversationResult(BaseModel):
    """run_conversation 返回值."""

    final_response: str
    messages: list[dict[str, Any]]
    compression_triggered: bool = False
