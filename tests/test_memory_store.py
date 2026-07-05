"""测试 MemoryStore — 文件读写、锁、上限校验."""

import tempfile
from pathlib import Path

import pytest

from dezhu_agent.memory.store import (
    MEMORY_MAX_CHARS,
    USER_MAX_CHARS,
    MemoryLimitError,
    MemoryLockError,
    MemoryIndexError,
    MemoryStore,
    MemoryStoreError,
)


@pytest.fixture
def store():
    """创建使用临时目录的 MemoryStore."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield MemoryStore(base_dir=tmpdir)


# ---- AC-14: 读取 MEMORY.md ----
def test_read_memory_returns_content(store):
    store.write("memory", "项目用 pytest 跑测试")
    content = store.read("memory")
    assert "pytest" in content


# ---- AC-15: 读取 USER.md ----
def test_read_user_returns_content(store):
    store.write("user", "用户偏好 tabs 缩进")
    content = store.read("user")
    assert "tabs" in content


# ---- AC-16: 写入 MEMORY.md ----
def test_write_memory_appends_entry(store):
    store.write("memory", "第一条记忆")
    store.write("memory", "第二条记忆")
    content = store.read("memory")
    assert "§ 第一条记忆" in content
    assert "§ 第二条记忆" in content


# ---- AC-17: 写入 USER.md ----
def test_write_user_appends_entry(store):
    store.write("user", "用户偏好简洁回答")
    content = store.read("user")
    assert "简洁回答" in content


# ---- AC-18: 删除条目 ----
def test_delete_entry_by_index(store):
    store.write("memory", "条目0")
    store.write("memory", "条目1")
    store.write("memory", "条目2")

    removed = store.delete("memory", 1)
    assert removed == "条目1"

    content = store.read("memory")
    assert "条目0" in content
    assert "条目1" not in content
    assert "条目2" in content


# ---- AC-21: 写入超上限 ----
def test_write_exceeding_limit_raises(store):
    limit = MEMORY_MAX_CHARS
    # 写入一个接近上限的大条目
    big_entry = "x" * (limit - 5)
    store.write("memory", big_entry)
    with pytest.raises(MemoryLimitError):
        store.write("memory", "再来一点就超了")


# ---- AC-22: 删除越界索引 ----
def test_delete_out_of_range_raises(store):
    store.write("memory", "只有一条")
    with pytest.raises(MemoryIndexError):
        store.delete("memory", 5)

    with pytest.raises(MemoryIndexError):
        store.delete("memory", -1)


# ---- AC-24: 空文件 ----
def test_read_nonexistent_file_returns_empty(store):
    assert store.read("memory") == ""
    assert store.read("user") == ""


def test_write_empty_content_raises(store):
    with pytest.raises(MemoryStoreError):
        store.write("memory", "")


def test_get_usage_returns_usage_and_limit(store):
    store.write("memory", "test")
    current, limit = store.get_usage("memory")
    assert current > 0
    assert limit == MEMORY_MAX_CHARS

    current_u, limit_u = store.get_usage("user")
    assert limit_u == USER_MAX_CHARS


def test_get_entry_count(store):
    assert store.get_entry_count("memory") == 0
    store.write("memory", "a")
    assert store.get_entry_count("memory") == 1
    store.write("memory", "b")
    assert store.get_entry_count("memory") == 2


def test_invalid_target_raises(store):
    with pytest.raises(MemoryStoreError):
        store.read("invalid")


def test_delete_last_entry_clears_file(store):
    store.write("memory", "only")
    store.delete("memory", 0)
    assert store.read("memory").strip() == ""
    assert store.get_entry_count("memory") == 0
