"""外部记忆提供者协议——预留接口，本期不实现."""

from __future__ import annotations

from typing import Protocol


class ExternalMemoryProvider(Protocol):
    """外部记忆提供者协议（如向量数据库）。

    内容不进 system prompt，通过 inject_into_user_message() 注入到 user message。
    """

    def retrieve(self, query: str) -> str:
        """根据查询检索相关记忆片段."""
        ...

    def inject_into_user_message(self, user_message: str) -> str:
        """将检索到的记忆注入到用户消息中."""
        ...


class NoopExternalProvider:
    """空实现——不注入任何外部记忆."""

    def retrieve(self, query: str) -> str:
        return ""

    def inject_into_user_message(self, user_message: str) -> str:
        return user_message
