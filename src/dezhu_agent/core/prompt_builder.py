"""Prompt Builder —— 分 6 层组装 system prompt, 每层独立维护."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from dezhu_agent.config import get_config

DEFAULT_SOUL = """\
你是一个简洁、直接的编程助手。
回答尽量简短。
不要在每次回复结尾加总结。"""


def build_system_prompt(
    model: str = "unknown",
) -> str:
    """按优先级顺序组装 6 层 prompt, 返回完整字符串."""
    parts: list[str] = []

    identity = _build_identity()
    if identity:
        parts.append(identity)

    behavior = _build_behavior_guidance()
    if behavior:
        parts.append(behavior)

    memory = _build_memory()
    if memory:
        parts.append(memory)

    skills = _build_skills()
    if skills:
        parts.append(skills)

    project = _build_project_context()
    if project:
        parts.append(project)

    timestamp = _build_timestamp_info(model)
    if timestamp:
        parts.append(timestamp)

    return "\n\n".join(parts)


# ---- Layer 1: 人设 ----
def _build_identity() -> str:
    soul_path = _get_hermes_dir() / "SOUL.md"
    if soul_path.is_file():
        return _read_and_truncate(soul_path)
    return DEFAULT_SOUL


# ---- Layer 2: 行为指导 ----
def _build_behavior_guidance() -> str:
    return ""


# ---- Layer 3: 记忆 ----
def _build_memory() -> str:
    hermes_dir = _get_hermes_dir()
    parts: list[str] = []

    memory_path = hermes_dir / "MEMORY.md"
    if memory_path.is_file():
        parts.append(f"# Memory\n{_read_and_truncate(memory_path)}")

    user_path = hermes_dir / "USER.md"
    if user_path.is_file():
        parts.append(f"# User Preferences\n{_read_and_truncate(user_path)}")

    return "\n\n".join(parts)


# ---- Layer 4: 技能清单 ----
def _build_skills() -> str:
    """读取 hermes/skills/ 目录下的技能文件, 当前为空扩展点."""
    skills_dir = _get_hermes_dir() / "skills"
    if not skills_dir.is_dir():
        return ""
    return ""


# ---- Layer 5: 项目配置 ----
def _build_project_context() -> str:
    content = _find_project_config_file()
    if content is None:
        return ""
    return f"# Project Context\n{content}"


# ---- Layer 6: 时间戳 + 模型信息 ----
def _build_timestamp_info(model: str) -> str:
    tz = datetime.now().astimezone().tzinfo
    offset = datetime.now(tz).strftime("%z") if tz else "+0000"
    now = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
    return f"Conversation started: {now} (UTC{offset}). Model: {model}."


# ---- 辅助函数 ----
def _get_hermes_dir() -> Path:
    hermes_dir = get_config().HERMES_DIR
    p = Path(hermes_dir)
    if not p.is_absolute():
        project_root = Path(__file__).resolve().parents[3]
        p = project_root / p
    return p


def _read_and_truncate(path: Path) -> str:
    max_chars = get_config().PROMPT_MAX_FILE_CHARS
    content = path.read_text(encoding="utf-8")
    if len(content) > max_chars:
        content = content[:max_chars] + f"\n\n... (truncated, max {max_chars} chars)"
    return content


def _find_project_config_file() -> str | None:
    cwd = Path.cwd()
    git_root = _find_git_root(cwd)

    # Priority 1: .hermes.md / HERMES.md — 从 cwd 向上搜到 git root
    search_dir = cwd.resolve()
    while True:
        for name in (".hermes.md", "HERMES.md"):
            candidate = search_dir / name
            if candidate.is_file():
                return _read_and_truncate(candidate)
        if search_dir == git_root or search_dir == git_root.parent:
            break
        search_dir = search_dir.parent

    # Priority 2-4: 仅 cwd
    for name in ("AGENTS.md", "agents.md", "CLAUDE.md", "claude.md", ".cursorrules"):
        candidate = search_dir / name
        if candidate.is_file():
            return _read_and_truncate(candidate)

    return None


def _find_git_root(cwd: Path) -> Path:
    for parent in (cwd, *cwd.parents):
        if (parent / ".git").is_dir():
            return parent
    return cwd
