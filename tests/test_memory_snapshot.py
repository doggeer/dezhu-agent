"""测试 MemorySnapshot — 冻结快照生成与渲染."""

import tempfile

import pytest

from dezhu_agent.memory import MemorySnapshot, MemoryStore


@pytest.fixture
def store():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield MemoryStore(base_dir=tmpdir)


# ---- AC-01: 会话启动时加载并冻结 ----
def test_from_store_populates_both_fields(store):
    store.write("memory", "项目约定：用 pytest")
    store.write("user", "用户偏好：tabs 缩进")

    snapshot = MemorySnapshot.from_store(store)
    assert "pytest" in snapshot.memory_text
    assert "tabs" in snapshot.user_text


def test_render_includes_memory_section(store):
    store.write("memory", "项目约定：用 pytest")
    snapshot = MemorySnapshot.from_store(store)
    rendered = snapshot.render()
    assert "## MEMORY (your personal notes)" in rendered
    assert "pytest" in rendered


def test_render_includes_user_section(store):
    store.write("user", "用户偏好：tabs 缩进")
    snapshot = MemorySnapshot.from_store(store)
    rendered = snapshot.render()
    assert "## USER PROFILE (who the user is)" in rendered
    assert "tabs" in rendered


# ---- AC-02: 文件不存在时静默跳过 ----
def test_render_empty_store_returns_empty_string(store):
    snapshot = MemorySnapshot.from_store(store)
    assert snapshot.render() == ""


# ---- AC-24: 空文件 ----
def test_render_empty_memory_skips_section(store):
    store.write("user", "用户偏好")
    snapshot = MemorySnapshot.from_store(store)
    rendered = snapshot.render()
    assert "## MEMORY" not in rendered
    assert "## USER PROFILE" in rendered


def test_render_empty_user_skips_section(store):
    store.write("memory", "项目约定")
    snapshot = MemorySnapshot.from_store(store)
    rendered = snapshot.render()
    assert "## MEMORY" in rendered
    assert "## USER PROFILE" not in rendered
