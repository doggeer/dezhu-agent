"""上下文压缩子系统测试."""

from __future__ import annotations

import pytest

from dezhu_agent.compression import (
    CompressionConfig,
    CompressionStuckError,
    compress,
    estimate_tokens,
    find_boundaries,
    get_task_state,
    reset_task_state,
    summarize_middle,
    truncate_old_tool_outputs,
)

# ---------------------------------------------------------------------------
# estimate_tokens
# ---------------------------------------------------------------------------


class TestEstimateTokens:
    def test_empty_list_returns_1(self):
        assert estimate_tokens([]) == 1

    def test_simple_message(self):
        n = estimate_tokens([{"role": "user", "content": "hello world"}])
        assert n > 0

    def test_tool_calls_accounted(self):
        msgs = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "function": {
                            "name": "read_file",
                            "arguments": '{"path": "/tmp/test"}',
                        }
                    }
                ],
            }
        ]
        n = estimate_tokens(msgs)
        assert n > 3  # should be more than content-only

    def test_scales_with_content_length(self):
        short = estimate_tokens([{"role": "user", "content": "hi"}])
        long = estimate_tokens([{"role": "user", "content": "hello world " * 100}])
        assert long > short * 5


# ---------------------------------------------------------------------------
# Layer 1: truncate_old_tool_outputs
# ---------------------------------------------------------------------------


class TestLayer1:
    def test_tool_within_max_rounds_untouched(self):
        msgs = [
            {"role": "user", "content": "q1"},
            {"role": "tool", "content": "result1", "tool_call_id": "1", "name": "read"},
        ]
        result = truncate_old_tool_outputs(msgs, max_rounds=10)
        assert result[1]["content"] == "result1"

    def test_tool_exceeds_max_rounds_cleared(self):
        msgs = [
            {"role": "user", "content": "q1"},
            {"role": "tool", "content": "result1", "tool_call_id": "1", "name": "read"},
        ]
        result = truncate_old_tool_outputs(msgs, max_rounds=0)
        assert result[1]["content"] == "[Old tool output cleared]"
        assert result[1]["tool_call_id"] == "1"
        assert result[1]["name"] == "read"

    def test_non_tool_messages_untouched(self):
        msgs = [
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "q2"},
            {"role": "assistant", "content": "a2"},
        ]
        result = truncate_old_tool_outputs(msgs, max_rounds=0)
        assert result == msgs

    def test_original_list_not_mutated(self):
        msgs = [
            {"role": "user", "content": "q1"},
            {"role": "tool", "content": "old", "tool_call_id": "1", "name": "r"},
        ]
        original_content = msgs[1]["content"]
        truncated = truncate_old_tool_outputs(msgs, max_rounds=0)
        assert msgs[1]["content"] == original_content  # untouched
        assert truncated[1]["content"] == "[Old tool output cleared]"

    def test_multiple_rounds_mixed(self):
        """第 1 轮超过阈值被裁剪，第 2 轮保留。"""
        msgs = [
            {"role": "user", "content": "q1"},
            {"role": "tool", "content": "old result", "tool_call_id": "1"},
            {"role": "user", "content": "q2"},
            {"role": "tool", "content": "recent result", "tool_call_id": "2"},
        ]
        result = truncate_old_tool_outputs(msgs, max_rounds=1)
        assert result[1]["content"] == "[Old tool output cleared]"  # round 1
        assert result[3]["content"] == "recent result"  # round 2, within threshold


# ---------------------------------------------------------------------------
# Layer 2: find_boundaries
# ---------------------------------------------------------------------------


class TestLayer2:
    def test_normal_split(self):
        msgs = [
            {"role": "system", "content": "you are helpful"},
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "q2"},
            {"role": "assistant", "content": "a2"},
            {"role": "user", "content": "q3"},
            {"role": "assistant", "content": "a3"},
            {"role": "user", "content": "q4"},
            {"role": "assistant", "content": "a4"},
        ]
        head, middle, tail = find_boundaries(msgs, head_rounds=2, tail_tokens=4)
        assert len(head) > 0
        assert len(tail) > 0
        # head 包含前 2 轮
        assert any(m["content"] == "q2" for m in head)
        # tail 包含最后的消息
        assert any(m["content"] == "q4" for m in tail)

    def test_all_head_when_few_messages(self):
        msgs = [
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"},
        ]
        head, middle, tail = find_boundaries(msgs, head_rounds=5, tail_tokens=10)
        assert len(head) == 2
        assert len(middle) == 0
        assert len(tail) == 0

    def test_all_tail_when_small_messages(self):
        """短消息不足以超过 tail_tokens 时应全部归入 tail。"""
        msgs = [
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "q2"},
            {"role": "assistant", "content": "a2"},
            {"role": "user", "content": "q3"},
            {"role": "assistant", "content": "a3"},
            {"role": "user", "content": "q4"},
            {"role": "assistant", "content": "a4"},
            {"role": "user", "content": "q5"},
            {"role": "assistant", "content": "a5"},
        ]
        head, middle, tail = find_boundaries(msgs, head_rounds=2, tail_tokens=100000)
        # 全部在 tail 内 → head 和 tail 会重叠，middle 应为空
        assert len(middle) == 0

    def test_empty_input(self):
        head, middle, tail = find_boundaries([], head_rounds=3, tail_tokens=100)
        assert head == []
        assert middle == []
        assert tail == []

    def test_boundary_alignment_orphan_tool(self):
        """切点落在 tool 消息上时，应吸附到下一个非 tool 消息。"""
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1", "tool_calls": [{"id": "1"}]},
            {"role": "tool", "content": "result1", "tool_call_id": "1"},
            {"role": "user", "content": "q2"},
            {"role": "assistant", "content": "a2"},
        ]
        head, middle, tail = find_boundaries(msgs, head_rounds=1, tail_tokens=1000)
        # head_rounds=1 means cut after first user round
        # The first round includes: user q1 + assistant a1 + tool result1
        # Our algorithm cuts at the next user message (q2)
        # But if middle starts at q2, head ends with the tool message
        # This is fine as long as the tool_calls/tool pair stays together in head
        head_roles = [m["role"] for m in head]
        # head should include the assistant.tool_calls + tool pair
        assert "tool" in head_roles or "tool" not in [m["role"] for m in msgs[:5]]


# ---------------------------------------------------------------------------
# Layer 3: summarize_middle
# ---------------------------------------------------------------------------


class TestLayer3:
    def test_empty_middle_returns_none(self):
        result = summarize_middle([], CompressionConfig())
        assert result is None

    def test_aux_llm_unavailable_degraded(self, monkeypatch):
        """辅助 LLM 不可用时返回 None（降级）。"""
        # Mock OpenAI client to raise an exception
        def mock_init(*args, **kwargs):
            raise RuntimeError("Connection refused")

        monkeypatch.setattr("dezhu_agent.compression.OpenAI", mock_init)
        result = summarize_middle(
            [{"role": "user", "content": "test"}],
            CompressionConfig(aux_api_key="sk-test"),
        )
        assert result is None


# ---------------------------------------------------------------------------
# TaskState
# ---------------------------------------------------------------------------


class TestTaskState:
    def setup_method(self):
        reset_task_state()

    def test_initial_state_empty(self):
        state = get_task_state()
        assert state.goal == ""
        assert state.todos == []

    def test_render_with_goal_and_todos(self):
        state = get_task_state()
        state.goal = "读取所有文件"
        state.todos = [
            {"id": "t1", "subject": "列出目录", "status": "completed"},
            {"id": "t2", "subject": "读文件 A", "status": "in_progress"},
            {"id": "t3", "subject": "读文件 B", "status": "pending"},
        ]
        rendered = state.render()
        assert "Task State (live, never compressed)" in rendered
        assert "读取所有文件" in rendered
        assert "[x] (t1) 列出目录" in rendered
        assert "[~] (t2) 读文件 A" in rendered
        assert "[ ] (t3) 读文件 B" in rendered

    def test_render_empty_state(self):
        state = get_task_state()
        rendered = state.render()
        assert "Task State" in rendered
        assert "Goal" not in rendered  # no goal = no Goal section

    def test_reset_clears_state(self):
        state = get_task_state()
        state.goal = "test"
        state.todos = [{"id": "t1", "subject": "x", "status": "completed"}]
        reset_task_state()
        new_state = get_task_state()
        assert new_state.goal == ""
        assert new_state.todos == []


# ---------------------------------------------------------------------------
# compress orchestrator
# ---------------------------------------------------------------------------


class TestCompressOrchestrator:
    def test_below_threshold_noop(self):
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        config = CompressionConfig(trigger_ratio=0.7, window_size=200000)
        result = compress(msgs, config)
        assert result.layers_applied == []
        assert result.after_tokens == result.before_tokens

    def test_layer1_sufficient(self):
        """Layer 1 就能降到阈值以下，跳过 Layer 2/3。"""
        msgs = [
            {"role": "user", "content": "q1"},
            {"role": "tool", "content": "x" * 10000, "tool_call_id": "1"},  # 长输出
        ]
        config = CompressionConfig(
            trigger_ratio=0.00001,  # 极低阈值，强制触发
            window_size=200000,
            max_rounds_layer1=0,  # 裁剪所有轮次的 tool 输出
        )
        result = compress(msgs, config)
        assert 1 in result.layers_applied
        # Layer 1 把 tool 输出裁剪为占位文本后 token 应大幅减少
        assert result.after_tokens < result.before_tokens

    def test_compression_stuck_raises(self):
        """无法降到 90% 以下时抛 CompressionStuckError。"""
        # 头部消息已占大部分——无法有效压缩
        msgs = [
            {"role": "user", "content": "x" * 100000},  # 极长用户消息
        ]
        config = CompressionConfig(
            trigger_ratio=0.00001,
            window_size=200000,
        )
        with pytest.raises(CompressionStuckError):
            compress(msgs, config)

    def test_exception_fallback_to_original(self):
        """未预期异常时回退到原始消息，不丢数据。"""
        msgs = [
            {"role": "user", "content": "test"},
            {"role": "assistant", "content": "response"},
        ]
        # force an unexpected error in Layer 2
        import dezhu_agent.compression as mod

        original_find = mod.find_boundaries

        def broken(*args, **kwargs):
            raise RuntimeError("simulated crash")

        mod.find_boundaries = broken
        try:
            config = CompressionConfig(trigger_ratio=0.00001, window_size=200000)
            result = compress(msgs, config)
            assert result.degraded
            assert result.after_tokens == result.before_tokens
            assert result.layers_applied == []
        finally:
            mod.find_boundaries = original_find

    def test_after_dicts_returned(self):
        """压缩结果中 after_dicts 包含压缩后的消息。"""
        msgs = [
            {"role": "user", "content": "q1"},
            {"role": "tool", "content": "x" * 500, "tool_call_id": "1"},  # 足够长，裁剪后 token 减少
            {"role": "user", "content": "q2"},
            {"role": "assistant", "content": "a2"},
        ]
        config = CompressionConfig(
            trigger_ratio=0.00001,
            window_size=200000,
            max_rounds_layer1=0,  # 裁剪所有 tool 输出
        )
        result = compress(msgs, config)
        assert len(result.after_dicts) > 0
        # 至少有一个消息
        assert any(m["role"] == "user" for m in result.after_dicts)


# ---------------------------------------------------------------------------
# CompressionConfig
# ---------------------------------------------------------------------------


class TestCompressionConfig:
    def test_thresholds_calculated(self):
        config = CompressionConfig(
            trigger_ratio=0.7, preflight_ratio=0.8, window_size=100000
        )
        assert config.trigger_threshold == 70000
        assert config.preflight_threshold == 80000
