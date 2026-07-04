"""核心循环测试 — mock LLM API，覆盖全部验收标准."""

from unittest.mock import patch

import pytest

from dezhu_agent.__main__ import main
from dezhu_agent.llm import LLMResponse, StreamChunk
from dezhu_agent.loop import run_conversation
from dezhu_agent.messages import Message

# --- 辅助函数：构建模拟的 LLM 返回值 ---


def _mock_llm(content, finish_reason, tool_calls=None, reasoning_content=None):
    """模拟 call_llm 的返回值 (LLMResponse)."""
    return LLMResponse(
        content=content,
        finish_reason=finish_reason,
        tool_calls=tool_calls,
        reasoning_content=reasoning_content,
    )


# ==================== 正常路径 ====================


class TestNormalPath:
    """N1-N4: 正常路径验收."""

    @patch("dezhu_agent.loop.call_llm")
    def test_n1_direct_reply(self, mock_call_llm):
        """N1: 用户发送不需要工具的消息，模型直接回复 stop."""
        mock_call_llm.return_value = _mock_llm("你好！有什么可以帮你的？", "stop")

        reply, history, _ = run_conversation("你好")
        assert reply == "你好！有什么可以帮你的？"
        assert len(history) == 2
        assert mock_call_llm.call_count == 1

    @patch("dezhu_agent.loop.call_llm")
    def test_n2_single_tool_call(self, mock_call_llm):
        """N2: 用户触发单次工具调用."""
        mock_call_llm.side_effect = [
            _mock_llm(
                "",
                "tool_calls",
                tool_calls=[
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": '{"path": "pyproject.toml"}',
                        },
                    }
                ],
            ),
            _mock_llm("文件内容是：name = dezhu-agent", "stop"),
        ]

        reply, history, _ = run_conversation("读一下 pyproject.toml")
        assert "name = dezhu-agent" in reply
        roles = [m.role for m in history]
        assert "tool" in roles
        assert mock_call_llm.call_count == 2

    @patch("dezhu_agent.loop.call_llm")
    def test_n3_length_truncation(self, mock_call_llm):
        """N3: 模型输出被截断（length），自动继续调用直到 stop."""
        mock_call_llm.side_effect = [
            _mock_llm("这是第一部分，", "length"),
            _mock_llm("这是第二部分", "stop"),
        ]

        reply, history, _ = run_conversation("写一篇长文章")
        assert "这是第二部分" in reply
        assert mock_call_llm.call_count == 2

    def test_n4_cli_empty_message(self):
        """N4: CLI 入口（通过空消息验证 B1 路径，不依赖 API）."""
        reply, history, _ = run_conversation("")
        assert "消息为空" in reply
        assert reply == "（消息为空，请输入有效内容）"

    @patch("dezhu_agent.__main__.run_conversation")
    @patch("builtins.input")
    def test_n4_cli_interactive(self, mock_input, mock_loop, capsys):
        """N4: CLI 交互式输入，打印最终回复到 stdout."""
        mock_input.side_effect = ["你好", EOFError]
        mock_loop.return_value = ("Hello from agent", [], "test-sid")
        main(argv=["--db-path", ":memory:"])
        captured = capsys.readouterr()
        assert "Hello from agent" in captured.out

    @patch("dezhu_agent.__main__.run_conversation")
    @patch("builtins.input")
    def test_n4_cli_skip_empty(self, mock_input, mock_loop, capsys):
        """N4: CLI 跳过空输入，继续等待有效输入."""
        mock_input.side_effect = ["", "hello", EOFError]
        mock_loop.return_value = ("reply", [], "test-sid")
        main(argv=["--db-path", ":memory:"])
        captured = capsys.readouterr()
        assert "reply" in captured.out


# ==================== 异常路径 ====================


class TestAbnormalPath:
    """E1-E4: 异常路径验收."""

    @patch("dezhu_agent.loop.call_llm")
    def test_e1_api_failure(self, mock_call_llm):
        """E1: API 调用失败，向上抛出异常（fail-fast）."""
        from openai import APIError

        mock_call_llm.side_effect = APIError("Connection failed", request=None, body=None)

        with pytest.raises(APIError, match="Connection failed"):
            run_conversation("你好")

    @patch("dezhu_agent.loop.call_llm")
    def test_e2_tool_not_found(self, mock_call_llm):
        """E2: 模型调用未注册的工具，执行器返回 not found."""
        mock_call_llm.side_effect = [
            _mock_llm(
                "",
                "tool_calls",
                tool_calls=[
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "unknown_tool", "arguments": "{}"},
                    }
                ],
            ),
            _mock_llm("抱歉，我使用了错误的工具", "stop"),
        ]

        reply, history, _ = run_conversation("用 unknown_tool")
        tool_msgs = [m for m in history if m.role == "tool" and m.name == "unknown_tool"]
        assert len(tool_msgs) == 1
        assert "not found" in tool_msgs[0].content

    @patch("dezhu_agent.loop.call_llm")
    def test_e3_tool_exception(self, mock_call_llm):
        """E3: 工具执行时抛出异常，执行器返回错误消息."""
        mock_call_llm.side_effect = [
            _mock_llm(
                "",
                "tool_calls",
                tool_calls=[
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": '{"path": "/nonexistent_dir_xyz/file.txt"}',
                        },
                    }
                ],
            ),
            _mock_llm("文件不存在", "stop"),
        ]

        reply, history, _ = run_conversation("读一个不存在的文件")
        tool_msgs = [m for m in history if m.role == "tool" and m.name == "read_file"]
        assert len(tool_msgs) == 1
        assert "Error" in tool_msgs[0].content or "not found" in tool_msgs[0].content

    @patch("dezhu_agent.loop.call_llm")
    def test_e4_budget_exhausted(self, mock_call_llm):
        """E4: iteration budget 耗尽时停止循环."""
        tool_call = [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "read_file", "arguments": '{"path": "test.txt"}'},
            }
        ]

        def side_effect(*args, **kwargs):
            return _mock_llm("", "tool_calls", tool_calls=tool_call)

        mock_call_llm.side_effect = side_effect

        with patch("dezhu_agent.loop.ITERATION_BUDGET", 3):
            reply, history, _ = run_conversation("反复调用工具")
            assert mock_call_llm.call_count == 3


# ==================== 边界条件 ====================


class TestBoundary:
    """B1-B4: 边界条件验收."""

    def test_b1_empty_message(self):
        """B1: 空消息跳过 API 调用，直接返回提示."""
        reply, history, _ = run_conversation("")
        assert reply == "（消息为空，请输入有效内容）"

        reply, history, _ = run_conversation("   ")
        assert reply == "（消息为空，请输入有效内容）"

    @patch("dezhu_agent.loop.call_llm")
    def test_b2_internal_fields_stripped(self, mock_call_llm):
        """B2: 包含内部字段的消息在 API 调用前被清洗."""
        history = [
            Message(role="user", content="hello", reasoning="这是内部推理"),
            Message(role="assistant", content="hi", _internal={"token_count": 42}),
        ]
        mock_call_llm.return_value = _mock_llm("world", "stop")

        reply, new_history, _ = run_conversation("next", history=history)

        call_args, call_kwargs = mock_call_llm.call_args
        api_messages = call_args[0]
        for msg in api_messages:
            assert "reasoning" not in msg, f"reasoning should be stripped: {msg}"
            assert "_internal" not in msg, f"_internal should be stripped: {msg}"
        assert reply == "world"

    @patch("dezhu_agent.loop.call_llm")
    def test_b3_multiple_tool_calls(self, mock_call_llm):
        """B3: 单次 API 返回多个 tool_calls，全部执行后合并结果."""
        mock_call_llm.side_effect = [
            _mock_llm(
                "",
                "tool_calls",
                tool_calls=[
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": '{"path": "pyproject.toml"}',
                        },
                    },
                    {
                        "id": "call_2",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": '{"path": "README.md"}',
                        },
                    },
                ],
            ),
            _mock_llm("两个文件都读完了", "stop"),
        ]

        reply, history, _ = run_conversation("读两个文件")
        tool_msgs = [m for m in history if m.role == "tool"]
        assert len(tool_msgs) == 2
        assert mock_call_llm.call_count == 2

    @patch("dezhu_agent.loop.call_llm")
    def test_b4_tools_passed_via_api_param(self, mock_call_llm):
        """B4: 工具通过 API tools 参数传递，不写入 system prompt 文本."""
        mock_call_llm.return_value = _mock_llm("好的", "stop")

        def capture_call(*args, **kwargs):
            assert "tools" in kwargs
            assert len(kwargs["tools"]) == 2
            messages = args[0] if args else kwargs.get("messages", [])
            assert messages[0]["role"] == "system"
            assert "DeZhu Agent" in messages[0]["content"]
            # 工具不应出现在 system prompt 文本中
            assert "可用工具" not in messages[0]["content"]
            return _mock_llm("好的", "stop")

        mock_call_llm.side_effect = capture_call

        reply, history, _ = run_conversation("你好")
        assert reply == "好的"


# ==================== 思考模式（DeepSeek）====================


class TestThinkingMode:
    """T1-T4: 思考模式验收."""

    @patch("dezhu_agent.loop.call_llm")
    def test_t1_reasoning_content_in_message(self, mock_call_llm):
        """T1: 思考模式正常回复，reasoning_content 出现在 Message 中."""
        mock_call_llm.return_value = _mock_llm(
            "答案是 9.11", "stop", reasoning_content="让我想想..."
        )

        with patch("dezhu_agent.config.THINKING_ENABLED", True):
            reply, history, _ = run_conversation("9.11 和 9.8 哪个大？")

        # 最后一条 assistant 消息应包含 reasoning_content
        last = history[-1]
        assert last.role == "assistant"
        assert last.reasoning_content == "让我想想..."
        assert last.content == "答案是 9.11"
        assert reply == "答案是 9.11"

    @patch("dezhu_agent.loop.call_llm")
    def test_t2_reasoning_with_tool_call(self, mock_call_llm):
        """T2: 思考模式 + 工具调用，reasoning_content 随 tool_calls 回传到 API."""
        reasoning = "用户想读文件，我先调用 read_file"

        mock_call_llm.side_effect = [
            _mock_llm(
                "",
                "tool_calls",
                tool_calls=[
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": '{"path": "test.txt"}',
                        },
                    }
                ],
                reasoning_content=reasoning,
            ),
            _mock_llm("文件内容已读取", "stop"),
        ]

        with patch("dezhu_agent.config.THINKING_ENABLED", True):
            reply, history, _ = run_conversation("读 test.txt")

        # 第一条 assistant 应有 reasoning_content
        assistant_msgs = [m for m in history if m.role == "assistant"]
        assert assistant_msgs[0].reasoning_content == reasoning
        # 验证 to_api_dict 包含 reasoning_content（因为有 tool_calls）
        api_dict = assistant_msgs[0].to_api_dict()
        assert "reasoning_content" in api_dict
        assert api_dict["reasoning_content"] == reasoning

    @patch("dezhu_agent.loop.call_llm")
    def test_t3_reasoning_disabled(self, mock_call_llm):
        """T3: 思考模式关闭时，reasoning_content 为 None."""
        mock_call_llm.return_value = _mock_llm("普通回复", "stop", reasoning_content=None)

        with patch("dezhu_agent.config.THINKING_ENABLED", False):
            reply, history, _ = run_conversation("你好")

        last = history[-1]
        assert last.reasoning_content is None
        assert reply == "普通回复"

    @patch("dezhu_agent.loop.call_llm")
    def test_t4_no_tool_calls_reasoning_not_sent(self, mock_call_llm):
        """T4: 未进行工具调用时，reasoning_content 不被回传到 API."""
        mock_call_llm.side_effect = [
            _mock_llm(
                "让我想一下",
                "stop",
                reasoning_content="我在思考这个问题",
            ),
        ]

        # 第二轮对话，历史中有 reasoning 但无需回传
        history = [
            Message(role="user", content="9.11 和 9.8 哪个大？"),
            Message(
                role="assistant",
                content="9.11 更大",
                reasoning_content="我在思考...",
            ),
        ]

        def capture_call(*args, **kwargs):
            msgs = args[0]
            # 检查传给 API 的消息中没有 reasoning_content（因无 tool_calls）
            for msg in msgs:
                if msg.get("role") == "assistant":
                    assert "reasoning_content" not in msg
            return _mock_llm("好的", "stop")

        mock_call_llm.side_effect = capture_call

        with patch("dezhu_agent.config.THINKING_ENABLED", True):
            reply, history, _ = run_conversation("另一个问题", history=history)


# ==================== 流式输出 ====================


class TestStreaming:
    """S1-S3: 流式输出验收."""

    @patch("dezhu_agent.loop.call_llm_stream")
    @patch("dezhu_agent.loop.STREAM_MODE", True)
    def test_s1_stream_chunks_received(self, mock_stream):
        """S1: 流式输出正常，on_stream_chunk 逐块收到内容."""
        chunks = [
            StreamChunk(content_delta="你好"),
            StreamChunk(content_delta="，"),
            StreamChunk(content_delta="世界", finish_reason="stop"),
        ]

        # 模拟 generator 的 StopIteration.value 行为
        def _gen():
            for c in chunks:  # noqa: UP028
                yield c
            return _mock_llm("你好，世界", "stop")

        mock_stream.side_effect = lambda *a, **kw: _gen()

        received = []

        def on_chunk(chunk):
            received.append(chunk)

        reply, history, _ = run_conversation("hi", on_stream_chunk=on_chunk)

        assert len(received) == 3
        assert received[0].content_delta == "你好"
        assert received[2].content_delta == "世界"
        assert reply == "你好，世界"

    @patch("dezhu_agent.loop.call_llm_stream")
    @patch("dezhu_agent.loop.STREAM_MODE", True)
    def test_s2_stream_with_reasoning(self, mock_stream):
        """S2: 流式 + 思考模式，reasoning_delta 和 content_delta 交替出现."""
        chunks = [
            StreamChunk(reasoning_delta="让我"),
            StreamChunk(reasoning_delta="想想"),
            StreamChunk(content_delta="答案是"),
            StreamChunk(content_delta="42", finish_reason="stop"),
        ]

        def _gen():
            for c in chunks:  # noqa: UP028
                yield c
            return _mock_llm("答案是42", "stop", reasoning_content="让我想想")

        mock_stream.side_effect = lambda *a, **kw: _gen()

        reasoning_parts = []
        content_parts = []

        def on_chunk(chunk):
            if chunk.reasoning_delta:
                reasoning_parts.append(chunk.reasoning_delta)
            if chunk.content_delta:
                content_parts.append(chunk.content_delta)

        reply, history, _ = run_conversation("宇宙的答案", on_stream_chunk=on_chunk)

        assert reasoning_parts == ["让我", "想想"]
        assert content_parts == ["答案是", "42"]
        assert reply == "答案是42"

    @patch("dezhu_agent.loop.call_llm")
    def test_s3_stream_disabled(self, mock_call_llm):
        """S3: 流式模式关闭时，调用 call_llm 而非 call_llm_stream."""
        mock_call_llm.return_value = _mock_llm("普通回复", "stop")

        with patch("dezhu_agent.loop.STREAM_MODE", False):
            reply, history, _ = run_conversation("你好")
        assert reply == "普通回复"
        # 不应启动流式路径
        assert mock_call_llm.call_count == 1


# ==================== 缓存统计 ====================


class TestCacheStats:
    """C1-C2: DeepSeek 硬盘缓存统计验收."""

    @patch("dezhu_agent.loop.call_llm")
    def test_c1_cache_tokens_in_response(self, mock_call_llm):
        """C1: LLMResponse 正确传递缓存 token 数."""
        from dezhu_agent.llm import LLMResponse

        mock_call_llm.return_value = LLMResponse(
            content="回复",
            finish_reason="stop",
            tool_calls=None,
            prompt_cache_hit_tokens=100,
            prompt_cache_miss_tokens=50,
        )

        reply, history, _ = run_conversation("你好")
        last = history[-1]
        assert last.prompt_cache_hit_tokens == 100
        assert last.prompt_cache_miss_tokens == 50

    @patch("dezhu_agent.loop.call_llm")
    def test_c2_cache_tokens_default_zero(self, mock_call_llm):
        """C2: 无缓存信息时默认为 0."""
        mock_call_llm.return_value = _mock_llm("回复", "stop")

        reply, history, _ = run_conversation("你好")
        last = history[-1]
        assert last.prompt_cache_hit_tokens == 0
        assert last.prompt_cache_miss_tokens == 0

    @patch("dezhu_agent.loop.call_llm_stream")
    @patch("dezhu_agent.loop.STREAM_MODE", True)
    def test_c3_stream_cache_tokens(self, mock_stream):
        """C3: 流式模式下 StreamChunk 携带缓存统计."""
        from dezhu_agent.llm import LLMResponse, StreamChunk

        def _gen():
            yield StreamChunk(content_delta="hi")
            yield StreamChunk(
                content_delta=" all",
                finish_reason="stop",
                prompt_cache_hit_tokens=200,
                prompt_cache_miss_tokens=30,
            )
            return LLMResponse(
                content="hi all",
                finish_reason="stop",
                tool_calls=None,
                prompt_cache_hit_tokens=200,
                prompt_cache_miss_tokens=30,
            )

        mock_stream.side_effect = lambda *a, **kw: _gen()

        last_chunk = [None]

        def on_chunk(c):
            last_chunk[0] = c

        reply, history, _ = run_conversation("hi", on_stream_chunk=on_chunk)
        assert last_chunk[0].prompt_cache_hit_tokens == 200
        assert last_chunk[0].prompt_cache_miss_tokens == 30


# ==================== 持久化集成 ====================


class TestPersistence:
    """P1-P4: 持久化集成验收."""

    @patch("dezhu_agent.loop.call_llm")
    def test_p1_storage_none_unchanged(self, mock_call_llm):
        """P1: storage=None 时现有行为完全不变."""
        mock_call_llm.return_value = _mock_llm("Hello", "stop")

        reply, history, _ = run_conversation("hi", storage=None)
        assert reply == "Hello"
        assert len(history) == 2

    @patch("dezhu_agent.loop.call_llm")
    def test_p2_save_messages_called_on_stop(self, mock_call_llm):
        """P2: finish_reason=stop 时调用 storage.save_messages."""
        from unittest.mock import MagicMock

        mock_call_llm.return_value = _mock_llm("done", "stop")
        mock_storage = MagicMock()
        mock_storage.save_messages = MagicMock()

        reply, history, _ = run_conversation(
            "hi", storage=mock_storage, session_id="test-sid"
        )
        assert reply == "done"
        mock_storage.save_messages.assert_called_once()
        call_args = mock_storage.save_messages.call_args
        assert call_args[0][0] == "test-sid"
        # 新增了 2 条消息：user + assistant
        assert len(call_args[0][1]) == 2

    @patch("dezhu_agent.loop.call_llm")
    def test_p3_save_messages_called_on_budget_exhausted(self, mock_call_llm):
        """P3: budget 耗尽时仍然调用 storage.save_messages."""
        from unittest.mock import MagicMock

        mock_storage = MagicMock()
        mock_storage.save_messages = MagicMock()

        tool_call = [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "read_file", "arguments": '{"path": "test.txt"}'},
            }
        ]
        mock_call_llm.side_effect = lambda *a, **kw: _mock_llm(
            "", "tool_calls", tool_calls=tool_call
        )

        with patch("dezhu_agent.loop.ITERATION_BUDGET", 2):
            reply, history, _ = run_conversation(
                "looping", storage=mock_storage, session_id="test-sid"
            )

        mock_storage.save_messages.assert_called_once()

    @patch("dezhu_agent.loop.call_llm")
    def test_p4_internal_fields_not_in_serialized(self, mock_call_llm):
        """P4: _internal 和 reasoning 不出现在持久化数据中."""
        from unittest.mock import MagicMock

        mock_call_llm.return_value = _mock_llm("ok", "stop")
        mock_storage = MagicMock()
        mock_storage.save_messages = MagicMock()

        history = [
            Message(role="user", content="hi", reasoning="hidden", _internal={"x": 1}),
        ]
        reply, new_history, _ = run_conversation(
            "next",
            history=history,
            storage=mock_storage,
            session_id="test-sid",
        )

        # 验证存储的序列化数据不包含内部字段
        from dezhu_agent.storage import _message_to_row

        for msg in new_history:
            row = _message_to_row(msg, "sid", 0, "now")
            assert "reasoning" not in row
            assert "_internal" not in row

    @patch("dezhu_agent.loop.call_llm")
    @patch("dezhu_agent.loop.build_system_prompt")
    def test_p5_system_prompt_saved_on_new_session(
        self, mock_build_sp, mock_call_llm
    ):
        """P5: 新会话时 system_prompt 被组装并持久化到 storage."""
        from unittest.mock import MagicMock

        mock_call_llm.return_value = _mock_llm("ok", "stop")
        mock_build_sp.return_value = "assembled prompt"
        mock_storage = MagicMock()
        mock_storage.load_system_prompt.return_value = ""  # 新会话，无已有 prompt

        run_conversation("hello", storage=mock_storage, session_id="new-sid")

        # 验证：调用了 load_system_prompt 检查已有 prompt
        mock_storage.load_system_prompt.assert_called_once_with("new-sid")
        # 验证：组装后调用了 save_system_prompt 持久化
        mock_storage.save_system_prompt.assert_called_once_with("new-sid", "assembled prompt")

    @patch("dezhu_agent.loop.call_llm")
    @patch("dezhu_agent.loop.build_system_prompt")
    def test_p6_system_prompt_reused_on_continue(
        self, mock_build_sp, mock_call_llm
    ):
        """P6: --continue 恢复会话时复用已有 system_prompt，不重新组装."""
        from unittest.mock import MagicMock

        mock_call_llm.return_value = _mock_llm("ok", "stop")
        mock_storage = MagicMock()
        mock_storage.load_system_prompt.return_value = "cached prompt"  # 已有

        run_conversation("hello", storage=mock_storage, session_id="existing-sid")

        # 验证：调用了 load_system_prompt
        mock_storage.load_system_prompt.assert_called_once_with("existing-sid")
        # 验证：build_system_prompt 没有被调用（复用已有）
        mock_build_sp.assert_not_called()
        # 验证：save_system_prompt 没有被调用（不需要重新保存）
        mock_storage.save_system_prompt.assert_not_called()
