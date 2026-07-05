"""memory 工具：agent 通过此工具读取、写入、删除跨会话记忆."""

from __future__ import annotations

from dezhu_agent.tools import tool

# MemoryStore 实例由 CLI 入口在启动时注入
_store = None


def set_memory_store(store) -> None:
    """注入 MemoryStore 实例（由 CLI 入口调用）."""
    global _store
    _store = store


def _get_store():
    if _store is None:
        raise RuntimeError("MemoryStore 尚未初始化")
    return _store


@tool(
    name="memory",
    description=(
        "管理跨会话记忆。读取、写入或删除 MEMORY.md（项目/环境记忆）或 "
        "USER.md（用户画像）中的条目。条目以 § 分隔。"
    ),
)
def memory(
    action: str,
    target: str,
    content: str = "",
    index: int = -1,
) -> str:
    """管理跨会话记忆。

    Args:
        action: "read"（读取全部）、"write"（追加条目）、"delete"（删除条目）.
        target: "memory"（MEMORY.md，项目/环境记忆，上限 2200 字符）
                或 "user"（USER.md，用户画像，上限 1375 字符）.
        content: 要写入的条目文本（action="write" 时必填）.
        index: 要删除的条目索引 0-based（action="delete" 时必填）.
    """
    from dezhu_agent.memory import (
        MemoryLimitError,
        MemoryLockError,
        MemoryIndexError,
        MemoryStoreError,
    )

    store = _get_store()

    try:
        if action == "read":
            text = store.read(target)
            usage = store.get_usage(target)
            limit_info = f"（{usage[0]}/{usage[1]} 字符）"
            if text.strip():
                return f"{target} 记忆内容 {limit_info}:\n\n{text}"
            return f"{target} 记忆为空 {limit_info}"

        elif action == "write":
            if not content.strip():
                return "错误：写入内容不能为空"
            return store.write(target, content)

        elif action == "delete":
            if index < 0:
                count = store.get_entry_count(target)
                return f"错误：请指定要删除的条目索引（0-{count - 1}）"
            removed = store.delete(target, index)
            usage = store.get_usage(target)
            return (
                f"已删除 {target} 记忆条目 #{index}: '{removed}'\n当前 {usage[0]}/{usage[1]} 字符"
            )

        else:
            return f"错误：无效的 action '{action}'，有效值: read, write, delete"

    except MemoryLimitError as e:
        return f"写入失败：{e}"
    except MemoryLockError as e:
        return f"操作失败：文件被锁定，请稍后重试。{e}"
    except MemoryIndexError as e:
        return f"删除失败：{e}"
    except MemoryStoreError as e:
        return f"Memory 操作失败：{e}"
