"""工具注册中心 -- 单例, 负责工具注册/查询/OpenAI 格式转换/扫描与代理执行."""

from __future__ import annotations

import importlib
import json
import pkgutil
from functools import lru_cache
from typing import Any

from dezhu_agent.models.tool import ToolDef


class ToolRegistry:
    """工具注册中心.

    Usage::

        registry = get_tool_registry()
        registry.scan("dezhu_agent.core.tools")
        tools = registry.get_tools_for_openai()
        result = registry.execute("terminal", '{"command":"ls"}')
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolDef] = {}

    def register(self, tool_def: ToolDef) -> None:
        self._tools[tool_def.name] = tool_def

    def get_tools_for_openai(self) -> list[dict[str, Any]]:
        return [t.to_openai_format() for t in self._tools.values()]

    def execute(self, name: str, arguments: str) -> str:
        tool_def = self._tools.get(name)
        if tool_def is None:
            return json.dumps({"error": f"Unknown tool: {name}"})

        try:
            parsed_args: dict[str, Any] = json.loads(arguments)
        except json.JSONDecodeError:
            return json.dumps({"error": f"Invalid JSON arguments: {arguments}"})

        try:
            instance = tool_def.handler()
            return str(instance.execute(**parsed_args))
        except Exception as exc:
            return json.dumps({"error": f"Tool execution failed: {exc}"})

    def scan(self, package_name: str) -> None:
        """扫描指定包下所有模块, 自动导入以触发 @register_tool 装饰器的注册."""
        try:
            package = importlib.import_module(package_name)
        except ModuleNotFoundError:
            return

        if not hasattr(package, "__path__"):
            return

        for _, module_name, _ in pkgutil.walk_packages(package.__path__, prefix=package_name + "."):
            importlib.import_module(module_name)


@lru_cache
def get_tool_registry() -> ToolRegistry:
    return ToolRegistry()
