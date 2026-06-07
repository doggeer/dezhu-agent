"""工具数据模型 -- ToolDef、BaseTool."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel


class ToolDef(BaseModel):
    """工具注册定义, 包含 OpenAI function-calling 所需的全部字段及执行入口."""

    name: str
    description: str
    parameters: dict[str, Any]
    handler: type[BaseTool]

    def to_openai_format(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class BaseTool(ABC):
    """所有工具的抽象基类, 子类需实现 execute 方法."""

    @abstractmethod
    def execute(self, **kwargs: Any) -> str: ...
