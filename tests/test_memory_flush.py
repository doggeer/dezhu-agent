"""测试 FlushManager — 轮数阈值、hook 回调行为."""

import tempfile
from unittest.mock import Mock, patch

import pytest

from dezhu_agent.memory import MemoryStore
from dezhu_agent.memory.flush import FlushManager, _MAX_FLUSH_ITERATIONS


@pytest.fixture
def store():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield MemoryStore(base_dir=tmpdir)


@pytest.fixture
def flush(store):
    return FlushManager(store, min_turns=6)


# ---- AC-13: 轮数不足不触发 ----
def test_flush_skips_below_min_turns(flush):
    flush._turn_count = 3
    with patch("dezhu_agent.llm.call_llm") as mock_llm:
        result = flush._do_flush([])
        mock_llm.assert_not_called()
        assert result == []
        assert flush._turn_count == 3  # 计数器不变


# ---- AC-10: 压缩前触发 flush ----
def test_flush_triggers_at_min_turns(flush):
    flush._turn_count = 6
    with patch("dezhu_agent.llm.call_llm") as mock_llm:
        mock_llm.return_value = Mock(content="Nothing to save", tool_calls=None)
        result = flush._do_flush([{"role": "user", "content": "hi"}])
        mock_llm.assert_called_once()
        assert flush._turn_count == 0  # 触发后归零


# ---- AC-12: flush 完成后返回原始消息 ----
def test_flush_returns_original_messages(flush):
    flush._turn_count = 6
    original = [{"role": "user", "content": "test message"}]
    with patch("dezhu_agent.llm.call_llm") as mock_llm:
        mock_llm.return_value = Mock(content="Nothing to save", tool_calls=None)
        result = flush._do_flush(original)
        assert result is original  # 返回原始列表（不变）


# ---- hook 回调签名 ----
def test_pre_compress_hook_calls_do_flush(flush):
    flush._turn_count = 6
    messages = [{"role": "user", "content": "test"}]
    hook = flush.create_pre_compress_hook()

    with patch("dezhu_agent.llm.call_llm") as mock_llm:
        mock_llm.return_value = Mock(content="Nothing to save", tool_calls=None)
        result = hook(messages)
        assert result is messages


# ---- on_user_turn 计数器 ----
def test_on_user_turn_increments(flush):
    assert flush.turn_count == 0
    flush.on_user_turn()
    assert flush.turn_count == 1
    flush.on_user_turn()
    assert flush.turn_count == 2
