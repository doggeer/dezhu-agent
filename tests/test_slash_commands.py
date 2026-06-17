"""内置斜杠命令测试."""

from __future__ import annotations

from typing import Any

import pytest

from dezhu_agent.config import Settings
from dezhu_agent.core.compression import ContextCompressor
from dezhu_agent.core.slash_commands import _cmd_help, _cmd_messages, _cmd_usages, handle_command


@pytest.fixture
def compressor() -> ContextCompressor:
    return ContextCompressor(Settings())


@pytest.fixture
def config() -> Settings:
    s = Settings()
    s.MODEL_MAX_CONTEXT_TOKENS = 65536
    s.COMPRESSION_THRESHOLD = 55000
    return s


class TestHandleCommand:
    def test_empty_input_returns_true(self, compressor: ContextCompressor, config: Settings) -> None:
        assert handle_command("", [], compressor, config) is True

    def test_quit_returns_true(self, compressor: ContextCompressor, config: Settings) -> None:
        assert handle_command("quit", [], compressor, config) is True
        assert handle_command("exit", [], compressor, config) is True
        assert handle_command("/quit", [], compressor, config) is True
        assert handle_command("/exit", [], compressor, config) is True

    def test_slash_messages_returns_false(self, compressor: ContextCompressor, config: Settings, capsys: Any) -> None:
        assert handle_command("/messages", [], compressor, config) is False

    def test_slash_usages_returns_false(self, compressor: ContextCompressor, config: Settings, capsys: Any) -> None:
        assert handle_command("/usages", [], compressor, config) is False

    def test_slash_help_returns_false(self, compressor: ContextCompressor, config: Settings, capsys: Any) -> None:
        assert handle_command("/help", [], compressor, config) is False

    def test_unknown_slash_returns_false(self, compressor: ContextCompressor, config: Settings, capsys: Any) -> None:
        assert handle_command("/unknown", [], compressor, config) is False

    def test_regular_input_returns_false(self, compressor: ContextCompressor, config: Settings) -> None:
        assert handle_command("hello world", [], compressor, config) is False


class TestCmdMessages:
    def test_empty_messages(self, compressor: ContextCompressor, capsys: Any) -> None:
        _cmd_messages([], compressor)
        captured = capsys.readouterr()
        assert "(empty)" in captured.out

    def test_single_user_message(self, compressor: ContextCompressor, capsys: Any) -> None:
        msgs: list[dict[str, Any]] = [{"role": "user", "content": "hello"}]
        _cmd_messages(msgs, compressor)
        captured = capsys.readouterr()
        assert "[ 1]" in captured.out
        assert "user" in captured.out
        assert "tokens" in captured.out
        assert "hello" in captured.out

    def test_assistant_with_tool_calls(self, compressor: ContextCompressor, capsys: Any) -> None:
        msgs: list[dict[str, Any]] = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "t1", "function": {"name": "ls", "arguments": "{}"}},
                    {"id": "t2", "function": {"name": "cat", "arguments": "{}"}},
                ],
            }
        ]
        _cmd_messages(msgs, compressor)
        captured = capsys.readouterr()
        assert "[2 tool_calls]" in captured.out

    def test_tool_message(self, compressor: ContextCompressor, capsys: Any) -> None:
        msgs: list[dict[str, Any]] = [
            {
                "role": "tool",
                "tool_call_id": "a1b2c3d4e5f6g7h8",
                "content": "command output here",
            }
        ]
        _cmd_messages(msgs, compressor)
        captured = capsys.readouterr()
        assert "(a1b2c3d4...)" in captured.out

    def test_long_content_truncated(self, compressor: ContextCompressor, capsys: Any) -> None:
        long_text = "x" * 200
        msgs: list[dict[str, Any]] = [{"role": "user", "content": long_text}]
        _cmd_messages(msgs, compressor)
        captured = capsys.readouterr()
        assert long_text[:80] + "..." in captured.out
        assert long_text not in captured.out


class TestCmdUsages:
    def test_ok_status(self, compressor: ContextCompressor, config: Settings, capsys: Any) -> None:
        config.MODEL_MAX_CONTEXT_TOKENS = 65536
        config.COMPRESSION_THRESHOLD = 55000
        msgs: list[dict[str, Any]] = [{"role": "user", "content": "hi"}]
        _cmd_usages(msgs, compressor, config)
        captured = capsys.readouterr()
        assert "OK" in captured.out

    def test_compression_needed_status(self, compressor: ContextCompressor, config: Settings, capsys: Any) -> None:
        config.MODEL_MAX_CONTEXT_TOKENS = 65536
        config.COMPRESSION_THRESHOLD = 10
        msgs: list[dict[str, Any]] = [{"role": "user", "content": "x" * 500} for _ in range(20)]
        _cmd_usages(msgs, compressor, config)
        captured = capsys.readouterr()
        assert "Compression needed" in captured.out

    def test_near_limit_status(self, compressor: ContextCompressor, config: Settings, capsys: Any) -> None:
        config.MODEL_MAX_CONTEXT_TOKENS = 100
        config.COMPRESSION_THRESHOLD = 90
        msgs: list[dict[str, Any]] = [{"role": "user", "content": "x" * 1000} for _ in range(10)]
        _cmd_usages(msgs, compressor, config)
        captured = capsys.readouterr()
        assert "Near limit" in captured.out

    def test_shows_estimate_and_threshold(self, compressor: ContextCompressor, config: Settings, capsys: Any) -> None:
        msgs: list[dict[str, Any]] = [{"role": "user", "content": "hi"}]
        _cmd_usages(msgs, compressor, config)
        captured = capsys.readouterr()
        assert "Estimate" in captured.out
        assert "Threshold" in captured.out


class TestCmdHelp:
    def test_contains_expected_sections(self, capsys: Any) -> None:
        _cmd_help()
        captured = capsys.readouterr()
        assert "dezhu-agent" in captured.out
        assert "/messages" in captured.out
        assert "/usages" in captured.out
        assert "/help" in captured.out
        assert "quit" in captured.out
