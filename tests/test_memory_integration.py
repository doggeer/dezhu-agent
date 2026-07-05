"""端到端集成测试 — 跨会话 memory 生效."""

import tempfile
from unittest.mock import Mock, patch

import pytest

from dezhu_agent.memory import MemorySnapshot, MemorySource, MemoryStore
from dezhu_agent.prompt_assembler import register_prompt_source, unregister_prompt_source


@pytest.fixture(autouse=True)
def clean_sources():
    import dezhu_agent.prompt_assembler as pa

    original = list(pa._sources)
    pa._sources.clear()
    yield
    pa._sources[:] = original


# ---- AC-03: 会话中间写入不更新当前 system prompt ----
def test_snapshot_stable_after_write(tmp_path):
    store = MemoryStore(tmp_path)
    # 生成快照
    snapshot = MemorySnapshot.from_store(store)
    assert snapshot.render() == ""

    # 写入
    store.write("memory", "项目约定：pytest -x")

    # 快照不变（AC-03）
    assert snapshot.render() == ""

    # 磁盘已更新
    assert "pytest" in store.read("memory")


# ---- AC-04: 下次会话生效 ----
def test_new_session_sees_previous_write(tmp_path):
    store = MemoryStore(tmp_path)

    # 会话 1：写入
    store.write("user", "偏好 tabs 缩进")

    # 会话 2：新快照应包含写入内容
    snapshot = MemorySnapshot.from_store(store)
    rendered = snapshot.render()
    assert "tabs" in rendered


# ---- AC-01/AC-02: MemorySource 注入 system prompt ----
def test_memory_source_in_production_pipeline(tmp_path):
    store = MemoryStore(tmp_path)
    store.write("memory", "使用 pytest")
    store.write("user", "偏好简洁回答")

    snapshot = MemorySnapshot.from_store(store)
    source = MemorySource(snapshot)
    register_prompt_source(source)

    from dezhu_agent.prompt_assembler import assemble_system_prompt

    prompt = assemble_system_prompt()
    assert "## MEMORY (your personal notes)" in prompt
    assert "## USER PROFILE (who the user is)" in prompt
    assert "pytest" in prompt
    assert "简洁回答" in prompt


# ---- 空文件不留痕迹 ----
def test_empty_files_produce_no_system_prompt_section(tmp_path):
    store = MemoryStore(tmp_path)
    snapshot = MemorySnapshot.from_store(store)
    source = MemorySource(snapshot)
    register_prompt_source(source)

    from dezhu_agent.prompt_assembler import assemble_system_prompt

    prompt = assemble_system_prompt()
    assert "## MEMORY" not in prompt
    assert "## USER PROFILE" not in prompt
