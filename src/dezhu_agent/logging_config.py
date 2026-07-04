"""后台日志系统：统一 logger、session 上下文、按日轮转."""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path
from typing import ClassVar

_MAX_MESSAGE_CHARS = 50 * 1024  # 50KB（按字符数近似截断，ASCII 下等价于字节）


class _SessionFilter(logging.Filter):
    """将当前 session_id 注入每条 LogRecord。"""

    session_id: ClassVar[str] = ""

    def filter(self, record: logging.LogRecord) -> bool:
        sid = self.session_id
        record.session = f"[session={sid[:8]}]" if sid else "[session=-]"
        return True


class _TruncatingFormatter(logging.Formatter):
    """超过 _MAX_MESSAGE_BYTES 的日志行自动截断。"""

    def format(self, record: logging.LogRecord) -> str:
        formatted = super().format(record)
        if len(formatted) > _MAX_MESSAGE_CHARS:
            formatted = (
                formatted[:_MAX_MESSAGE_CHARS]
                + f"\n… [TRUNCATED {_MAX_MESSAGE_CHARS // 1024}KB]"
            )
        return formatted


def _log_level_to_int(level: str) -> int:
    """将字符串日志级别转换为 int."""
    return getattr(logging, level.upper(), logging.INFO)


_log_initialized = False


def init_logging(
    log_dir: str = "logs",
    log_level: int = logging.INFO,
    force: bool = False,
) -> None:
    """初始化日志系统.

    创建日志目录，配置 TimedRotatingFileHandler（每天轮转），
    设置统一的格式和 session 过滤器。
    多次调用安全（幂等），除非 force=True。

    Args:
        log_dir: 日志目录路径。
        log_level: 日志级别（logging.DEBUG / INFO / WARNING / ERROR）。
        force: 强制重新初始化（测试用）。
    """
    global _log_initialized
    if _log_initialized and not force:
        return

    # 清理已有 handler（支持 force 重初始化）
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)

    log_path = Path(log_dir)

    try:
        log_path.mkdir(parents=True, exist_ok=True)
    except OSError:
        # 目录不可写 → 降级到 stderr（WARNING 级别以上）
        handler: logging.Handler = logging.StreamHandler(sys.stderr)
        handler.setLevel(logging.WARNING)
        print(
            "⚠️ 日志目录不可写，日志降级输出到 stderr",
            file=sys.stderr,
        )
    else:
        # TimedRotatingFileHandler：每天 00:00 轮转
        log_filename = str(log_path / "dezhu-agent.log")

        def _namer(default_name: str) -> str:
            """将轮转文件名从 dezhu-agent.log.YYYY-MM-DD 改为 dezhu-agent-YYYY-MM-DD.log."""
            # default_name 示例: .../logs/dezhu-agent.log.2025-07-04
            parts = default_name.rsplit(".log.", 1)
            if len(parts) == 2:
                return f"{parts[0]}-{parts[1]}.log"
            return default_name

        handler = logging.handlers.TimedRotatingFileHandler(
            filename=log_filename,
            when="midnight",
            interval=1,
            backupCount=30,
            encoding="utf-8",
        )
        handler.suffix = "%Y-%m-%d"
        handler.namer = _namer
        handler.setLevel(log_level)

    handler.setFormatter(
        _TruncatingFormatter(
            "%(asctime)s | %(levelname)-5s | %(session)s | %(name)s:%(lineno)d | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    handler.addFilter(_SessionFilter())

    root.setLevel(log_level)
    root.addHandler(handler)
    _log_initialized = True


def get_logger(name: str) -> logging.Logger:
    """获取模块级 logger。"""
    return logging.getLogger(name)


def set_session_context(session_id: str) -> None:
    """设置当前 session 上下文（截取前 8 位短 ID 注入每条日志）。"""
    _SessionFilter.session_id = session_id or ""


def get_log_level() -> int:
    """获取 root logger 当前日志级别。"""
    return logging.getLogger().level
