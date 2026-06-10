"""文件工具共用的路径校验逻辑."""

from __future__ import annotations

from pathlib import Path

from dezhu_agent.config import get_config


def validate_path(path_raw: str) -> Path:
    """校验并解析路径, 确保其在 ALLOWED_PATHS 允许的目录内.

    Raises:
        ValueError: 路径不在允许范围内.
    """
    path = Path(path_raw)
    if not path.is_absolute():
        path = Path.cwd() / path
    path = path.resolve()

    allowed_raw = get_config().ALLOWED_PATHS
    allowed_dirs: list[Path] = []
    for p in allowed_raw.split(","):
        p = p.strip()
        allowed_dir = Path(p).resolve() if Path(p).is_absolute() else (Path.cwd() / p).resolve()
        allowed_dirs.append(allowed_dir)

    for allowed in allowed_dirs:
        try:
            path.relative_to(allowed)
            return path
        except ValueError:
            continue

    raise ValueError(f"Path '{path_raw}' is not within allowed directories: {allowed_raw}")
