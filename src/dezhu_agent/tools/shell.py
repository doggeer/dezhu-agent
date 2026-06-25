"""工具：执行 shell 命令."""

from __future__ import annotations

import shlex
import subprocess

from dezhu_agent.tools import tool

_OUTPUT_MAX_CHARS = 10000
_SHELL_TIMEOUT = 60


@tool(
    name="shell",
    description="在系统 shell 中执行一条命令并返回输出。"
    "适用于运行脚本、编译、测试、文件操作等。"
    "命令在项目根目录下执行。"
    "超时 60 秒，输出截断至 10000 字符。",
)
def shell(command: str) -> str:
    """执行 shell 命令，返回 stdout + stderr."""
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=_SHELL_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return f"Error: command timed out after {_SHELL_TIMEOUT}s\nCommand: {shlex.join([command])}"
    except OSError as e:
        return f"Error: failed to execute command: {e}"

    output = ""
    if result.stdout:
        output += result.stdout
    if result.stderr:
        if output:
            output += "\n--- stderr ---\n"
        output += result.stderr

    if not output:
        output = "(no output)"

    if result.returncode != 0:
        output += f"\n(exit code: {result.returncode})"

    if len(output) > _OUTPUT_MAX_CHARS:
        output = output[:_OUTPUT_MAX_CHARS] + f"\n... (truncated, {len(output)} total chars)"

    return output
