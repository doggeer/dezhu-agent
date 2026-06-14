"""任务管理工具 -- task_set_goal、todo_write、todo_update."""

from __future__ import annotations

from typing import Any

from dezhu_agent.core.task_state import get_current_session_id, get_task_state_manager
from dezhu_agent.models.tool import BaseTool, tool_error
from dezhu_agent.utils.tool_decorator import register_tool

_TASK_SET_GOAL_DESC = (
    "Set the goal for the current multi-step task. "
    "Call this once at the start of a complex task before using todo_write to plan the steps."
    "The goal is a concise description of what the task aims to accomplish."
)

_TODO_WRITE_DESC = (
    "Write/overwrite the TODO step list for the current task. "
    "Each item is a short description of one step. "
    "IDs (t1, t2, ...) are auto-assigned; all items start with status='pending'. "
    "Call this after task_set_goal (or instead of it) to define the concrete execution plan. "
    "Call it again if you need to replan — it fully replaces the previous list."
)

_TODO_UPDATE_DESC = (
    "Update the status of one TODO step. "
    "Typical workflow: mark one item 'in_progress' → do the work → mark it 'completed' with a result. "
    "The result should be a brief conclusion (1-3 sentences) summarizing what was done and what was found. "
    "Always include a meaningful result when marking completed."
)


def _get_task_state():
    """获取当前会话的 TaskState, 确保 session_id 已设置."""
    session_id = get_current_session_id()
    if not session_id:
        return None
    return get_task_state_manager().get_or_create(session_id)


@register_tool(
    name="task_set_goal",
    description=_TASK_SET_GOAL_DESC,
    parameters={
        "type": "object",
        "properties": {
            "goal": {
                "type": "string",
                "description": "The goal / objective of the current task. A concise one-line description.",
            },
        },
        "required": ["goal"],
    },
)
class TaskSetGoalTool(BaseTool):
    """设定任务目标的工具."""

    def execute(self, goal: str = "", **kwargs: Any) -> str:
        state = _get_task_state()
        if state is None:
            return tool_error("No active session")
        return state.set_goal(goal)


@register_tool(
    name="todo_write",
    description=_TODO_WRITE_DESC,
    parameters={
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of step descriptions. Each should be a concise one-line action.",
                "minItems": 1,
            },
        },
        "required": ["items"],
    },
)
class TodoWriteTool(BaseTool):
    """列出 TODO 步骤的工具."""

    def execute(self, items: list = [], **kwargs: Any) -> str:  # type: ignore[override]
        state = _get_task_state()
        if state is None:
            return tool_error("No active session")
        return state.todo_write(items)


@register_tool(
    name="todo_update",
    description=_TODO_UPDATE_DESC,
    parameters={
        "type": "object",
        "properties": {
            "id": {
                "type": "string",
                "description": "The step ID to update (e.g., 't1', 't2').",
            },
            "status": {
                "type": "string",
                "enum": ["pending", "in_progress", "completed"],
                "description": "New status for this step.",
            },
            "result": {
                "type": "string",
                "description": "Brief conclusion / result of this step, required when marking completed.",
            },
        },
        "required": ["id", "status"],
    },
)
class TodoUpdateTool(BaseTool):
    """更新 TODO 步骤状态的工具."""

    def execute(self, id: str = "", status: str = "", result: str = "", **kwargs: Any) -> str:  # type: ignore[override]
        state = _get_task_state()
        if state is None:
            return tool_error("No active session")
        return state.todo_update(id, status, result if result else None)
