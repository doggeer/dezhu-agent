"""测试 NudgeManager — 轮数计数、触发逻辑、消息过滤."""

import tempfile
from unittest.mock import Mock, patch

import pytest

from dezhu_agent.memory import MemoryStore
from dezhu_agent.memory.nudge import NudgeManager, _MAX_REVIEW_ITERATIONS


@pytest.fixture
def store():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield MemoryStore(base_dir=tmpdir)


@pytest.fixture
def nudge(store):
    return NudgeManager(store, interval=3)


# ---- AC-09: 计数器归零 ----
def test_counter_resets_after_trigger(nudge):
    assert nudge.turn_count == 0
    nudge.on_user_turn([])
    nudge.on_user_turn([])
    assert nudge.turn_count == 2
    nudge.reset_counter()
    assert nudge.turn_count == 0


# ---- AC-05: 达到阈值触发审查 ----
def test_nudge_triggers_on_threshold(nudge, store):
    nudge._turn_count = 2  # 手动设置到 2
    with patch("threading.Thread") as mock_thread:
        nudge.on_user_turn([])  # 第 3 轮 → 触发
        mock_thread.assert_called_once()
        assert nudge.turn_count == 0  # AC-09: 归零


def test_nudge_does_not_trigger_below_threshold(nudge, store):
    nudge._turn_count = 1
    with patch("threading.Thread") as mock_thread:
        nudge.on_user_turn([])  # 第 2 轮 → 不触发
        mock_thread.assert_not_called()
        assert nudge.turn_count == 2


# ---- AC-07: 审查 agent 无发现时静默 ----
def test_nothing_to_save_recognized():
    assert (
        "Nothing to save"
        in __import__("dezhu_agent.memory.nudge", fromlist=["_REVIEW_PROMPT"])._REVIEW_PROMPT
    )


# ---- 审查消息过滤 system 消息 ----
def test_build_review_messages_filters_system(nudge):
    messages = [
        {"role": "system", "content": "You are an AI assistant"},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]
    result = nudge._build_review_messages(messages)
    # 应包含审查 system prompt + user + assistant（不含主 system 消息）
    roles = [m["role"] for m in result]
    assert roles.count("system") == 1  # 只有审查提示词
    assert roles.count("user") == 1
    assert roles.count("assistant") == 1


# ---- tool 隔离：只返回 memory 工具 ----
def test_get_memory_only_tools_returns_only_memory():
    from dezhu_agent.tools import registry as tool_registry
    from dezhu_agent.tools import ToolDef

    # 注册一个假的 memory 工具
    tool_registry.register(
        ToolDef(
            name="memory",
            description="test",
            parameters={"type": "object", "properties": {}},
            fn=lambda **kw: "ok",
        )
    )
    tools = NudgeManager._get_memory_only_tools()
    assert tools is not None
    tool_names = [t["function"]["name"] for t in tools]
    assert "memory" in tool_names
    assert "bash" not in tool_names
    assert len(tools) == 1  # 只有 memory
