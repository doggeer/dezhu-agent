"""终端命令执行工具."""

from __future__ import annotations

import json
import subprocess
from typing import Any

from dezhu_agent.models.tool import BaseTool
from dezhu_agent.utils.tool_decorator import register_tool

BLOCKED_COMMANDS = [
    "rm -rf /",
    "mkfs",
    "dd if=",
    "shutdown",
    "reboot",
]


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

        for blocked in BLOCKED_COMMANDS:
            if blocked in command:
                return json.dumps({"error": f"Blocked: {blocked}"})

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
