"""终端命令执行工具."""

from __future__ import annotations

import shlex
import subprocess
from typing import Any

from dezhu_agent.models.tool import BaseTool, tool_error
from dezhu_agent.utils.tool_decorator import register_tool

# 黑名单命令 (按第一个 token 匹配)
_BLOCKED_COMMANDS = frozenset(
    {
        "mkfs",
        "shutdown",
        "reboot",
        "halt",
        "poweroff",
        "init",
    }
)

# 危险子命令模式: (命令, 参数前缀)
_BLOCKED_PATTERNS: list[tuple[str, str]] = [
    ("rm", "-rf"),
    ("rm", "-r"),
    ("dd", "if="),
]


def _is_blocked(cmd_str: str) -> str | None:
    """检查命令是否在黑名单中，返回拦截原因或 None."""
    try:
        tokens = shlex.split(cmd_str)
    except ValueError:
        # 无法解析（如未闭合引号），为安全起见拦截
        return "unparseable command"

    if not tokens:
        return None

    base = tokens[0]

    # 检查基础命令黑名单
    if base in _BLOCKED_COMMANDS:
        return base

    # 检查危险子命令模式
    for blocked_cmd, flag_prefix in _BLOCKED_PATTERNS:
        if base == blocked_cmd:
            for t in tokens[1:]:
                if t.startswith(flag_prefix):
                    # 额外检查: rm -rf 目标是否包含 / 或 /*
                    if blocked_cmd == "rm":
                        for t2 in tokens[1:]:
                            if t2 in ("/", "/*") or t2.startswith("/ "):
                                return f"rm targeting root: {t2}"
                    return f"{blocked_cmd} {flag_prefix}..."

    return None


@register_tool(
    name="terminal",
    description="Run a shell command and return its output.",
    parameters={
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The shell command to execute",
            }
        },
        "required": ["command"],
    },
)
class TerminalTool(BaseTool):
    """在 shell 中执行命令并返回输出."""

    def execute(self, **kwargs: Any) -> str:
        command: str = kwargs.get("command", "")

        reason = _is_blocked(command)
        if reason:
            return tool_error(f"Blocked: {reason}")

        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            output = result.stdout + result.stderr
            return output[:10000] if output else "(no output)"
        except subprocess.TimeoutExpired:
            return "(command timed out after 30s)"
        except Exception as exc:
            return f"(error: {exc})"
