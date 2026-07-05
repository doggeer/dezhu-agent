"""测试 memory 工具集成."""

import tempfile

import pytest

from dezhu_agent.memory import MemoryStore
from dezhu_agent.tools import ToolDef, registry as tool_registry
from dezhu_agent.tools.memory_tool import memory as memory_fn, set_memory_store


@pytest.fixture(autouse=True)
def setup_memory_store():
    """每个测试注入独立的 MemoryStore 并注册 memory 工具."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store = MemoryStore(base_dir=tmpdir)
        set_memory_store(store)

        # 在测试 registry 中注册 memory 工具
        tool_registry.register(
            ToolDef(
                name="memory",
                description="管理跨会话记忆",
                parameters={
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "description": "read/write/delete"},
                        "target": {"type": "string", "description": "memory/user"},
                        "content": {"type": "string", "description": "条目内容（write 时）"},
                        "index": {"type": "integer", "description": "条目索引（delete 时）"},
                    },
                    "required": ["action", "target"],
                },
                fn=memory_fn,
            )
        )
        yield store


def test_read_empty_memory(setup_memory_store):
    result = tool_registry.execute("memory", {"action": "read", "target": "memory"})
    assert "为空" in result


def test_write_and_read_memory(setup_memory_store):
    tool_registry.execute(
        "memory",
        {"action": "write", "target": "memory", "content": "项目使用 pytest"},
    )
    result = tool_registry.execute("memory", {"action": "read", "target": "memory"})
    assert "pytest" in result


def test_write_and_read_user(setup_memory_store):
    tool_registry.execute(
        "memory",
        {"action": "write", "target": "user", "content": "偏好 tabs 缩进"},
    )
    result = tool_registry.execute("memory", {"action": "read", "target": "user"})
    assert "tabs" in result


def test_delete_entry(setup_memory_store):
    tool_registry.execute(
        "memory",
        {"action": "write", "target": "memory", "content": "条目0"},
    )
    tool_registry.execute(
        "memory",
        {"action": "write", "target": "memory", "content": "条目1"},
    )

    result = tool_registry.execute(
        "memory",
        {"action": "delete", "target": "memory", "index": 0},
    )
    assert "已删除" in result

    content = setup_memory_store.read("memory")
    assert "条目0" not in content
    assert "条目1" in content


def test_delete_without_index_shows_error(setup_memory_store):
    result = tool_registry.execute(
        "memory",
        {"action": "delete", "target": "memory", "index": -1},
    )
    assert "错误" in result or "请指定" in result


def test_write_empty_content_shows_error(setup_memory_store):
    result = tool_registry.execute(
        "memory",
        {"action": "write", "target": "memory", "content": ""},
    )
    assert "错误" in result


def test_invalid_action_shows_error(setup_memory_store):
    result = tool_registry.execute(
        "memory",
        {"action": "invalid", "target": "memory"},
    )
    assert "错误" in result
