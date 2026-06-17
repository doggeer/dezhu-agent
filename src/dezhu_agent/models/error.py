"""API 调用结果与错误分类模型."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel


class ErrorCategory(StrEnum):
    """API 错误的分类, 每种分类对应不同的恢复策略."""

    RETRYABLE = "retryable"
    """临时故障: 429 限流 / 503 过载 / 超时 / 连接错误 — 退避重试."""

    CONTEXT_OVERFLOW = "context_overflow"
    """疑似上下文溢出: 400 Bad Request — 压缩后重试一次."""

    TRUNCATED = "truncated"
    """输出被截断: finish_reason=length, 续写已达最大次数仍被截断."""

    AUTH_FAILURE = "auth_failure"
    """认证失败: 401 — 尝试故障转移或放弃."""

    MODEL_NOT_FOUND = "model_not_found"
    """模型不存在或被下线: 404 — 尝试故障转移或放弃."""

    THINKING_BUDGET = "thinking_budget"
    """所有输出 token 被 reasoning 消耗, 回复 token 为零."""

    FATAL = "fatal"
    """不可恢复: 403 / 其他未知错误."""


class ApiCallResult(BaseModel):
    """封装一次 API 调用的结果, 供 agent 循环判断下一步动作.

    Attributes:
        success: 调用是否成功 (包括续写成功).
        response: 成功时的 OpenAI chat completion 对象.
        category: 失败时的错误分类.
        error_message: 失败时的人类可读错误描述.
        finish_reason: API 返回的 finish_reason 字符串.
        needs_continuation: 是否需要续写 (finish_reason=length).
    """

    success: bool
    response: Any | None = None
    category: ErrorCategory | None = None
    error_message: str = ""
    finish_reason: str = ""
    needs_continuation: bool = False
