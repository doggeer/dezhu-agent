"""共享 fixture：所有测试自动设置 OPENAI_API_KEY."""

import pytest


@pytest.fixture(autouse=True)
def _default_env(monkeypatch):
    """自动设置测试环境变量和模块常量，避免 .env 影响测试行为."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    # 模块级 from config import X 创建了局部引用，需逐模块 patch
    import dezhu_agent.__main__ as main_mod
    import dezhu_agent.config as cfg
    import dezhu_agent.llm as llm_mod
    import dezhu_agent.loop as loop_mod

    monkeypatch.setattr(cfg, "STREAM_MODE", False)
    monkeypatch.setattr(cfg, "THINKING_ENABLED", False)
    monkeypatch.setattr(loop_mod, "STREAM_MODE", False)
    monkeypatch.setattr(main_mod, "STREAM_MODE", False)
    monkeypatch.setattr(main_mod, "THINKING_ENABLED", False)
    monkeypatch.setattr(llm_mod, "THINKING_ENABLED", False)

    # 重置工具注册表：用独立 ToolRegistry 实例替换模块级单例
    import dezhu_agent.tools as tools_mod
    from dezhu_agent.tools import ToolDef, ToolRegistry

    fresh_registry = ToolRegistry()

    # 注册 read_file 和 write_file（保持 loop 测试需要的工具可用）
    from pathlib import Path

    def _read_file(path: str) -> str:
        p = Path(path).resolve()
        if not p.exists():
            return f"Error: file not found: {path}"
        if not p.is_file():
            return f"Error: not a file: {path}"
        return p.read_text(encoding="utf-8", errors="replace")

    def _write_file(path: str, content: str) -> str:
        p = Path(path).resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"Successfully wrote {len(content)} bytes to {path}"

    fresh_registry.register(
        ToolDef(
            name="read_file",
            description="读取指定文件的内容并返回。适用于查看源代码、配置文件、文档等。",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要读取的文件路径（相对或绝对路径）",
                    },
                },
                "required": ["path"],
            },
            fn=_read_file,
        )
    )
    fresh_registry.register(
        ToolDef(
            name="write_file",
            description="将内容写入指定文件（覆盖写）。适用于创建新文件或修改已有文件。",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要写入的文件路径（相对或绝对路径）",
                    },
                    "content": {
                        "type": "string",
                        "description": "要写入的文件内容",
                    },
                },
                "required": ["path", "content"],
            },
            fn=_write_file,
        )
    )

    monkeypatch.setattr(tools_mod, "registry", fresh_registry)
    monkeypatch.setattr(loop_mod, "registry", fresh_registry)
