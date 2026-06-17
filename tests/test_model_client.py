"""ModelClient 测试."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
)

from dezhu_agent.core.model_client import CONTINUE_MESSAGE, ModelClient
from dezhu_agent.models.error import ApiCallResult, ErrorCategory


def _mock_api_response(**kwargs):
    """构造一个带 request 属性的 mock response, 用于异常构造函数."""
    r = MagicMock()
    r.request = MagicMock()
    return r


class TestErrorCategory:
    """ErrorCategory 枚举测试."""

    def test_all_categories_exist(self) -> None:
        expected = {
            "retryable",
            "context_overflow",
            "truncated",
            "auth_failure",
            "model_not_found",
            "thinking_budget",
            "fatal",
        }
        actual = {c.value for c in ErrorCategory}
        assert expected == actual

    def test_error_category_is_string(self) -> None:
        assert ErrorCategory.RETRYABLE == "retryable"
        assert str(ErrorCategory.FATAL) == "fatal"


class TestApiCallResult:
    """ApiCallResult 模型测试."""

    def test_defaults(self) -> None:
        result = ApiCallResult(success=False)
        assert result.success is False
        assert result.response is None
        assert result.category is None
        assert result.error_message == ""
        assert result.finish_reason == ""
        assert result.needs_continuation is False

    def test_success_result(self) -> None:
        result = ApiCallResult(success=True, finish_reason="stop")
        assert result.success is True
        assert result.finish_reason == "stop"
        assert result.needs_continuation is False

    def test_length_result(self) -> None:
        result = ApiCallResult(success=True, finish_reason="length", needs_continuation=True)
        assert result.needs_continuation is True

    def test_error_result(self) -> None:
        result = ApiCallResult(
            success=False,
            category=ErrorCategory.AUTH_FAILURE,
            error_message="Invalid API key",
        )
        assert result.category == ErrorCategory.AUTH_FAILURE
        assert "Invalid API key" in result.error_message


class TestModelClientBackupParsing:
    """BACKUP_MODELS 解析测试."""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("", []),
            ("  ", []),
            ("deepseek-v4-flash", ["deepseek-v4-flash"]),
            ("m1,m2,m3", ["m1", "m2", "m3"]),
            (" m1 , m2 , m3 ", ["m1", "m2", "m3"]),
            (",,m1,,m2,,", ["m1", "m2"]),
        ],
    )
    def test_parse(self, settings, raw, expected):
        settings.BACKUP_MODELS = raw
        client = ModelClient(settings)
        assert client._backup_models == expected


def _make_ok_response():
    """构造一个成功的 mock chat completion 响应."""
    r = MagicMock()
    c = MagicMock()
    c.finish_reason = "stop"
    c.message = MagicMock(content="ok", tool_calls=None)
    r.choices = [c]
    r.usage = None
    return r


class TestModelClientCall:
    """ModelClient.call() — 错误分类与重试."""

    def test_call_success_stop(self, settings):
        client = ModelClient(settings)
        with patch.object(client._client.chat.completions, "create", return_value=_make_ok_response()):
            result = client.call([{"role": "user", "content": "hi"}], None)
        assert result.success is True
        assert result.finish_reason == "stop"
        assert result.needs_continuation is False

    def test_call_success_length(self, settings):
        client = ModelClient(settings)
        r = MagicMock()
        c = MagicMock()
        c.finish_reason = "length"
        c.message = MagicMock(content="partial...", tool_calls=None)
        r.choices = [c]
        r.usage = None

        with patch.object(client._client.chat.completions, "create", return_value=r):
            result = client.call([{"role": "user", "content": "hi"}], None)

        assert result.success is True
        assert result.finish_reason == "length"
        assert result.needs_continuation is True

    def test_call_rate_limit_retry_success(self, settings):
        client = ModelClient(settings)
        mock_resp = _mock_api_response()
        with patch.object(
            client._client.chat.completions, "create",
            side_effect=[RateLimitError("rate limited", response=mock_resp, body=None), _make_ok_response()],
        ) as mock_create:
            with patch.object(client, "_backoff", return_value=None):
                result = client.call([{"role": "user", "content": "hi"}], None)

        assert result.success is True
        assert mock_create.call_count == 2

    def test_call_rate_limit_exhausted(self, settings):
        client = ModelClient(settings)
        settings.MAX_RETRIES = 1
        mock_resp = _mock_api_response()

        with patch.object(
            client._client.chat.completions, "create",
            side_effect=RateLimitError("rate limited", response=mock_resp, body=None),
        ):
            with patch.object(client, "_backoff", return_value=None):
                result = client.call([{"role": "user", "content": "hi"}], None)

        assert result.success is False
        assert result.category == ErrorCategory.RETRYABLE

    def test_call_timeout_retry(self, settings):
        client = ModelClient(settings)
        with patch.object(
            client._client.chat.completions, "create",
            side_effect=[APITimeoutError("timeout"), _make_ok_response()],
        ):
            with patch.object(client, "_backoff", return_value=None):
                result = client.call([{"role": "user", "content": "hi"}], None)

        assert result.success is True

    def test_call_auth_error(self, settings):
        client = ModelClient(settings)
        mock_resp = _mock_api_response()
        with patch.object(
            client._client.chat.completions, "create",
            side_effect=AuthenticationError("invalid key", response=mock_resp, body=None),
        ):
            result = client.call([{"role": "user", "content": "hi"}], None)

        assert result.success is False
        assert result.category == ErrorCategory.AUTH_FAILURE

    def test_call_model_not_found(self, settings):
        client = ModelClient(settings)
        mock_resp = _mock_api_response()
        with patch.object(
            client._client.chat.completions, "create",
            side_effect=NotFoundError("model not found", response=mock_resp, body=None),
        ):
            result = client.call([{"role": "user", "content": "hi"}], None)

        assert result.success is False
        assert result.category == ErrorCategory.MODEL_NOT_FOUND

    def test_call_bad_request(self, settings):
        client = ModelClient(settings)
        mock_resp = _mock_api_response()
        with patch.object(
            client._client.chat.completions, "create",
            side_effect=BadRequestError("context too long", response=mock_resp, body=None),
        ):
            result = client.call([{"role": "user", "content": "hi"}], None)

        assert result.success is False
        assert result.category == ErrorCategory.CONTEXT_OVERFLOW

    def test_call_permission_denied(self, settings):
        client = ModelClient(settings)
        mock_resp = _mock_api_response()
        with patch.object(
            client._client.chat.completions, "create",
            side_effect=PermissionDeniedError("quota exceeded", response=mock_resp, body=None),
        ):
            result = client.call([{"role": "user", "content": "hi"}], None)

        assert result.success is False
        assert result.category == ErrorCategory.FATAL

    def test_call_connection_error_retry(self, settings):
        client = ModelClient(settings)
        with patch.object(
            client._client.chat.completions, "create",
            side_effect=[APIConnectionError(message="connection refused", request=MagicMock()), _make_ok_response()],
        ):
            with patch.object(client, "_backoff", return_value=None):
                result = client.call([{"role": "user", "content": "hi"}], None)

        assert result.success is True

    def test_call_internal_server_error_retry(self, settings):
        client = ModelClient(settings)
        mock_resp = _mock_api_response()
        with patch.object(
            client._client.chat.completions, "create",
            side_effect=[InternalServerError("server error", response=mock_resp, body=None), _make_ok_response()],
        ):
            with patch.object(client, "_backoff", return_value=None):
                result = client.call([{"role": "user", "content": "hi"}], None)

        assert result.success is True


class TestModelClientContinuation:
    """ModelClient.call_with_continuation() 测试."""

    def test_no_continuation_needed(self, settings):
        client = ModelClient(settings)
        r = MagicMock()
        c = MagicMock()
        c.finish_reason = "stop"
        c.message = MagicMock(content="Hello", tool_calls=None)
        r.choices = [c]
        r.usage = None

        with patch.object(client._client.chat.completions, "create", return_value=r):
            result = client.call_with_continuation([{"role": "user", "content": "hi"}], None)

        assert result.success is True
        assert result.needs_continuation is False

    def test_continuation_accumulates_content(self, settings):
        client = ModelClient(settings)
        settings.MAX_CONTINUATION_ATTEMPTS = 3

        r1 = MagicMock()
        c1 = MagicMock()
        c1.finish_reason = "length"
        c1.message = MagicMock(content="Part1", tool_calls=None)
        r1.choices = [c1]
        r1.usage = None

        r2 = MagicMock()
        c2 = MagicMock()
        c2.finish_reason = "stop"
        c2.message = MagicMock(content="Part2", tool_calls=None)
        r2.choices = [c2]
        r2.usage = None

        messages: list[dict] = [{"role": "user", "content": "hi"}]
        with patch.object(
            client._client.chat.completions, "create",
            side_effect=[r1, r2],
        ) as mock_create:
            result = client.call_with_continuation(messages, None)

        assert result.success is True
        assert result.needs_continuation is False
        assert mock_create.call_count == 2
        assert result.response is not None
        assert result.response.choices[0].message.content == "Part1Part2"
        assert messages[1]["content"] == CONTINUE_MESSAGE

    def test_continuation_tool_calls_truncated(self, settings):
        client = ModelClient(settings)

        r1 = MagicMock()
        c1 = MagicMock()
        c1.finish_reason = "length"
        c1.message = MagicMock(content=None, tool_calls=[MagicMock()])
        r1.choices = [c1]
        r1.usage = None

        r2 = MagicMock()
        c2 = MagicMock()
        c2.finish_reason = "stop"
        c2.message = MagicMock(content="done", tool_calls=None)
        r2.choices = [c2]
        r2.usage = None

        messages: list[dict] = [{"role": "user", "content": "hi"}]
        with patch.object(
            client._client.chat.completions, "create",
            side_effect=[r1, r2],
        ) as mock_create:
            result = client.call_with_continuation(messages, None)

        assert result.success is True
        assert mock_create.call_count == 2
        assert messages[1]["content"] == CONTINUE_MESSAGE

    def test_continuation_max_attempts_exhausted(self, settings):
        client = ModelClient(settings)
        settings.MAX_CONTINUATION_ATTEMPTS = 2

        def _make():
            r = MagicMock()
            c = MagicMock()
            c.finish_reason = "length"
            c.message = MagicMock(content="part", tool_calls=None)
            r.choices = [c]
            r.usage = None
            return r

        responses = [_make() for _ in range(3)]

        with patch.object(client._client.chat.completions, "create", side_effect=responses):
            result = client.call_with_continuation([{"role": "user", "content": "hi"}], None)

        assert result.success is False
        assert result.category == ErrorCategory.TRUNCATED


class TestModelClientFallback:
    """故障转移测试."""

    def test_fallback_no_backups(self, settings):
        settings.BACKUP_MODELS = ""
        client = ModelClient(settings)
        assert client.try_fallback() is False

    def test_fallback_success(self, settings):
        settings.BACKUP_MODELS = "fallback-model"
        client = ModelClient(settings)
        with patch.object(client, "check_health", return_value=True):
            assert client.try_fallback() is True
        assert client._current_model == "fallback-model"
        assert client._is_fallback is True

    def test_fallback_all_unhealthy(self, settings):
        settings.BACKUP_MODELS = "fb1,fb2"
        client = ModelClient(settings)
        original = client._current_model
        with patch.object(client, "check_health", return_value=False):
            assert client.try_fallback() is False
        assert client._current_model == original

    def test_fallback_skip_current(self, settings):
        settings.BACKUP_MODELS = "current,other"
        client = ModelClient(settings)
        client._current_model = "current"
        with patch.object(client, "check_health", return_value=True):
            assert client.try_fallback() is True
        assert client._current_model == "other"


class TestModelClientRecoverMain:
    """主模型恢复测试."""

    def test_not_in_fallback_does_nothing(self, settings):
        client = ModelClient(settings)
        original = client._current_model
        client.try_recover_main()
        assert client._current_model == original

    def test_within_cooldown_does_nothing(self, settings):
        import time
        client = ModelClient(settings)
        client._is_fallback = True
        client._current_model = "fallback"
        client._fallback_at = time.time()
        client.try_recover_main()
        assert client._current_model == "fallback"

    def test_recover_main_success(self, settings):
        import time
        from openai import OpenAI

        settings.MAIN_MODEL_COOLDOWN_SECONDS = 0
        client = ModelClient(settings)
        client._is_fallback = True
        client._current_model = "fallback"
        client._fallback_at = time.time() - 999

        r = MagicMock()
        r.choices = [MagicMock()]

        with patch.object(OpenAI, "chat", MagicMock()):
            with patch.object(OpenAI, "__init__", lambda self, **kw: setattr(self, 'chat', MagicMock())):
                with patch("dezhu_agent.core.model_client.OpenAI") as mock_openai:
                    mock_openai.return_value.chat.completions.create.return_value = r
                    client.try_recover_main()

        # 主模型健康检查成功 → 恢复
        assert client._current_model == settings.MODEL
        assert client._is_fallback is False
        assert client._fallback_at is None

    def test_recover_main_still_unhealthy(self, settings):
        import time
        from openai import OpenAI

        settings.MAIN_MODEL_COOLDOWN_SECONDS = 0
        client = ModelClient(settings)
        client._is_fallback = True
        client._current_model = "fallback"
        client._fallback_at = time.time() - 999

        with patch("dezhu_agent.core.model_client.OpenAI") as mock_openai:
            mock_openai.return_value.chat.completions.create.side_effect = APIConnectionError(message="still down", request=MagicMock())
            client.try_recover_main()

        assert client._current_model == "fallback"
        assert client._is_fallback is True


class TestModelClientHealthCheck:
    """健康检查测试."""

    def test_health_check_ok(self, settings):
        client = ModelClient(settings)
        r = MagicMock()
        r.choices = [MagicMock()]
        with patch.object(client._client.chat.completions, "create", return_value=r):
            assert client.check_health() is True

    def test_health_check_fail(self, settings):
        client = ModelClient(settings)
        with patch.object(
            client._client.chat.completions, "create",
            side_effect=APIConnectionError(message="connection refused", request=MagicMock()),
        ):
            assert client.check_health() is False
