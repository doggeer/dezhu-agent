"""测试 PromptSource 协议 + MemorySource."""

import tempfile

import pytest

from dezhu_agent.memory import MemorySnapshot, MemorySource, MemoryStore
from dezhu_agent.prompt_assembler import (
    register_prompt_source,
    unregister_prompt_source,
    assemble_system_prompt,
)


@pytest.fixture(autouse=True)
def clean_sources():
    """每个测试前后清理注册的 PromptSource."""
    # 清理
    import dezhu_agent.prompt_assembler as pa

    original = list(pa._sources)
    pa._sources.clear()
    yield
    pa._sources[:] = original


@pytest.fixture
def store():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield MemoryStore(base_dir=tmpdir)


# ---- AC-01: Memory source 注入 system prompt ----
def test_memory_source_injects_into_system_prompt(store):
    store.write("memory", "项目约定：pytest -x")
    store.write("user", "偏好简洁回答")

    snapshot = MemorySnapshot.from_store(store)
    source = MemorySource(snapshot)
    register_prompt_source(source)

    prompt = assemble_system_prompt()
    assert "## MEMORY (your personal notes)" in prompt
    assert "pytest" in prompt
    assert "## USER PROFILE (who the user is)" in prompt
    assert "简洁回答" in prompt


def test_empty_memory_source_produces_no_section(store):
    snapshot = MemorySnapshot.from_store(store)  # 空 store
    source = MemorySource(snapshot)
    register_prompt_source(source)

    prompt = assemble_system_prompt()
    assert "## MEMORY" not in prompt
    assert "## USER PROFILE" not in prompt


def test_register_replaces_same_name():
    # 注册第一个
    snapshot1 = MemorySnapshot("mem1", "user1")
    source1 = MemorySource(snapshot1)
    register_prompt_source(source1)

    # 注册同名第二个（应该替换）
    snapshot2 = MemorySnapshot("mem2", "user2")
    source2 = MemorySource(snapshot2)
    register_prompt_source(source2)

    import dezhu_agent.prompt_assembler as pa

    assert len(pa._sources) == 1
    assert pa._sources[0].render() == snapshot2.render()


def test_unregister_removes_source(store):
    snapshot = MemorySnapshot.from_store(store)
    source = MemorySource(snapshot)
    register_prompt_source(source)

    unregister_prompt_source("MEMORY")

    import dezhu_agent.prompt_assembler as pa

    assert len(pa._sources) == 0
