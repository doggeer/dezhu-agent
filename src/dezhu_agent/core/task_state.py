"""任务状态管理 -- TaskState + TaskStateManager 单例 + session contextvar."""

from __future__ import annotations

import json
from contextvars import ContextVar
from functools import lru_cache
from typing import Any

from dezhu_agent.models.task import PlanItem, PlanItemStatus, PlanningState

# ---- ContextVar: 当前会话 ID ----
_current_session_id: ContextVar[str] = ContextVar("task_state_session_id", default="")


def get_current_session_id() -> str:
    """获取当前会话 ID (由 run_conversation 设置)."""
    return _current_session_id.get()


def set_current_session_id(session_id: str) -> None:
    """设置当前会话 ID (run_conversation 开始前调用)."""
    _current_session_id.set(session_id)


class TaskState:
    """单个会话的任务状态, 包装 PlanningState 并提供操作方法."""

    def __init__(self) -> None:
        self._state = PlanningState()
        self._id_counter: int = 0

    # -- 属性透传 --
    @property
    def goal(self) -> str:
        return self._state.goal

    @property
    def is_active(self) -> bool:
        return self._state.is_active

    @property
    def is_completed(self) -> bool:
        return self._state.is_completed

    @property
    def rounds_since_update(self) -> int:
        return self._state.rounds_since_update

    # -- 操作方法 --

    def set_goal(self, goal: str) -> str:
        """设定任务目标, 重置所有 TODO. 返回 JSON 确认."""
        self._state.goal = goal
        self._state.items = []
        self._id_counter = 0
        self._state.rounds_since_update = 0
        return json.dumps({"status": "ok", "goal": goal})

    def todo_write(self, items: list[str]) -> str:
        """列出步骤, 自动补 id (t1, t2...) 和默认 status=pending. 返回 JSON."""
        if not items:
            return json.dumps({"status": "error", "message": "items cannot be empty"})

        new_items: list[PlanItem] = []
        for content in items:
            self._id_counter += 1
            new_items.append(
                PlanItem(
                    id=f"t{self._id_counter}",
                    content=content,
                    status="pending",
                )
            )

        self._state.items = new_items
        self._state.rounds_since_update = 0

        return json.dumps(
            {
                "status": "ok",
                "items": [
                    {"id": item.id, "content": item.content, "status": item.status}
                    for item in new_items
                ],
            }
        )

    def todo_update(self, item_id: str, status: PlanItemStatus, result: str | None = None) -> str:
        """标记一步的进度和结论. 返回 JSON."""
        target = self._find_item(item_id)
        if target is None:
            return json.dumps(
                {
                    "status": "error",
                    "message": f"Item {item_id} not found. Available: "
                    f"{[i.id for i in self._state.items]}",
                }
            )

        target.status = status
        if result is not None and result.strip():
            target.result = result
        self._state.rounds_since_update = 0

        # 如果当前步骤标记为 in_progress, 确保其他项没有 in_progress
        if status == "in_progress":
            for item in self._state.items:
                if item.id != item_id and item.status == "in_progress":
                    item.status = "pending"

        return json.dumps(
            {
                "status": "ok",
                "id": target.id,
                "content": target.content,
                "status": target.status,
                "result": target.result,
            }
        )

    def render(self) -> str:
        """渲染完整的 task state 文本, 追加到 system prompt 末尾."""
        return self._state.render()

    def increment_round(self) -> None:
        """每轮结束后 +1, 用于超时提醒."""
        self._state.rounds_since_update += 1

    def check_reminder(self, threshold: int) -> str | None:
        """如果超过 threshold 轮未更新, 返回提醒文本; 否则返回 None."""
        if not self._state.is_active:
            return None
        if self._state.rounds_since_update >= threshold:
            return (
                f"[System Reminder] You have an active task (goal: '{self._state.goal}') "
                f"but have not updated the TODO list for {self._state.rounds_since_update} rounds. "
                "Please use todo_update to mark your progress, or continue working on the next step."
            )
        return None

    def _find_item(self, item_id: str) -> PlanItem | None:
        for item in self._state.items:
            if item.id == item_id:
                return item
        return None


class TaskStateManager:
    """管理 session_id → TaskState 的映射, 全局单例."""

    def __init__(self) -> None:
        self._states: dict[str, TaskState] = {}

    def get_or_create(self, session_id: str) -> TaskState:
        """获取或创建 session 对应的 TaskState."""
        if session_id not in self._states:
            self._states[session_id] = TaskState()
        return self._states[session_id]

    def get(self, session_id: str) -> TaskState | None:
        """获取 session 对应的 TaskState, 不存在返回 None."""
        return self._states.get(session_id)

    def remove(self, session_id: str) -> None:
        """移除 session 对应的 TaskState."""
        self._states.pop(session_id, None)


@lru_cache
def get_task_state_manager() -> TaskStateManager:
    """获取 TaskStateManager 全局单例."""
    return TaskStateManager()
