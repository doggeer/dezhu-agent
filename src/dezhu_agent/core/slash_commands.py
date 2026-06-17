"""内置命令处理: /messages, /usages, /help, quit/exit."""

from __future__ import annotations

from enum import Enum
from typing import Any

from dezhu_agent.config import Settings
from dezhu_agent.core.compression import ContextCompressor

_CONTENT_PREVIEW_CHARS = 80
_BAR_WIDTH = 20
_NEAR_LIMIT_RATIO = 0.9


class CommandResult(Enum):
    """handle_command 的三态返回值."""

    QUIT = "quit"
    HANDLED = "handled"
    PASS = "pass"


def handle_command(
    user_input: str,
    messages: list[dict[str, Any]],
    compressor: ContextCompressor,
    config: Settings,
) -> CommandResult:
    """处理内置命令, 返回 CommandResult 告诉 agent_loop 下一步动作."""
    cmd = user_input.strip().lower()

    # 退出命令 (可带 / 前缀)
    if cmd in ("quit", "exit", "/quit", "/exit"):
        return CommandResult.QUIT

    # 空输入
    if not cmd:
        return CommandResult.QUIT

    # Slash 命令
    if cmd.startswith("/"):
        if cmd == "/messages":
            _cmd_messages(messages, compressor)
        elif cmd == "/usages":
            _cmd_usages(messages, compressor, config)
        elif cmd == "/help":
            _cmd_help()
        else:
            print(f"Unknown command: {cmd}  (try /help)")
        print()
        return CommandResult.HANDLED

    return CommandResult.PASS


def _cmd_messages(
    messages: list[dict[str, Any]],
    compressor: ContextCompressor,
) -> None:
    """打印当前会话的对话列表, 每条带 token 估算."""
    total = len(messages)
    print(f"=== Messages ({total} total) ===")

    if not messages:
        print("(empty)")
        return

    for i, msg in enumerate(messages, 1):
        role = msg.get("role", "?")
        content = msg.get("content", "")
        tool_calls = msg.get("tool_calls")
        tool_call_id = msg.get("tool_call_id")

        # Token 估算
        tokens = compressor.estimate_tokens([msg])

        # 角色标签
        role_label = role.ljust(9)

        # Content 预览
        preview = ""
        if role == "assistant" and tool_calls:
            n = len(tool_calls)
            preview = f"[{n} tool_call{'s' if n > 1 else ''}]"
        elif role == "tool" and tool_call_id:
            short_id = tool_call_id[:8]
            text_preview = _truncate(content, _CONTENT_PREVIEW_CHARS)
            preview = f"({short_id}...) {text_preview}"
        else:
            preview = _truncate(content, _CONTENT_PREVIEW_CHARS)

        print(f"  [{i:>2}] {role_label} ({tokens:>5} tokens): {preview}")


def _cmd_usages(
    messages: list[dict[str, Any]],
    compressor: ContextCompressor,
    config: Settings,
) -> None:
    """打印上下文窗口用量, 包含 token 估算、阈值和状态."""
    estimated = compressor.estimate_tokens(messages)
    max_context = config.MODEL_MAX_CONTEXT_TOKENS
    threshold = config.COMPRESSION_THRESHOLD

    pct = min(100.0, estimated / max_context * 100) if max_context else 0.0

    # 状态判定
    if estimated < threshold:
        status = "OK (well below threshold)"
    elif estimated < max_context * _NEAR_LIMIT_RATIO:
        status = "⚠️ Compression needed"
    else:
        status = "⚠️ Near limit"

    bar_used = int(pct / 100 * _BAR_WIDTH)
    bar_threshold = int(threshold / max_context * _BAR_WIDTH) if max_context else 0

    usage_bar = "#" * bar_used + "·" * (_BAR_WIDTH - bar_used)
    threshold_bar = "=" * bar_threshold + "·" * (_BAR_WIDTH - bar_threshold)

    print("=== Context Usage ===")
    print(f"  Estimate:   {estimated:,} / {max_context:,} tokens  {pct:.1f}%  [{usage_bar}]")
    print(f"  Threshold:  {threshold:,} tokens                    [{threshold_bar}]")
    print(f"  Status:     {status}")


def _cmd_help() -> None:
    """打印帮助信息."""
    print("=== 德柱Agent (dezhu-agent) ===")
    print("Python AI Agent, powered by DeepSeek.")
    print()
    print("Commands:")
    print("  /messages  查看当前会话的对话列表")
    print("  /usages    查看上下文窗口用量")
    print("  /help      显示帮助信息")
    print("  quit/exit  退出会话")


def _truncate(text: str, max_chars: int) -> str:
    """截断文本到 max_chars 字符, 超出加 '...'."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "..."
