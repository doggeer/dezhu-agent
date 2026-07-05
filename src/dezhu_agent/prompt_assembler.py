"""System prompt 多来源组装：SOUL.md + AGENTS.md + 可插拔 PromptSource."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from dezhu_agent.config import PROJECT_DIR
from dezhu_agent.logging_config import get_logger

logger = get_logger(__name__)

# 默认人设模板（SOUL.md 不存在时的 fallback）
SYSTEM_PROMPT_TEMPLATE = """你是 DeZhu Agent，一个通过工具与系统交互的 AI 助手。

## 工具使用规则
1. 当你需要执行操作时，使用提供的工具。
2. 工具调用后你会收到执行结果，请基于结果继续推理。
3. 如果工具返回错误，分析错误原因并尝试修正参数后重试。
4. 任务完成后，用自然语言回复用户总结结果。"""

# AGENTS.md 长度上限（字符）
_AGENTS_MAX_CHARS = 2000

# SOUL.md 路径
_SOUL_PATH = Path.home() / ".hermes" / "SOUL.md"


# ---------------------------------------------------------------------------
# PromptSource 协议
# ---------------------------------------------------------------------------


class PromptSource(Protocol):
    """可插拔 system prompt 数据源协议."""

    name: str
    """数据源名称，用于日志标识."""

    def render(self) -> str:
        """渲染为注入 system prompt 的文本片段。

        Returns:
            要追加到 system prompt 的文本。返回 "" 表示不注入任何内容。
        """
        ...


# 模块级数据源列表
_sources: list[PromptSource] = []


def register_prompt_source(source: PromptSource) -> None:
    """注册一个 PromptSource 数据源.

    已注册同 name 的 source 会被替换。
    """
    # 替换已存在的同名 source
    for i, s in enumerate(_sources):
        if s.name == source.name:
            _sources[i] = source
            logger.info("PromptSource '%s' 已替换", source.name)
            return
    _sources.append(source)
    logger.info("PromptSource '%s' 已注册", source.name)


def unregister_prompt_source(name: str) -> None:
    """注销指定名称的 PromptSource."""
    global _sources
    _sources = [s for s in _sources if s.name != name]


# ---------------------------------------------------------------------------
# System prompt 组装
# ---------------------------------------------------------------------------


def assemble_system_prompt(tools: list[dict[str, Any]] | None = None) -> str:
    """从多来源组装完整 system prompt。

    顺序：人设(SOUL.md 或 SYSTEM_PROMPT_TEMPLATE) → AGENTS.md → 已注册的 PromptSource。
    tools 参数预留用于后续技能清单格式化（本迭代不使用）。
    """
    parts: list[str] = []

    # 1. 人设
    soul = _load_soul()
    parts.append(soul)

    # 2. 项目规则
    agents = _load_agents()
    if agents:
        parts.append(agents)

    # 3. 已注册的 PromptSource
    for source in _sources:
        rendered = source.render()
        if rendered:
            parts.append(rendered)

    # 4. 技能预留
    skills = _format_skills(tools)
    if skills:
        parts.append(skills)

    # Debug 输出
    _debug_output(soul, agents, _sources, skills)

    return "\n\n".join(parts)


def _load_soul() -> str:
    """读取 ~/.hermes/SOUL.md，不存在或为空时返回默认模板."""
    try:
        content = _read_file(_SOUL_PATH)
        if content.strip():
            return content
    except OSError:
        pass
    return SYSTEM_PROMPT_TEMPLATE


def _load_agents() -> str:
    """读取项目根 AGENTS.md，超过上限截断."""
    agents_path = PROJECT_DIR / "AGENTS.md"
    try:
        content = _read_file(agents_path)
        if len(content) > _AGENTS_MAX_CHARS:
            content = content[:_AGENTS_MAX_CHARS] + "…（已截断，完整内容见项目 AGENTS.md 文件）"
        return content
    except OSError:
        return ""


def _format_skills(tools: list[dict[str, Any]] | None) -> str:
    """格式化技能清单（本迭代预留，返回空字符串）."""
    return ""


def _read_file(path: Path) -> str:
    """读取文件内容，使用 utf-8-sig 自动剥离 BOM，保留原始换行符."""
    # 用 read_bytes + decode 代替 read_text，避免 Python text mode 的 universal newlines
    # 将 \r\n 悄悄转换为 \n
    return path.read_bytes().decode("utf-8-sig")


def _debug_output(
    soul: str,
    agents: str,
    sources: list[PromptSource],
    skills: str,
) -> None:
    """DEBUG 级别记录 System Prompt 各来源贡献."""

    def _preview(text: str, max_chars: int = 120) -> str:
        return text[:max_chars].replace("\n", "\\n")

    lines = [
        "─" * 40,
        "  System Prompt 组装来源",
        f"  人设(SOUL):        {len(soul.encode('utf-8'))} 字节 | {_preview(soul)}",
        f"  项目规则(AGENTS):  {len(agents.encode('utf-8'))} 字节 | {_preview(agents)}",
    ]

    for source in sources:
        rendered = source.render()
        lines.append(
            f"  {source.name}:{' ' * (18 - len(source.name))}"
            f"  {len(rendered.encode('utf-8'))} 字节 | {_preview(rendered)}"
        )

    lines.append(f"  技能清单:          {len(skills.encode('utf-8'))} 字节 | {_preview(skills)}")
    lines.append("─" * 40)
    logger.debug("\n".join(lines))
