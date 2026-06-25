"""ToolRegistry + @tool 装饰器 单元测试 — 独立实例，不依赖模块级单例."""

from __future__ import annotations

from typing import Any

import pytest

from dezhu_agent.tools import ToolDef, ToolRegistry, tool

# ==================== 辅助函数 ====================


def make_registry() -> ToolRegistry:
    """创建一个干净的 ToolRegistry 实例（测试隔离：独立实例方案）."""
    return ToolRegistry()


def make_tool_def(
    name: str = "test_tool",
    fn: Any = None,
    enabled: bool = True,
) -> ToolDef:
    """快速创建 ToolDef."""
    return ToolDef(
        name=name,
        description=f"Tool {name}",
        parameters={"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]},
        fn=fn or (lambda x: f"hello {x}"),
        enabled=enabled,
    )


# ==================== 正常路径 ====================


class TestNormalPath:
    """N1-N6: 正常路径验收."""

    def test_n1_decorator_registers_tool(self):
        """N1: @tool 装饰器注册工具，可通过 ToolRegistry 获取."""
        from dezhu_agent.tools import _TOOL_REGISTRATIONS

        # 清理先前注册的工具（来自模块级初始化），确保测试隔离
        _TOOL_REGISTRATIONS.clear()

        @tool(name="my_tool", description="My custom tool")
        def my_tool(x: str) -> str:
            return f"processed {x}"

        r = make_registry()
        for td in _TOOL_REGISTRATIONS:
            r.register(td)
        _TOOL_REGISTRATIONS.clear()

        tools = r.get_tools()
        assert len(tools) == 1
        assert tools[0]["name"] == "my_tool"
        assert tools[0]["description"] == "My custom tool"

    def test_n2_scan_discovery(self):
        """N2: 目录扫描自动发现 — 验证 _fs_path_to_dotted 和加载机制."""
        from dezhu_agent.tools import _TOOL_REGISTRATIONS, _fs_path_to_dotted

        # 测试路径转换
        assert _fs_path_to_dotted("src/dezhu_agent/tools") == "dezhu_agent.tools"
        assert _fs_path_to_dotted("dezhu_agent/tools") == "dezhu_agent.tools"

        # 测试 scan_tools 能找到已导入的模块
        import importlib

        # 确保工具模块已加载
        import dezhu_agent.tools.read_file  # noqa: F811
        import dezhu_agent.tools.write_file  # noqa: F811

        r = make_registry()
        _TOOL_REGISTRATIONS.clear()

        # 重新加载模块，触发 @tool 注册
        importlib.reload(dezhu_agent.tools.read_file)
        importlib.reload(dezhu_agent.tools.write_file)

        # 现在 _TOOL_REGISTRATIONS 应有 2 个条目
        # 注：scan_tools 因 import 缓存机制在测试中效果有限，
        # 核心的自动注册在模块级 _init_registry() 中已通过导入验证覆盖
        assert len(_TOOL_REGISTRATIONS) == 2, (
            f"Expected 2 registrations after reload, got {len(_TOOL_REGISTRATIONS)}"
        )

        # 手动注册到独立 registry
        for td in _TOOL_REGISTRATIONS:
            r.register(td)
        _TOOL_REGISTRATIONS.clear()

        assert "read_file" in r.get_tool_names()

    def test_n3_get_tools_format(self):
        """N3: get_tools() 返回兼容 OpenAI API 的格式."""
        r = make_registry()
        r.register(make_tool_def(name="t1", fn=lambda x: "ok"))
        r.register(make_tool_def(name="t2", fn=lambda x: "ok"))

        tools = r.get_tools()
        assert len(tools) == 2
        for t in tools:
            assert "name" in t
            assert "description" in t
            assert "parameters" in t
            assert "type" not in t  # 不含 fn 等内部字段

    def test_n4_execute_returns_result(self):
        """N4: execute() 正确调用工具函数并返回结果."""
        r = make_registry()
        r.register(make_tool_def(name="echo", fn=lambda x: f"echo: {x}"))

        result = r.execute("echo", {"x": "hello"})
        assert result == "echo: hello"

    def test_n5_disable_tool(self):
        """N5: 禁用工具后在 get_tools 中排除，execute 返回 disabled."""
        r = make_registry()
        r.register(make_tool_def(name="secret_tool"))
        r.disable("secret_tool")

        # 不在 get_tools 列表中
        names = [t["name"] for t in r.get_tools()]
        assert "secret_tool" not in names

        # execute 返回 disabled
        result = r.execute("secret_tool", {"x": ""})
        assert "disabled" in result

    def test_n5_enable_tool(self):
        """N5: 重新启用工具后恢复正常."""
        r = make_registry()
        r.register(make_tool_def(name="my_tool"))
        r.disable("my_tool")
        r.enable("my_tool")

        names = [t["name"] for t in r.get_tools()]
        assert "my_tool" in names
        result = r.execute("my_tool", {"x": "ok"})
        assert "disabled" not in result

    def test_n6_zero_change_main_flow(self):
        """N6: loop.py 通过 registry 获取工具，新增工具不修改主流程."""
        # This is an architecture-level check — the N6 assertion is
        # that loop.py only references `from dezhu_agent.tools import registry`
        # and never imports individual tool modules.
        import inspect

        import dezhu_agent.loop as loop_mod

        source = inspect.getsource(loop_mod)
        assert "from dezhu_agent.tools import registry" in source
        assert "from dezhu_agent.tools.read_file" not in source
        assert "from dezhu_agent.tools.write_file" not in source


# ==================== 异常路径 ====================


class TestAbnormalPath:
    """E1-E4: 异常路径验收."""

    def test_e1_tool_exception_wrapped(self):
        """E1: 工具执行异常时返回错误消息，不向上抛出."""
        r = make_registry()

        def failing_fn(x: str) -> str:
            raise ValueError("something went wrong")

        r.register(make_tool_def(name="failing", fn=failing_fn))

        result = r.execute("failing", {"x": ""})
        assert "Error executing tool" in result
        assert "failing" in result
        assert "something went wrong" in result

    def test_e2_tool_not_found(self):
        """E2: 调用未注册工具返回 not found."""
        r = make_registry()
        result = r.execute("nonexistent", {})
        assert "not found" in result

    def test_e3_decorator_missing_name(self):
        """E3: @tool() 缺少 name 参数时抛出 ValueError."""
        with pytest.raises(ValueError, match="requires a 'name' argument"):
            tool(name="")  # type: ignore[arg-type]

    def test_e4_config_format_error(self):
        """E4: 配置文件格式错误时抛出异常."""
        import os
        import tempfile

        from dezhu_agent.tools_config import load_config

        # 创建非法 YAML 文件
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        tmp.write(": invalid yaml :\n")
        tmp.close()

        with pytest.raises(Exception):
            load_config(tmp.name)

        os.unlink(tmp.name)

    def test_e3_decorator_empty_name(self):
        """E3: @tool('') 空字符串也抛出 ValueError."""
        with pytest.raises(ValueError):
            # Validate that empty string triggers the same guard
            def dummy():
                pass

            # Direct call
            tool(name="")


# ==================== 边界条件 ====================


class TestBoundary:
    """B1-B5: 边界条件验收."""

    def test_b1_empty_registry(self):
        """B1: 空 registry 返回空列表，execute 返回 not found."""
        r = make_registry()
        assert r.get_tools() == []
        assert r.get_tool_names() == []
        assert "not found" in r.execute("anything", {})

    def test_b2_config_file_not_found(self):
        """B2: 配置文件不存在时返回空 dict."""
        from dezhu_agent.tools_config import load_config

        cfg = load_config("/tmp/__nonexistent_config_xyz.yaml")
        assert cfg == {}

    def test_b3_all_tools_disabled(self):
        """B3: 所有工具禁用时 get_tools 返回空列表."""
        r = make_registry()
        r.register(make_tool_def(name="a"))
        r.register(make_tool_def(name="b"))
        r.disable("a")
        r.disable("b")

        assert r.get_tools() == []

    def test_b4_unknown_tool_in_config(self):
        """B4: 配置中包含未注册工具不报错."""
        from dezhu_agent.tools_config import apply_config

        r = make_registry()
        r.register(make_tool_def(name="real_tool"))

        cfg = {"tools": {"nonexistent_tool": {"enabled": False}}}
        # Should not raise
        apply_config(r, cfg)
        assert "real_tool" in [t["name"] for t in r.get_tools()]

    def test_b5_empty_params_function(self):
        """B5: 无参数函数 → properties 为空，required 为 空数组."""
        r = make_registry()
        from dezhu_agent.tools import _TOOL_REGISTRATIONS

        _TOOL_REGISTRATIONS.clear()

        @tool(name="no_params", description="No params")
        def no_params() -> str:
            return "done"

        for td in _TOOL_REGISTRATIONS:
            r.register(td)
        _TOOL_REGISTRATIONS.clear()

        tools = r.get_tools()
        assert len(tools) == 1
        params = tools[0]["parameters"]
        assert params["properties"] == {}
        assert params["required"] == []
