"""可插拔工具系统：ToolRegistry + @tool 装饰器."""

from __future__ import annotations

import importlib
import inspect
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class ToolDef:
    """工具定义：名称、描述、参数模式、执行函数、启用状态."""

    name: str
    description: str
    parameters: dict[str, Any]
    fn: Callable[..., str]
    enabled: bool = True


class ToolRegistry:
    """工具注册中心：注册、执行、查询、启用/禁用."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDef] = {}

    def register(self, tool: ToolDef) -> None:
        """注册一个工具."""
        self._tools[tool.name] = tool

    def deregister(self, name: str) -> None:
        """注销一个工具."""
        self._tools.pop(name, None)

    def get_tools(self) -> list[dict[str, Any]]:
        """返回启用的工具定义列表（不含 fn），适配 OpenAI API tools 参数格式."""
        return [
            {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            }
            for t in self._tools.values()
            if t.enabled
        ]

    def get_tool_names(self) -> list[str]:
        """返回所有已注册的工具名称."""
        return list(self._tools.keys())

    def execute(self, name: str, args: dict[str, Any]) -> str:
        """执行指定工具，返回结果字符串.

        E2: 工具不存在 → "Tool '{name}' not found"
        N5: 工具被禁用 → "Tool '{name}' is disabled"
        E1: 执行异常 → "Error executing tool '{name}': {msg}"
        """
        from dezhu_agent.logging_config import get_logger

        _log = get_logger(__name__)
        tool = self._tools.get(name)
        if tool is None:
            _log.warning("工具不存在: %s", name)
            return f"Tool '{name}' not found"
        if not tool.enabled:
            _log.warning("工具已被禁用: %s", name)
            return f"Tool '{name}' is disabled"

        start = time.monotonic()
        try:
            result = tool.fn(**args)
            elapsed_ms = (time.monotonic() - start) * 1000
            result_preview = result[:500] + ("… [TRUNCATED]" if len(result) > 500 else "")
            _log.info(
                "工具执行: %s | 参数=%s | 耗时=%.1fms | 结果=%s",
                name,
                {k: str(v)[:100] for k, v in args.items()},
                elapsed_ms,
                result_preview,
            )
            _log.debug(
                "工具执行详情: %s | 参数=%s | 耗时=%.1fms | 结果=%s",
                name,
                args,
                elapsed_ms,
                result,
            )
            return result
        except Exception as e:
            elapsed_ms = (time.monotonic() - start) * 1000
            _log.error(
                "工具执行异常: %s | 参数=%s | 耗时=%.1fms | 异常=%s",
                name,
                args,
                elapsed_ms,
                e,
            )
            return f"Error executing tool '{name}': {e}"

    def disable(self, name: str) -> None:
        """禁用指定工具."""
        if name in self._tools:
            self._tools[name].enabled = False

    def enable(self, name: str) -> None:
        """启用指定工具."""
        if name in self._tools:
            self._tools[name].enabled = True


# --- @tool 装饰器 ---

_TOOL_REGISTRATIONS: list[ToolDef] = []


def tool(name: str, description: str = "") -> Callable:
    """装饰器：将函数声明为可注册的工具.

    自动解析函数签名生成 parameters（str→string, int→integer, bool→boolean）。
    缺少 name 时抛出 ValueError（E3）。
    """
    if not name:
        raise ValueError("tool() requires a 'name' argument")

    def decorator(func: Callable) -> Callable:
        sig = inspect.signature(func)
        properties: dict[str, dict[str, Any]] = {}
        required: list[str] = []

        for param_name, param in sig.parameters.items():
            if param_name in ("self", "cls"):
                continue
            # 类型映射
            param_type = "string"
            if param.annotation is not inspect.Parameter.empty:
                if param.annotation is int:
                    param_type = "integer"
                elif param.annotation is bool:
                    param_type = "boolean"
            properties[param_name] = {
                "type": param_type,
                "description": f"Parameter {param_name}",
            }
            # 无默认值的参数为 required
            if param.default is inspect.Parameter.empty:
                required.append(param_name)

        parameters: dict[str, Any] = {
            "type": "object",
            "properties": properties,
            "required": required,
        }

        td = ToolDef(
            name=name,
            description=description,
            parameters=parameters,
            fn=func,
        )
        _TOOL_REGISTRATIONS.append(td)
        return func

    return decorator


# 模块级单例
registry = ToolRegistry()


__all__ = [
    "ToolDef",
    "ToolRegistry",
    "tool",
    "registry",
    "_TOOL_REGISTRATIONS",
]

# --- 目录扫描 ---


def _fs_path_to_dotted(fs_path: str) -> str:
    """将文件系统路径转换为点号路径.

    'src/dezhu_agent/tools' → 'dezhu_agent.tools'
    'dezhu_agent/tools' → 'dezhu_agent.tools'
    """
    path = fs_path.replace("/", ".").lstrip(".")
    # 去掉 'src.' 前缀（src 是项目 source 目录约定）
    if path.startswith("src."):
        path = path[4:]
    return path


def scan_tools(pkg_fs_path: str, target_registry: ToolRegistry | None = None) -> None:
    """扫描指定文件系统路径下的所有工具模块并注册.

    导入每个 .py 文件会触发 @tool 装饰器，将 ToolDef 追加到 _TOOL_REGISTRATIONS。
    扫描完成后统一注册到 registry（或指定的 target_registry）。
    """
    reg = target_registry or registry

    # 只扫描新文件（清空之前的注册收集，防止重复注册）
    _TOOL_REGISTRATIONS.clear()

    pkg_dotted = _fs_path_to_dotted(pkg_fs_path)

    # 先导入 package 本身
    try:
        pkg = importlib.import_module(pkg_dotted)
    except ImportError:
        return  # package 不存在，静默跳过

    pkg_dir = Path(pkg.__file__).resolve().parent if pkg.__file__ else Path(pkg_fs_path)
    if not pkg_dir.is_dir():
        return

    # 遍历目录下的 .py 文件（排除 __init__.py）
    for entry in sorted(pkg_dir.iterdir()):
        if entry.suffix != ".py" or entry.stem == "__init__":
            continue
        module_name = f"{pkg_dotted}.{entry.stem}"
        try:
            importlib.import_module(module_name)
        except ImportError:
            continue  # 静默跳过导入失败的模块

    # 将所有收集到的 ToolDef 注册到 registry
    for td in _TOOL_REGISTRATIONS:
        reg.register(td)


# --- 模块级初始化 ---


def _init_registry() -> None:
    """模块加载时自动扫描并应用配置."""
    from dezhu_agent.tools_config import apply_config, load_config, load_scan_paths

    config = load_config()
    scan_paths = load_scan_paths(config)

    for fs_path in scan_paths:
        scan_tools(fs_path)

    apply_config(registry, config)


# 模块导入时自动执行初始化
_init_registry()
