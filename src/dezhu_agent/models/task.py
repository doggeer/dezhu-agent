"""任务管理数据模型 -- PlanItem、PlanningState."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

PlanItemStatus = Literal["pending", "in_progress", "completed"]


class PlanItem(BaseModel):
    """单个 TODO 步骤."""

    id: str = Field(description="步骤唯一标识, 如 t1、t2")
    content: str = Field(description="步骤描述")
    status: PlanItemStatus = Field(default="pending", description="步骤状态")
    result: str | None = Field(default=None, description="步骤完成后的结论")


class PlanningState(BaseModel):
    """当前任务的完整规划状态."""

    goal: str = Field(default="", description="任务目标")
    items: list[PlanItem] = Field(default_factory=list, description="TODO 步骤列表")
    rounds_since_update: int = Field(default=0, description="自上次更新以来的轮数")

    @property
    def is_active(self) -> bool:
        """任务是否活跃: 有目标且未全部完成."""
        if not self.goal or not self.items:
            return False
        return any(item.status != "completed" for item in self.items)

    @property
    def is_completed(self) -> bool:
        """所有步骤是否已完成."""
        if not self.items:
            return False
        return all(item.status == "completed" for item in self.items)

    def render(self) -> str:
        """渲染为 system prompt 片段, 每轮追加到 system prompt 末尾."""
        if not self.goal:
            return ""

        lines: list[str] = [
            "# Task State (live, never compressed)",
            "",
            "## Goal",
            self.goal,
            "",
            "## TODO",
        ]

        status_icons = {"pending": "[ ]", "in_progress": "[~]", "completed": "[x]"}
        for item in self.items:
            icon = status_icons.get(item.status, "[ ]")
            line = f"{icon} ({item.id}) {item.content}"
            if item.result:
                line += f" | 结论: {item.result}"
            lines.append(line)

        return "\n".join(lines)
