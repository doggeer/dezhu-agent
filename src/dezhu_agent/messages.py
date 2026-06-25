"""消息模型：内部消息与 API 消息的双份管理."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Message:
    """内部消息——包含完整信息，包括模型内部字段."""

    role: str  # 'system' | 'user' | 'assistant' | 'tool'
    content: str | None = None
    tool_calls: list[dict[str, Any]] | None = None  # 仅 assistant
    tool_call_id: str | None = None  # 仅 tool
    name: str | None = None  # 仅 tool

    # --- DeepSeek 思考模式 ---
    reasoning_content: str | None = None  # 思维链内容，仅 assistant

    # --- DeepSeek 硬盘缓存统计 ---
    prompt_cache_hit_tokens: int = 0
    prompt_cache_miss_tokens: int = 0

    # --- 内部字段（API 调用前必须清除） ---
    reasoning: str | None = None
    _internal: dict[str, Any] = field(default_factory=dict)

    def to_api_dict(self) -> dict[str, Any]:
        """清洗内部字段，返回仅含 OpenAI API 认可字段的 dict."""
        d: dict[str, Any] = {"role": self.role}

        if self.content is not None:
            d["content"] = self.content

        # DeepSeek 思考模式：有 tool_calls 时必须回传 reasoning_content
        if self.reasoning_content is not None and self.tool_calls is not None:
            d["reasoning_content"] = self.reasoning_content

        if self.tool_calls is not None:
            # 深度复制 tool_calls，确保不携带内部字段
            cleaned_calls = []
            for tc in self.tool_calls:
                fn = tc["function"]
                clean_tc = {
                    "id": tc["id"],
                    "type": tc["type"],
                    "function": {"name": fn["name"], "arguments": fn["arguments"]},
                }
                cleaned_calls.append(clean_tc)
            d["tool_calls"] = cleaned_calls

        if self.tool_call_id is not None:
            d["tool_call_id"] = self.tool_call_id

        if self.name is not None:
            d["name"] = self.name

        return d


def messages_to_api_messages(messages: list[Message]) -> list[dict[str, Any]]:
    """将内部消息列表转换为 API 可接受的 dict 列表."""
    return [m.to_api_dict() for m in messages]
