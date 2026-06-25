"""配置文件加载：YAML 配置解析 + 应用到 ToolRegistry."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from dezhu_agent.config import PROJECT_ROOT
from dezhu_agent.tools import ToolRegistry

# 默认配置文件路径（可被环境变量覆盖）
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "tools_config.yaml"
CONFIG_PATH = Path(os.environ.get("TOOLS_CONFIG_PATH", str(DEFAULT_CONFIG_PATH)))


def load_config(path: str | None = None) -> dict[str, Any]:
    """加载 YAML 配置文件.

    B2: 文件不存在 → 返回空 dict（视为全启用）
    E4: 格式错误 → 抛出异常（fail-fast）
    """
    config_path = Path(path) if path else CONFIG_PATH
    if not config_path.exists():
        return {}

    try:
        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else {}
    except (yaml.YAMLError, OSError) as e:
        raise RuntimeError(f"Failed to parse config file {config_path}: {e}") from e


def load_scan_paths(config: dict[str, Any]) -> list[str]:
    """从配置中读取扫描路径.

    默认返回 ['src/dezhu_agent/tools']（文件系统路径）。
    """
    paths = config.get("scan_paths", ["src/dezhu_agent/tools"])
    return list(paths) if isinstance(paths, list) else [str(paths)]


def _fs_path_to_dotted(fs_path: str) -> str:
    """将文件系统路径转换为点号路径.

    'src/dezhu_agent/tools' → 'dezhu_agent.tools'
    'dezhu_agent/tools' → 'dezhu_agent.tools'
    """
    path = fs_path.replace("/", ".").lstrip(".")
    if path.startswith("src."):
        path = path[4:]
    return path


def apply_config(registry: ToolRegistry, config: dict[str, Any]) -> None:
    """将配置中的启用/禁用设置应用到 registry.

    B4: 配置中列举了未注册的工具名 → 静默忽略
    """
    tools_config = config.get("tools", {})
    if not isinstance(tools_config, dict):
        return

    for tool_name, settings in tools_config.items():
        if not isinstance(settings, dict):
            continue
        enabled = settings.get("enabled", True)
        if not enabled:
            registry.disable(tool_name)
        else:
            registry.enable(tool_name)
