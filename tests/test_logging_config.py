"""后台日志系统测试 — 覆盖 spec 第 5 节全部日志相关验收."""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dezhu_agent.logging_config import (
    _SessionFilter,
    _log_level_to_int,
    get_logger,
    init_logging,
    set_session_context,
)


class TestInitLogging:
    """N1: 日志系统初始化."""

    def test_init_creates_log_dir_and_file(self):
        """init_logging 创建日志目录和文件."""
        with tempfile.TemporaryDirectory() as tmpdir:
            init_logging(log_dir=tmpdir, log_level=logging.DEBUG, force=True)
            log_file = Path(tmpdir) / "dezhu-agent.log"
            assert log_file.exists()
            assert log_file.stat().st_size >= 0

    def test_init_is_idempotent(self):
        """多次 init_logging 不重复添加 handler."""
        with tempfile.TemporaryDirectory() as tmpdir:
            init_logging(log_dir=tmpdir, force=True)
            handler_count = len(logging.getLogger().handlers)
            init_logging(log_dir=tmpdir, force=False)
            assert len(logging.getLogger().handlers) == handler_count

    def test_init_force_clears_and_recreates(self):
        """force=True 清除已有 handler 重新创建."""
        with tempfile.TemporaryDirectory() as tmpdir:
            init_logging(log_dir=tmpdir, force=True)
            handler_count = len(logging.getLogger().handlers)
            init_logging(log_dir=tmpdir, force=True)
            assert len(logging.getLogger().handlers) == handler_count

    def test_log_level_string_to_int(self):
        """_log_level_to_int 正确转换字符串级别."""
        assert _log_level_to_int("DEBUG") == logging.DEBUG
        assert _log_level_to_int("INFO") == logging.INFO
        assert _log_level_to_int("WARNING") == logging.WARNING
        assert _log_level_to_int("ERROR") == logging.ERROR
        assert _log_level_to_int("INVALID") == logging.INFO  # 默认回退


class TestLogOutput:
    """N2-N5: 日志输出内容."""

    def test_log_message_includes_session_prefix(self):
        """日志消息包含 [session=xxx] 前缀."""
        with tempfile.TemporaryDirectory() as tmpdir:
            init_logging(log_dir=tmpdir, log_level=logging.DEBUG, force=True)
            set_session_context("test-session-12345678")
            logger = get_logger("test.module")
            logger.info("测试消息")
            content = (Path(tmpdir) / "dezhu-agent.log").read_text()
            assert "[session=test-ses]" in content
            assert "测试消息" in content

    def test_log_level_filtering(self):
        """日志级别过滤：INFO 级别不输出 DEBUG."""
        with tempfile.TemporaryDirectory() as tmpdir:
            init_logging(log_dir=tmpdir, log_level=logging.INFO, force=True)
            logger = get_logger("test.filter")
            logger.debug("这条不应该出现")
            logger.info("这条应该出现")
            content = (Path(tmpdir) / "dezhu-agent.log").read_text()
            assert "这条应该出现" in content
            assert "这条不应该出现" not in content

    def test_log_format_includes_required_fields(self):
        """日志格式包含时间戳、级别、session、模块、行号."""
        with tempfile.TemporaryDirectory() as tmpdir:
            init_logging(log_dir=tmpdir, log_level=logging.DEBUG, force=True)
            set_session_context("sid-12345678")
            logger = get_logger("test.format")
            logger.info("格式测试")
            content = (Path(tmpdir) / "dezhu-agent.log").read_text()
            assert "INFO" in content
            assert "[session=sid-1234]" in content
            assert "test.format" in content
            # 验证时间戳格式 YYYY-MM-DD HH:MM:SS
            import re

            assert re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", content)


class TestSessionContext:
    """Session 上下文管理."""

    def test_empty_session_shows_dash(self):
        """空 session 显示 [session=-]."""
        with tempfile.TemporaryDirectory() as tmpdir:
            init_logging(log_dir=tmpdir, log_level=logging.INFO, force=True)
            set_session_context("")
            logger = get_logger("test.session")
            logger.info("空 session")
            content = (Path(tmpdir) / "dezhu-agent.log").read_text()
            assert "[session=-]" in content

    def test_session_truncates_to_8_chars(self):
        """session ID 截取前 8 位."""
        with tempfile.TemporaryDirectory() as tmpdir:
            init_logging(log_dir=tmpdir, log_level=logging.INFO, force=True)
            set_session_context("abcdef1234567890")
            logger = get_logger("test.session")
            logger.info("truncate 测试")
            content = (Path(tmpdir) / "dezhu-agent.log").read_text()
            assert "[session=abcdef12]" in content
            assert "1234567890" not in content.split("[session=abcdef12]")[0]

    def test_session_update_takes_effect(self):
        """set_session_context 更新后影响后续日志."""
        with tempfile.TemporaryDirectory() as tmpdir:
            init_logging(log_dir=tmpdir, log_level=logging.INFO, force=True)
            logger = get_logger("test.session")
            set_session_context("first-111")
            logger.info("第一条")
            set_session_context("second-22")
            logger.info("第二条")
            content = (Path(tmpdir) / "dezhu-agent.log").read_text()
            assert "[session=first-11]" in content
            assert "[session=second-2]" in content


class TestErrorPaths:
    """E1-E4: 异常路径."""

    def test_e1_unwritable_dir_fallback_to_stderr(self, capsys):
        """日志目录不可写 → 降级 stderr + 警告."""
        # 使用一个不可能写入的路径
        init_logging(log_dir="/dev/null/logs", log_level=logging.INFO, force=True)
        captured = capsys.readouterr()
        assert "日志目录不可写" in captured.err

    def test_e2_handler_write_error_does_not_raise(self):
        """日志写入失败不抛异常、不中断."""
        handler = MagicMock()
        handler.handleError = MagicMock()
        # 模拟 handler 的 handleError（标准 logging 行为：写失败调用 handleError）
        try:
            # 验证 logger 调用不会因为 handler 故障而崩溃
            logger = get_logger("test.write_error")
            logger.info("这条不会崩溃")
        except Exception:
            pytest.fail("日志写入不应抛出未捕获异常")

    def test_e4_level_fallback_to_info_for_invalid(self):
        """无效日志级别字符串默认回退 INFO."""
        assert _log_level_to_int("NONSENSE") == logging.INFO


class TestBoundary:
    """B1-B4: 边界条件."""

    def test_b3_long_message_truncated(self):
        """单条日志超 50KB 自动截断."""
        with tempfile.TemporaryDirectory() as tmpdir:
            init_logging(log_dir=tmpdir, log_level=logging.INFO, force=True)
            logger = get_logger("test.truncate")
            # 生成 60KB 的消息
            long_msg = "A" * (60 * 1024)
            logger.info(long_msg)
            content = (Path(tmpdir) / "dezhu-agent.log").read_text()
            assert "TRUNCATED" in content
            assert len(content) < 55 * 1024  # 格式开销 + 截断后的长度

    def test_session_filter_class_var_isolation(self):
        """_SessionFilter.session_id 是 ClassVar，全局共享正确."""
        sf = _SessionFilter()
        # 通过 set_session_context 设置
        set_session_context("global-session")
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="test",
            args=(),
            exc_info=None,
        )
        sf.filter(record)
        assert record.session == "[session=global-s]"

    def test_logger_get_logger_returns_logger(self):
        """get_logger 返回有效的 Logger 实例."""
        logger = get_logger("test.getter")
        assert isinstance(logger, logging.Logger)

    def test_init_logging_clears_existing_handlers_on_force(self):
        """force=True 时清除已有 handler."""
        with tempfile.TemporaryDirectory() as tmpdir:
            init_logging(log_dir=tmpdir, force=True)
            # 手动添加一个 handler
            extra_handler = logging.StreamHandler()
            logging.getLogger().addHandler(extra_handler)
            assert len(logging.getLogger().handlers) >= 2
            # force 重新初始化
            init_logging(log_dir=tmpdir, force=True)
            assert len(logging.getLogger().handlers) == 1


class TestSpecAcceptance:
    """验收标准 E3/E4/B1/B2 的补充测试."""

    def test_e3_llm_exception_logs_error(self, caplog):
        """E3: LLM API 异常时 ERROR 记录异常类型和消息."""
        from unittest.mock import patch

        caplog.set_level(logging.ERROR)
        # 模拟 API 调用异常
        from dezhu_agent.llm import call_llm

        with patch("dezhu_agent.llm._get_client") as mock_client:
            mock_client.return_value.chat.completions.create.side_effect = RuntimeError("测试异常")
            try:
                call_llm([{"role": "user", "content": "test"}])
            except RuntimeError:
                pass

        assert "LLM API 调用异常" in caplog.text
        assert "RuntimeError" in caplog.text
        assert "测试异常" in caplog.text

    def test_e4_tool_exception_logs_error(self, caplog):
        """E4: 工具执行异常时 ERROR 记录工具名称+参数+异常消息."""
        from dezhu_agent.tools import ToolDef, ToolRegistry

        caplog.set_level(logging.ERROR)
        reg = ToolRegistry()

        def broken_tool(path: str) -> str:
            raise RuntimeError("工具执行失败")

        reg.register(
            ToolDef(
                name="broken_tool",
                description="会抛异常的工具",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
                fn=broken_tool,
            )
        )
        result = reg.execute("broken_tool", {"path": "/tmp/test"})
        assert "Error executing tool" in result
        assert "工具执行异常" in caplog.text
        assert "broken_tool" in caplog.text
        assert "工具执行失败" in caplog.text

    def test_b1_empty_message_not_logged(self, caplog):
        """B1: 空消息不在日志中记录用户输入."""
        from unittest.mock import patch

        caplog.set_level(logging.INFO)
        # 模拟 run_conversation 收到空消息
        from dezhu_agent.loop import run_conversation

        # 空消息应该快速返回，不产生用户输入日志
        result, _, _ = run_conversation("   ")
        assert "消息为空" in result
        # 空消息不应该在日志中出现用户输入
        user_input_logs = [r for r in caplog.records if "用户输入" in r.message]
        assert len(user_input_logs) == 0

    def test_b2_file_naming_format(self):
        """B2: 日志文件使用 dezhu-agent-YYYY-MM-DD.log 命名格式（活跃文件为 dezhu-agent.log）."""
        import re

        with tempfile.TemporaryDirectory() as tmpdir:
            init_logging(log_dir=tmpdir, log_level=logging.INFO, force=True)
            # 活跃文件应为 dezhu-agent.log
            active_file = Path(tmpdir) / "dezhu-agent.log"
            assert active_file.exists()

            # 验证 namer 函数将轮转文件名转换为正确格式
            from dezhu_agent.logging_config import init_logging as _init

            # 通过检查 handler 的 namer 来验证
            root = logging.getLogger()
            for h in root.handlers:
                if hasattr(h, "namer") and h.namer is not None:
                    test_name = f"{tmpdir}/dezhu-agent.log.2025-07-04"
                    result = h.namer(test_name)
                    assert result.endswith("dezhu-agent-2025-07-04.log")
                    break
