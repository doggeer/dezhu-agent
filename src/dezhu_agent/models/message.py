"""对话消息相关模型."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class Message(BaseModel):
    """单条对话消息, 对应 OpenAI chat message 格式.

    Attributes:
        role: "system" | "user" | "assistant" | "tool"
        content: 消息文本内容
        tool_calls: assistant 消息中的工具调用列表 (OpenAI 格式)
        tool_call_id: tool 消息对应的调用 ID
    """

    role: str
    content: str = ""
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """转为 OpenAI API 兼容的 dict."""
        d: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls is not None:
            d["tool_calls"] = self.tool_calls
        if self.tool_call_id is not None:
            d["tool_call_id"] = self.tool_call_id
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Message:
        """从 dict 构造."""
        return cls(
            role=d.get("role", ""),
            content=d.get("content", ""),
            tool_calls=d.get("tool_calls"),
            tool_call_id=d.get("tool_call_id"),
        )

    @classmethod
    def assistant(
        cls,
        content: str = "",
        tool_calls: list[dict[str, Any]] | None = None,
    ) -> Message:
        """便捷构造 assistant 消息."""
        return cls(role="assistant", content=content, tool_calls=tool_calls)

    @classmethod
    def user(cls, content: str) -> Message:
        """便捷构造 user 消息."""
        return cls(role="user", content=content)

    @classmethod
    def tool(cls, tool_call_id: str, content: str) -> Message:
        """便捷构造 tool 消息."""
        return cls(role="tool", content=content, tool_call_id=tool_call_id)


class ConversationResult(BaseModel):
    """run_conversation 返回值."""

    final_response: str
    messages: list[dict[str, Any]]
    compression_triggered: bool = False
