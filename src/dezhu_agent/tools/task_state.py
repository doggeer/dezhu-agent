"""TaskState 维护工具：task_set_goal / todo_write / todo_update.

模型通过这三个工具自行维护 TaskState，TaskState 不进消息流、永不压缩。
"""

from __future__ import annotations

import uuid

from dezhu_agent.compression import get_task_state, reset_task_state
from dezhu_agent.tools import tool


@tool(
    name="task_set_goal",
    description="设置当前任务的目标。在开始多步任务时调用，用于帮助保持工作连续性。",
)
def task_set_goal(goal: str) -> str:
    """设置 TaskState.goal。

    Args:
        goal: 任务目标描述，一句话说明要完成什么。
    """
    state = get_task_state()
    state.goal = goal
    return f"目标已设置：{goal}"


@tool(
    name="todo_write",
    description="列出当前任务的步骤列表。多步任务开始前调用，每步完成后用 todo_update 标记进度。",
)
def todo_write(items: str) -> str:
    """写入 TODO 列表。

    Args:
        items: JSON 字符串数组，每个元素为 {"subject": "步骤描述"}。
               示例: '[{"subject":"列出目录"},{"subject":"读取文件"}]'
    """
    try:
        items_list = __import__("json").loads(items)
    except (__import__("json").JSONDecodeError, TypeError):
        return "错误：items 参数需要是合法的 JSON 数组字符串"

    if not isinstance(items_list, list):
        return "错误：items 需要是数组"

    state = get_task_state()
    new_todos = []
    for item in items_list:
        if not isinstance(item, dict):
            return f"错误：数组元素需要是对象，收到 {type(item).__name__}"
        new_todos.append({
            "id": f"t{uuid.uuid4().hex[:6]}",
            "subject": item.get("subject", str(item)),
            "status": "pending",
        })

    state.todos = new_todos
    return f"已列出 {len(new_todos)} 个步骤"


@tool(
    name="todo_update",
    description="更新指定步骤的状态。完成一步后调用，让模型知道进度。",
)
def todo_update(id: str, status: str) -> str:
    """更新一个 TODO 的状态。

    Args:
        id: 步骤 ID（todo_write 返回的 id）。
        status: 新状态，可选 pending / in_progress / completed。
    """
    if status not in ("pending", "in_progress", "completed"):
        return f"错误：status 只能是 pending / in_progress / completed，收到 '{status}'"

    state = get_task_state()
    for t in state.todos:
        if t.get("id") == id:
            t["status"] = status
            return f"步骤 ({id}) 已更新为 {status}"

    return f"错误：未找到步骤 ID '{id}'"


# 重置工具——供测试和 session 清理使用
@tool(
    name="task_reset",
    description="重置当前任务状态（清空目标和 TODO 列表）。",
)
def task_reset() -> str:
    """重置 TaskState 为新实例。"""
    reset_task_state()
    return "任务状态已重置"
