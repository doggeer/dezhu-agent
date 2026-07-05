"""Error classification for OpenAI SDK exceptions.

Extracts a unified failure reason from APIStatusError, APITimeoutError,
and APIConnectionError so callers can implement retry / recovery logic.
"""

from __future__ import annotations

from enum import Enum

from openai import APIConnectionError, APIStatusError, APITimeoutError


class ErrorCategory(str, Enum):
    """Unified error categories for LLM API failures."""

    length = "length"  # 输出截断（not classified from exceptions）
    context_overflow = "context_overflow"
    rate_limit = "rate_limit"
    timeout = "timeout"
    auth = "auth"
    billing = "billing"
    model_not_found = "model_not_found"
    server_error = "server_error"


def classify_error(exception: Exception) -> ErrorCategory:
    """Classify an OpenAI SDK exception into a unified ErrorCategory.

    Args:
        exception: The exception raised by the OpenAI SDK.

    Returns:
        An ErrorCategory enum value.

    Raises:
        The original exception if it cannot be classified (unknown error).
    """
    if isinstance(exception, APIStatusError):
        status = exception.status_code
        body: dict = exception.body if isinstance(exception.body, dict) else {}

        # 5xx -> server error
        if 500 <= status < 600:
            return ErrorCategory.server_error

        # 401 -> auth
        if status == 401:
            return ErrorCategory.auth

        # 404 -> model_not_found
        if status == 404:
            return ErrorCategory.model_not_found

        # 429 -> rate_limit
        if status == 429:
            return ErrorCategory.rate_limit

        # 400 — inspect body for billing vs context_overflow
        if status == 400:
            # Check body for billing-related codes / messages
            error_info = body.get("error", {})
            code = error_info.get("code", "")
            message = error_info.get("message", "")

            # Billing: code or message mentions quota / billing
            combined = f"{code} {message}".lower()
            if "quota" in combined or "insufficient_quota" in combined or "billing" in combined:
                return ErrorCategory.billing

            # Context overflow: body mentions context / length / token
            if any(kw in combined for kw in ("context", "length", "token")):
                return ErrorCategory.context_overflow

            # Default for 400: context_overflow (most common cause)
            return ErrorCategory.context_overflow

        # Other 4xx — treat as server_error / unknown
        return ErrorCategory.server_error

    if isinstance(exception, APITimeoutError):
        return ErrorCategory.timeout

    if isinstance(exception, APIConnectionError):
        return ErrorCategory.timeout

    # Unknown exception — re-raise
    raise exception
