"""prompt_assembler 测试 — 覆盖 spec 第 5 节全部组装相关验收."""

import os

from dezhu_agent.prompt_assembler import (
    SYSTEM_PROMPT_TEMPLATE,
    _load_agents,
    _load_soul,
    _read_file,
    assemble_system_prompt,
)

# ---- SOUL.md 测试 ----


class TestSoulLoading:
    """人设文件加载."""

    def test_soul_exists_with_content_replaces_template(self, tmp_path, monkeypatch):
        """SOUL.md 存在且非空 → 内容替代 SYSTEM_PROMPT_TEMPLATE."""
        soul_file = tmp_path / "SOUL.md"
        soul_file.write_text("我是自定义人设", encoding="utf-8")
        monkeypatch.setattr(
            "dezhu_agent.prompt_assembler._SOUL_PATH", soul_file
        )
        result = assemble_system_prompt()
        assert "我是自定义人设" in result
        assert result.startswith("我是自定义人设")

    def test_soul_not_exists_uses_template(self):
        """SOUL.md 不存在 → 使用 SYSTEM_PROMPT_TEMPLATE."""
        # 默认路径 ~/.hermes/SOUL.md 通常不存在
        result = _load_soul()
        assert result == SYSTEM_PROMPT_TEMPLATE

    def test_soul_exists_but_empty_uses_template(self, tmp_path, monkeypatch):
        """SOUL.md 存在但为空 → 使用 SYSTEM_PROMPT_TEMPLATE."""
        soul_file = tmp_path / "SOUL.md"
        soul_file.write_text("", encoding="utf-8")
        monkeypatch.setattr(
            "dezhu_agent.prompt_assembler._SOUL_PATH", soul_file
        )
        result = assemble_system_prompt()
        assert "DeZhu Agent" in result  # 来自默认模板

    def test_soul_utf8_bom_handled(self, tmp_path, monkeypatch):
        """SOUL.md 使用 UTF-8 BOM → 正确处理，不拼入 BOM 字节."""
        soul_file = tmp_path / "SOUL.md"
        content = "BOM 测试"
        soul_file.write_bytes(b"\xef\xbb\xbf" + content.encode("utf-8"))
        monkeypatch.setattr(
            "dezhu_agent.prompt_assembler._SOUL_PATH", soul_file
        )
        result = _read_file(soul_file)
        assert result == content
        assert "\ufeff" not in result

    def test_crlf_preserved(self, tmp_path):
        """\\r\\n 换行符保持原样，不转换为 \\n."""
        f = tmp_path / "test.md"
        f.write_bytes(b"line1\r\nline2\r\n")
        result = _read_file(f)
        assert result == "line1\r\nline2\r\n"
        assert "\r\n" in result


# ---- AGENTS.md 测试 ----


class TestAgentsLoading:
    """项目规则文件加载."""

    def test_agents_exists_appended_to_prompt(self, tmp_path, monkeypatch):
        """AGENTS.md 存在 → 拼入 system prompt（在 SOUL.md 之后）."""
        agents_file = tmp_path / "AGENTS.md"
        agents_file.write_text("项目规则内容", encoding="utf-8")
        monkeypatch.setattr(
            "dezhu_agent.prompt_assembler.PROJECT_DIR", tmp_path
        )
        result = assemble_system_prompt()
        assert "项目规则内容" in result
        # 验证顺序：SOUL 在前，AGENTS 在后
        soul_pos = result.index("DeZhu Agent")
        agents_pos = result.index("项目规则内容")
        assert soul_pos < agents_pos

    def test_agents_not_exists_silently_skipped(self, tmp_path, monkeypatch):
        """AGENTS.md 不存在 → 静默跳过."""
        monkeypatch.setattr(
            "dezhu_agent.prompt_assembler.PROJECT_DIR", tmp_path
        )
        result = _load_agents()
        assert result == ""

    def test_agents_exceeds_limit_truncated(self, tmp_path, monkeypatch):
        """AGENTS.md > 2000 字符 → 截断 + 提示."""
        agents_file = tmp_path / "AGENTS.md"
        long_content = "A" * 2500
        agents_file.write_text(long_content, encoding="utf-8")
        monkeypatch.setattr(
            "dezhu_agent.prompt_assembler.PROJECT_DIR", tmp_path
        )
        result = _load_agents()
        assert len(result) < 2500
        assert "已截断" in result
        assert result.startswith("A" * 2000)


# ---- 组装行为 ----


class TestAssembly:
    """整体组装."""

    def test_no_external_files_outputs_template_only(self, tmp_path, monkeypatch):
        """无 SOUL.md、无 AGENTS.md → 输出仅 SYSTEM_PROMPT_TEMPLATE."""
        monkeypatch.setattr(
            "dezhu_agent.prompt_assembler.PROJECT_DIR", tmp_path
        )
        result = assemble_system_prompt()
        assert result == SYSTEM_PROMPT_TEMPLATE

    def test_backward_compatible_with_current_template(self):
        """向后兼容：不提供任何外部文件时，输出与当前硬编码一致."""
        result = assemble_system_prompt()
        assert "DeZhu Agent" in result
        assert "工具使用规则" in result
        # 工具不应出现在 prompt 文本中
        assert "可用工具" not in result

    def test_tools_not_written_to_prompt_text(self, tmp_path, monkeypatch):
        """工具定义不写入 system prompt 文本（即使 tools 非空）."""
        monkeypatch.setattr(
            "dezhu_agent.prompt_assembler.PROJECT_DIR", tmp_path
        )
        tools = [{"name": "test_tool", "description": "a test", "parameters": {}}]
        result = assemble_system_prompt(tools)
        assert "test_tool" not in result

    def test_special_characters_preserved(self, tmp_path, monkeypatch):
        """特殊字符 { } \" \\n 在组装后保持原样."""
        soul_file = tmp_path / "SOUL.md"
        soul_file.write_text('{"key": "value"}\nspecial: { braces }', encoding="utf-8")
        monkeypatch.setattr(
            "dezhu_agent.prompt_assembler._SOUL_PATH", soul_file
        )
        monkeypatch.setattr(
            "dezhu_agent.prompt_assembler.PROJECT_DIR", tmp_path
        )
        result = assemble_system_prompt()
        assert '{"key": "value"}' in result
        assert "{ braces }" in result


# ---- Debug 输出 ----


class TestDebug:
    """Debug 级别日志可观测性（原 DEZHU_DEBUG env，现改用日志级别控制）."""

    def test_debug_enabled_outputs_to_log(self, tmp_path, monkeypatch, caplog):
        """DEBUG 级别 → 日志中包含组装来源信息."""
        import logging

        monkeypatch.setattr(
            "dezhu_agent.prompt_assembler.PROJECT_DIR", tmp_path
        )
        caplog.set_level(logging.DEBUG, logger="dezhu_agent.prompt_assembler")
        assemble_system_prompt()
        assert "System Prompt 组装来源" in caplog.text
        assert "人设(SOUL):" in caplog.text
        assert "项目规则(AGENTS):" in caplog.text

    def test_info_level_no_debug_output(self, tmp_path, monkeypatch, caplog):
        """INFO 级别 → 日志中无 Debug 输出."""
        import logging

        monkeypatch.setattr(
            "dezhu_agent.prompt_assembler.PROJECT_DIR", tmp_path
        )
        caplog.set_level(logging.INFO, logger="dezhu_agent.prompt_assembler")
        assemble_system_prompt()
        assert "System Prompt 组装来源" not in caplog.text

    def test_debug_output_shows_byte_count_and_preview_truncation(
        self, tmp_path, monkeypatch, caplog
    ):
        """Debug 输出包含真实字节数和 120 字符预览截断."""
        import logging

        soul_file = tmp_path / "SOUL.md"
        # 中文内容：每字符 3 字节，120+ 字符触发截断
        long_content = "人设" * 80  # 160 chars, 480 bytes
        soul_file.write_text(long_content, encoding="utf-8")
        monkeypatch.setattr(
            "dezhu_agent.prompt_assembler._SOUL_PATH", soul_file
        )
        monkeypatch.setattr(
            "dezhu_agent.prompt_assembler.PROJECT_DIR", tmp_path
        )
        caplog.set_level(logging.DEBUG, logger="dezhu_agent.prompt_assembler")
        assemble_system_prompt()
        # 验证字节数（中文每字符 3 字节 → 480）
        assert "480 字节" in caplog.text
        # 验证预览截断至 120 字符：完整内容 160 字符，不应全部出现
        assert long_content not in caplog.text
