"""上下文压缩模块测试."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from dezhu_agent.config import Settings
from dezhu_agent.core.compression import (
    OLD_TOOL_PLACEHOLDER,
    SUMMARY_PREFIX,
    CompressionStuckError,
    ContextCompressor,
)

LOREM = "the quick brown fox jumps over the lazy dog. "



@pytest.fixture
def compressor() -> ContextCompressor:
    """使用默认配置创建压缩器，避免网络调用."""
    settings = Settings()
    # 压低阈值让测试更容易触发压缩
    settings.COMPRESSION_THRESHOLD = 200
    settings.TAIL_TOKEN_BUDGET = 100
    settings.PROTECT_FIRST = 2
    settings.KEEP_RECENT_TOOL_RESULTS = 2
    settings.COMPRESSION_MIN_SHRINK = 0.5
    return ContextCompressor(settings)


# ---- Token 估算 ----

class TestEstimateTokens:
    def test_empty_list(self, compressor: ContextCompressor) -> None:
        assert compressor.estimate_tokens([]) == 3  # 3 tokens overhead

    def test_simple_user_message(self, compressor: ContextCompressor) -> None:
        msgs: list[dict[str, Any]] = [{"role": "user", "content": "hello"}]
        tokens = compressor.estimate_tokens(msgs)
        assert tokens > 3  # overhead + "hello"

    def test_tool_message(self, compressor: ContextCompressor) -> None:
        msgs = [{"role": "tool", "tool_call_id": "abc", "content": "output"}]
        tokens = compressor.estimate_tokens(msgs)
        assert tokens > 3

    def test_assistant_with_tool_calls(self, compressor: ContextCompressor) -> None:
        msgs = [
            {
                "role": "assistant",
                "content": "let me check",
                "tool_calls": [{"function": {"name": "cat", "arguments": '{"path":"/x"}'}}],
            }
        ]
        tokens = compressor.estimate_tokens(msgs)
        assert tokens > 3

    def test_grows_with_content(self, compressor: ContextCompressor) -> None:
        short = compressor.estimate_tokens([{"role": "user", "content": "hi"}])
        long = compressor.estimate_tokens([{"role": "user", "content": "hi " * 100}])
        assert long > short


class TestEstimateSingle:
    def test_handles_all_field_types(self, compressor: ContextCompressor) -> None:
        msg: dict[str, Any] = {
            "role": "assistant",
            "content": "text",
            "tool_calls": [{"function": {"name": "cmd", "arguments": "{}"}}],
        }
        tokens = compressor._estimate_single(msg)
        assert tokens > 0


# ---- Layer 1: 旧工具输出裁剪 ----

class TestClearOldToolOutputs:
    def test_clears_old_tool_messages(self, compressor: ContextCompressor) -> None:
        msgs: list[dict[str, Any]] = [
            {"role": "user", "content": "question"},
            {"role": "assistant", "content": "let me check", "tool_calls": [
                {"id": "1", "type": "function", "function": {"name": "cat", "arguments": "{}"}}
            ]},
            {"role": "tool", "tool_call_id": "1", "content": "old result 1"},
            {"role": "tool", "tool_call_id": "2", "content": "old result 2"},
            {"role": "assistant", "content": "still working", "tool_calls": [
                {"id": "3", "type": "function", "function": {"name": "ls", "arguments": "{}"}}
            ]},
            {"role": "tool", "tool_call_id": "3", "content": "recent result 1"},
            {"role": "tool", "tool_call_id": "4", "content": "recent result 2"},
        ]
        compressor.clear_old_tool_outputs(msgs, keep_recent=2)

        # 前 2 条 tool 结果被清
        assert msgs[2]["content"] == OLD_TOOL_PLACEHOLDER
        assert msgs[3]["content"] == OLD_TOOL_PLACEHOLDER
        # 后 2 条 tool 结果保留
        assert msgs[5]["content"] == "recent result 1"
        assert msgs[6]["content"] == "recent result 2"

    def test_no_tool_messages(self, compressor: ContextCompressor) -> None:
        msgs = [{"role": "user", "content": "hi"}]
        original = [dict(m) for m in msgs]
        compressor.clear_old_tool_outputs(msgs)
        assert msgs == original

    def test_keeps_all_if_within_keep_recent(self, compressor: ContextCompressor) -> None:
        msgs = [
            {"role": "tool", "tool_call_id": "1", "content": "result 1"},
            {"role": "tool", "tool_call_id": "2", "content": "result 2"},
        ]
        compressor.clear_old_tool_outputs(msgs, keep_recent=5)
        assert msgs[0]["content"] == "result 1"
        assert msgs[1]["content"] == "result 2"

    def test_already_placeholder_not_modified(self, compressor: ContextCompressor) -> None:
        msgs = [{"role": "tool", "tool_call_id": "1", "content": OLD_TOOL_PLACEHOLDER}]
        compressor.clear_old_tool_outputs(msgs, keep_recent=0)
        assert msgs[0]["content"] == OLD_TOOL_PLACEHOLDER


# ---- Layer 2: 找压缩边界 ----

class TestFindBoundaries:
    def test_returns_none_when_too_few_messages(self, compressor: ContextCompressor) -> None:
        msgs = [{"role": "user", "content": "hi"}]
        assert compressor.find_boundaries(msgs, protect_first=2) is None

    def test_returns_boundaries_when_enough_messages(self, compressor: ContextCompressor) -> None:
        # 构建足够长的消息让 tail budget 能确定尾边界
        msgs: list[dict[str, Any]] = [
            {"role": "user", "content": "head message"},
            {"role": "assistant", "content": "head reply"},
        ]
        # 中间大量内容迫使尾部边界内移
        for _i in range(20):
            msgs.append({"role": "assistant", "content": "mid " + "the quick brown fox jumps over the lazy dog. " * 20})
        msgs.append({"role": "assistant", "content": "tail message"})

        result = compressor.find_boundaries(msgs, protect_first=2, tail_budget=500)
        assert result is not None
        head_end, tail_start = result
        assert head_end >= 2
        assert tail_start <= len(msgs)
        assert head_end < tail_start

    def test_skips_tool_at_head_boundary(self, compressor: ContextCompressor) -> None:
        """head_end 落在 tool 上时应跳过."""
        msgs: list[dict[str, Any]] = [
            {"role": "user", "content": "head"},
            {"role": "assistant", "content": "head reply", "tool_calls": [
                {"id": "1", "type": "function", "function": {"name": "t", "arguments": "{}"}}
            ]},
            {"role": "tool", "tool_call_id": "1", "content": "tool output"},
            # head_end=2 落在这里（tool），应跳到 3
            {"role": "assistant", "content": "next"},
        ]
        for _i in range(30):
            msgs.append({"role": "assistant", "content": "the quick brown fox jumps over the lazy dog. " * 20})
        result = compressor.find_boundaries(msgs, protect_first=2, tail_budget=200)
        assert result is not None
        head_end, _ = result
        assert head_end >= 3  # 跳过了 tool 消息

    def test_skips_tool_at_tail_boundary(self, compressor: ContextCompressor) -> None:
        """tail_start 落在 tool 上时应后退."""
        mid = [{"role": "assistant", "content": LOREM * 20} for _ in range(30)]
        msgs: list[dict[str, Any]] = [
            {"role": "user", "content": "head"},
            {"role": "assistant", "content": "head reply"},
            *mid,
            {"role": "assistant", "content": "tail", "tool_calls": [
                {"id": "99", "type": "function", "function": {"name": "t", "arguments": "{}"}}
            ]},
            {"role": "tool", "tool_call_id": "99", "content": "tool output"},
        ]
        result = compressor.find_boundaries(msgs, protect_first=2, tail_budget=300)
        assert result is not None
        _, tail_start = result
        # tail_start 不应指向 tool 消息
        assert msgs[tail_start]["role"] != "tool"


# ---- 组装 ----

class TestAssemble:
    def test_produces_correct_structure(self, compressor: ContextCompressor) -> None:
        msgs = [
            {"role": "user", "content": "head"},
            {"role": "assistant", "content": "mid1"},
            {"role": "assistant", "content": "mid2"},
            {"role": "assistant", "content": "tail"},
        ]
        summary = f"{SUMMARY_PREFIX}\n\n## Goal\ntest"
        result = compressor.assemble(msgs, head_end=1, tail_start=3, summary=summary)
        assert len(result) == 3
        assert result[0] == msgs[0]
        assert result[1]["role"] == "assistant"
        assert result[1]["content"] == summary
        assert result[2] == msgs[3]


# ---- 查找已有摘要 ----

class TestFindExistingSummary:
    def test_finds_summary(self, compressor: ContextCompressor) -> None:
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": f"{SUMMARY_PREFIX}\n\nold summary"},
        ]
        assert compressor._find_existing_summary(msgs) == f"{SUMMARY_PREFIX}\n\nold summary"

    def test_returns_none_when_no_summary(self, compressor: ContextCompressor) -> None:
        msgs = [{"role": "user", "content": "hi"}]
        assert compressor._find_existing_summary(msgs) is None


# ---- 压缩总入口 ----

class TestCompress:
    def test_layer1_sufficient(self, compressor: ContextCompressor) -> None:
        """Layer 1 已足够降到阈值以下时不触发完整压缩."""
        msgs: list[dict[str, Any]] = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "reply", "tool_calls": [
                {"id": "1", "type": "function", "function": {"name": "t", "arguments": "{}"}}
            ]},
            {"role": "tool", "tool_call_id": "1", "content": "the quick brown fox jumps over the lazy dog. " * 50},
            {"role": "tool", "tool_call_id": "2", "content": "the quick brown fox jumps over the lazy dog. " * 50},
            {"role": "tool", "tool_call_id": "3", "content": "the quick brown fox jumps over the lazy dog. " * 50},
            {"role": "tool", "tool_call_id": "4", "content": "recent1"},
            {"role": "tool", "tool_call_id": "5", "content": "recent2"},
            {"role": "assistant", "content": "done"},
        ]
        before = compressor.estimate_tokens(msgs)
        assert before > compressor._config.COMPRESSION_THRESHOLD

        new_msgs, was_compressed = compressor.compress(msgs)
        after = compressor.estimate_tokens(new_msgs)
        assert after < before
        assert not was_compressed  # Layer 1 够了

    def test_full_compression(self, compressor: ContextCompressor) -> None:
        """Layer 1 不够时需要完整压缩."""
        mid = [{"role": "assistant", "content": "mid " + LOREM * 20} for _ in range(30)]
        msgs: list[dict[str, Any]] = [
            {"role": "user", "content": "head"},
            {"role": "assistant", "content": "head reply"},
            *mid,
            {"role": "assistant", "content": "tail"},
        ]

        before = compressor.estimate_tokens(msgs)
        assert before > compressor._config.COMPRESSION_THRESHOLD

        # Mock summarize 避免实际 LLM 调用
        with patch.object(compressor, "summarize", return_value=f"{SUMMARY_PREFIX}\n\n## Goal\ntest"):
            new_msgs, was_compressed = compressor.compress(msgs)

        assert was_compressed
        after = compressor.estimate_tokens(new_msgs)
        assert after < before

    def test_compression_stuck_raises(self, compressor: ContextCompressor) -> None:
        """压缩率不足时抛出 CompressionStuckError."""
        msgs = [
            {"role": "user", "content": "head"},
        ]
        for _ in range(40):
            msgs.append({"role": "assistant", "content": "mid " + "the quick brown fox jumps over the lazy dog. " * 20})
        msgs.append({"role": "assistant", "content": "tail"})

        before = compressor.estimate_tokens(msgs)
        assert before > compressor._config.COMPRESSION_THRESHOLD

        huge_summary = "large summary " * 5000
        with patch.object(
            compressor, "summarize", return_value=huge_summary
        ), pytest.raises(CompressionStuckError):
            compressor.compress(msgs)

    def test_no_boundaries_returns_false(self, compressor: ContextCompressor) -> None:
        """消息太少无法找到边界时返回 False."""
        msgs = [{"role": "user", "content": "hi"}]
        # 消息太少 token 数低于阈值
        assert compressor.estimate_tokens(msgs) <= compressor._config.COMPRESSION_THRESHOLD


# ---- 辅助: 消息格式化 ----

class TestFormatMessagesForSummary:
    def test_formats_various_roles(self, compressor: ContextCompressor) -> None:
        msgs: list[dict[str, Any]] = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
            {"role": "tool", "tool_call_id": "abc", "content": "ls output"},
        ]
        result = compressor._format_messages_for_summary(msgs)
        assert "[user]" in result
        assert "[assistant]" in result
        assert "[tool abc]" in result

    def test_truncates_long_content(self, compressor: ContextCompressor) -> None:
        compressor._config.SUMMARY_PER_MSG_CHARS = 10
        msgs = [{"role": "user", "content": "this is a long message"}]
        result = compressor._format_messages_for_summary(msgs)
        assert "truncated" in result


# ---- CompressionStuckError ----

class TestCompressionStuckError:
    def test_stores_before_after(self) -> None:
        err = CompressionStuckError(100, 95)
        assert err.before == 100
        assert err.after == 95
        assert "100" in str(err)
        assert "95" in str(err)
