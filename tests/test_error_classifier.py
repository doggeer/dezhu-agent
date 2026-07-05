"""Tests for error_classifier module."""

from unittest.mock import MagicMock

import pytest
from openai import APIConnectionError, APIStatusError, APITimeoutError

from dezhu_agent.error_classifier import ErrorCategory, classify_error


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _mock_api_status_error(status_code: int, body: dict | None = None) -> APIStatusError:
    """Create a mock APIStatusError with the given status_code and body."""
    mock = MagicMock(spec=APIStatusError)
    mock.status_code = status_code
    mock.body = body or {}
    return mock


# ---------------------------------------------------------------------------
# ErrorCategory enum
# ---------------------------------------------------------------------------


def test_error_category_has_all_expected_values():
    """ErrorCategory must include length, context_overflow, rate_limit, timeout,
    auth, billing, model_not_found, server_error."""
    expected = {
        "length",
        "context_overflow",
        "rate_limit",
        "timeout",
        "auth",
        "billing",
        "model_not_found",
        "server_error",
    }
    actual = set(v.value for v in ErrorCategory)
    assert actual == expected


# ---------------------------------------------------------------------------
# classify_error: APIStatusError by status_code
# ---------------------------------------------------------------------------


def test_classify_429_is_rate_limit():
    err = _mock_api_status_error(429)
    assert classify_error(err) == ErrorCategory.rate_limit


def test_classify_401_is_auth():
    err = _mock_api_status_error(401)
    assert classify_error(err) == ErrorCategory.auth


def test_classify_404_is_model_not_found():
    err = _mock_api_status_error(404)
    assert classify_error(err) == ErrorCategory.model_not_found


def test_classify_500_is_server_error():
    err = _mock_api_status_error(500)
    assert classify_error(err) == ErrorCategory.server_error


def test_classify_502_is_server_error():
    err = _mock_api_status_error(502)
    assert classify_error(err) == ErrorCategory.server_error


def test_classify_503_is_server_error():
    err = _mock_api_status_error(503)
    assert classify_error(err) == ErrorCategory.server_error


# ---------------------------------------------------------------------------
# classify_error: 400 + body inspection
# ---------------------------------------------------------------------------


def test_classify_400_context_length_exceeded():
    """400 with body containing 'context' / 'length' / 'token' -> context_overflow."""
    err = _mock_api_status_error(
        400,
        body={"error": {"code": "context_length_exceeded", "message": "context too long"}},
    )
    assert classify_error(err) == ErrorCategory.context_overflow


def test_classify_400_token_limit():
    """400 with body mentioning 'token' -> context_overflow."""
    err = _mock_api_status_error(
        400,
        body={"error": {"message": "maximum token limit exceeded"}},
    )
    assert classify_error(err) == ErrorCategory.context_overflow


def test_classify_400_insufficient_quota_by_code():
    """400 with body code 'insufficient_quota' -> billing."""
    err = _mock_api_status_error(
        400,
        body={"error": {"code": "insufficient_quota"}},
    )
    assert classify_error(err) == ErrorCategory.billing


def test_classify_400_insufficient_quota_by_message():
    """400 with body message containing 'quota' -> billing."""
    err = _mock_api_status_error(
        400,
        body={"error": {"message": "You exceeded your current quota"}},
    )
    assert classify_error(err) == ErrorCategory.billing


def test_classify_400_generic_body_is_context_overflow():
    """400 with generic body that does NOT mention quota -> context_overflow (default for 400)."""
    err = _mock_api_status_error(
        400,
        body={"error": {"message": "bad request"}},
    )
    assert classify_error(err) == ErrorCategory.context_overflow


# ---------------------------------------------------------------------------
# classify_error: timeout errors
# ---------------------------------------------------------------------------


def test_classify_api_timeout_is_timeout():
    err = MagicMock(spec=APITimeoutError)
    assert classify_error(err) == ErrorCategory.timeout


def test_classify_api_connection_error_is_timeout():
    err = MagicMock(spec=APIConnectionError)
    assert classify_error(err) == ErrorCategory.timeout


# ---------------------------------------------------------------------------
# classify_error: unknown exception re-raised
# ---------------------------------------------------------------------------


def test_classify_unknown_exception_reraises():
    original = ValueError("something went wrong")
    with pytest.raises(ValueError, match="something went wrong") as exc_info:
        classify_error(original)
    # The original exception must be the one re-raised (not wrapped)
    assert exc_info.value is original


def test_classify_none_is_unknown_and_reraises():
    """None is not an Exception but classify_error must handle gracefully."""
    with pytest.raises(TypeError):
        classify_error(None)  # type: ignore[arg-type]
