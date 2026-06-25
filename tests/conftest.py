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
