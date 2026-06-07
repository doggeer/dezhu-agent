"""工具注册装饰器 — @register_tool."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from dezhu_agent.models.tool import ToolDef
from dezhu_agent.services.tool_registry import get_tool_registry

T = TypeVar("T", bound=type)


def register_tool(
    name: str,
    description: str,
    parameters: dict[str, Any],
) -> Callable[[T], T]:
    """类装饰器: 将工具类注册到 ToolRegistry 单例.

    Usage::

        @register_tool(
            name="my_tool",
            description="Does something useful.",
            parameters={
                "type": "object",
                "properties": {"arg1": {"type": "string", "description": "..."}},
                "required": ["arg1"],
            },
        )
        class MyTool(BaseTool):
            def execute(self, **kwargs: Any) -> str: ...
    """

    def decorator(cls: T) -> T:
        registry = get_tool_registry()
        tool_def = ToolDef(
            name=name,
            description=description,
            parameters=parameters,
            handler=cls,
        )
        registry.register(tool_def)
        return cls

    return decorator
