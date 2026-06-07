"""工具注册中心 -- 单例, 负责工具注册/查询/OpenAI 格式转换/扫描与代理执行."""

from __future__ import annotations

import importlib
import json
import pkgutil
from typing import Any, ClassVar

from dezhu_agent.models.tool import ToolDef


class ToolRegistry:
    """工具注册中心单例.

    用法::

        registry = ToolRegistry.get_instance()
        registry.scan("dezhu_agent.core.tools")
        tools = registry.get_tools_for_openai()
        result = registry.execute("terminal", '{"command":"ls"}')
    """

    _instance: ClassVar[ToolRegistry | None] = None

    def __init__(self) -> None:
        self._tools: dict[str, ToolDef] = {}

    @classmethod
    def get_instance(cls) -> ToolRegistry:
        """获取 ToolRegistry 单例."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def register(self, tool_def: ToolDef) -> None:
        """注册一个工具定义."""
        self._tools[tool_def.name] = tool_def

    def is_empty(self) -> bool:
        """注册表是否为空."""
        return len(self._tools) == 0

    def get_tools_for_openai(self) -> list[dict[str, Any]]:
        """返回所有已注册工具的 OpenAI function-calling 格式列表."""
        return [tool.to_openai_format() for tool in self._tools.values()]

    def execute(self, name: str, arguments: str) -> str:
        """根据工具名称和 JSON 参数字符串代理执行工具.

        Args:
            name: 工具名称
            arguments: JSON 格式的参数字符串

        Returns:
            工具执行结果字符串
        """
        tool_def = self._tools.get(name)
        if tool_def is None:
            return json.dumps({"error": f"Unknown tool: {name}"})

        try:
            parsed_args: dict[str, Any] = json.loads(arguments)
        except json.JSONDecodeError:
            return json.dumps({"error": f"Invalid JSON arguments: {arguments}"})

        try:
            instance = tool_def.handler()
            result = instance.execute(**parsed_args)
            return str(result)
        except Exception as exc:
            return json.dumps({"error": f"Tool execution failed: {exc}"})

    def scan(self, package_name: str) -> None:
        """扫描指定包下所有模块, 自动导入以触发 @register_tool 装饰器的注册.

        Args:
            package_name: 完整包名, 如 ``dezhu_agent.core.tools``
        """
        try:
            package = importlib.import_module(package_name)
        except ModuleNotFoundError:
            return

        if not hasattr(package, "__path__"):
            return

        for _, module_name, _ in pkgutil.walk_packages(package.__path__, prefix=package_name + "."):
            importlib.import_module(module_name)
