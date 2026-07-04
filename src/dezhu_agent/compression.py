"""上下文压缩子系统：三层压缩 + TaskState + 编排器."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from openai import OpenAI

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 错误类
# ---------------------------------------------------------------------------


class CompressionStuckError(RuntimeError):
    """压缩无法有效缩小消息列表（降幅 < 10%）。"""

    def __init__(self, before: int, after: int) -> None:
        self.before = before
        self.after = after
        super().__init__(
            f"Compression stuck: {before} → {after} tokens "
            f"(reduction: {(1 - after / before) * 100:.1f}%)"
        )


# ---------------------------------------------------------------------------
# 配置数据类
# ---------------------------------------------------------------------------


@dataclass
class CompressionConfig:
    """压缩配置——聚合 config.py 中所有压缩相关常量。"""

    enabled: bool = True
    trigger_ratio: float = 0.7
    preflight_ratio: float = 0.8
    window_size: int = 200_000
    aux_model: str = "deepseek-v4-flash"
    aux_api_key: str | None = None
    aux_base_url: str = "https://api.deepseek.com"
    max_rounds_layer1: int = 20
    head_rounds_layer2: int = 3
    tail_tokens_layer2: int = 20_000
    aux_timeout: float = 5.0

    @property
    def trigger_threshold(self) -> int:
        return int(self.window_size * self.trigger_ratio)

    @property
    def preflight_threshold(self) -> int:
        return int(self.window_size * self.preflight_ratio)


@dataclass
class CompressionResult:
    """压缩结果。"""

    before_tokens: int
    after_tokens: int
    layers_applied: list[int]  # 实际执行了的层编号 [1, 2, 3]
    after_dicts: list[dict[str, Any]] = field(default_factory=list)  # 压缩后的消息列表
    new_session_id: str = ""
    degraded: bool = False  # Layer 3 降级时置 True
    degrade_reason: str = ""


# ---------------------------------------------------------------------------
# TaskState —— 不进消息流的任务状态
# ---------------------------------------------------------------------------


@dataclass
class TaskState:
    """不进消息流的任务状态，每轮拼入 system prompt 末尾，永不压缩。"""

    goal: str = ""
    todos: list[dict] = field(default_factory=list)
    # 每个 todo: {"id": str, "subject": str, "status": "pending"|"in_progress"|"completed"}

    _status_mark: dict[str, str] = field(
        default_factory=lambda: {
            "pending": "[ ]",
            "in_progress": "[~]",
            "completed": "[x]",
        },
        init=False,
        repr=False,
    )

    def render(self) -> str:
        """渲染为 system prompt 追加块。

        示例输出:
            # Task State (live, never compressed)
            ## Goal
            读取 agents 下的所有文件
            ## TODO
            [x] (t1) 列出 agents 目录
            [~] (t2) 读 s01_agent_loop.py
            [ ] (t3) 读 s02_tool_system.py
        """
        lines: list[str] = []
        lines.append("# Task State (live, never compressed)")

        if self.goal:
            lines.append("## Goal")
            lines.append(self.goal)

        if self.todos:
            lines.append("## TODO")
            for t in self.todos:
                tid = t.get("id", "?")
                subject = t.get("subject", "")
                status = t.get("status", "pending")
                mark = self._status_mark.get(status, "[ ]")
                lines.append(f"{mark} ({tid}) {subject}")

        return "\n".join(lines)


# 模块级 TaskState 单例
_task_state = TaskState()


def get_task_state() -> TaskState:
    """获取模块级 TaskState 单例。"""
    return _task_state


def reset_task_state() -> None:
    """重置 TaskState 为新实例。"""
    global _task_state
    _task_state = TaskState()


# ---------------------------------------------------------------------------
# Token 估算
# ---------------------------------------------------------------------------


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    """估算消息列表的 token 数。

    使用字符数/4 的近似算法（与 tiktoken 误差在 ±20% 内）。
    接受的消息格式与 messages_to_api_messages 一致。

    对于 tool_calls（函数调用参数），额外计入 JSON 序列化后的 token 估算。
    """
    total_chars = 0
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            total_chars += len(content)
        elif content is not None:
            total_chars += len(str(content))

        # role 字段
        role = msg.get("role", "")
        total_chars += len(role)

        # tool_calls（assistant 消息中的函数调用）
        tool_calls = msg.get("tool_calls")
        if tool_calls:
            if isinstance(tool_calls, list):
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    total_chars += len(fn.get("name", ""))
                    total_chars += len(fn.get("arguments", ""))
            elif isinstance(tool_calls, str):
                total_chars += len(tool_calls)

        # tool_call_id 和 name（tool 消息）
        total_chars += len(str(msg.get("tool_call_id", "")))
        total_chars += len(str(msg.get("name", "")))

    return max(1, total_chars // 4)


# ---------------------------------------------------------------------------
# Layer 1: 工具输出裁剪
# ---------------------------------------------------------------------------


def truncate_old_tool_outputs(
    messages: list[dict[str, Any]], max_rounds: int = 20
) -> list[dict[str, Any]]:
    """将超过 max_rounds 轮之前的 tool 消息 content 替换为占位文本。

    保留最近 max_rounds 轮的工具输出，更早的轮次中的 tool 消息被裁剪。
    不删除消息（保留 tool_call_id / name / role），只替换 content。
    "轮"以 user 角色消息为边界：每遇到一条 user 消息，round_counter 加 1。
    所有 role != "user" 的消息属于当前轮。

    返回新列表（不修改原始列表）。
    """
    if not messages:
        return []

    # 第一遍：给每条消息标记所属轮次
    round_of_msg: list[int] = []
    current_round = 0
    for msg in messages:
        if msg.get("role") == "user":
            current_round += 1
        round_of_msg.append(current_round)

    total_rounds = current_round
    cutoff_round = total_rounds - max_rounds  # 轮次 ≤ cutoff_round 的被裁剪

    # 第二遍：裁剪旧轮次的 tool 消息
    result: list[dict[str, Any]] = []
    for i, msg in enumerate(messages):
        role = msg.get("role", "")
        if role == "tool" and round_of_msg[i] <= cutoff_round:
            new_msg = dict(msg)
            new_msg["content"] = "[Old tool output cleared]"
            result.append(new_msg)
        else:
            result.append(msg)

    return result


# ---------------------------------------------------------------------------
# Layer 2: 边界查找与切分
# ---------------------------------------------------------------------------


def _align_to_assistant_boundary(
    messages: list[dict[str, Any]], index: int
) -> int:
    """将切点 index 吸附到下一个非 tool 消息。

    若 messages[index] 的 role == "tool"，向后移动直到找到非 tool 消息，
    确保 assistant.tool_calls 与 tool 响应成对保留。
    """
    while index < len(messages) and messages[index].get("role") == "tool":
        index += 1
    return index


def find_boundaries(
    messages: list[dict[str, Any]],
    head_rounds: int = 3,
    tail_tokens: int = 20_000,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """将消息列表切分为 (head, middle, tail)。

    head: 系统消息 + 前 head_rounds 个用户-助手交互轮次
    tail: 最近约 tail_tokens 个 token 的消息
    middle: 其余部分

    边界对齐：head 切点若落在 tool 消息上，吸附到下一个非 tool 消息。
    若 head 和 tail 重叠（总消息太少），middle 为空。
    """
    if not messages:
        return [], [], []

    # ---- 找 head 结束位置 ----
    head_end = 0
    user_rounds_seen = 0

    for i, msg in enumerate(messages):
        if msg.get("role") == "user":
            user_rounds_seen += 1
            if user_rounds_seen > head_rounds:
                # head 结束于这条 user 消息之前
                head_end = i
                break
    else:
        # 遍历完所有消息也没超过 head_rounds——全部作为 head，无 middle
        return list(messages), [], []

    # 边界对齐
    head_end = _align_to_assistant_boundary(messages, head_end)

    # ---- 找 tail 开始位置（从末尾往前扫描） ----
    tail_start = len(messages)
    accumulated = 0

    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        # 估算单条消息的 token（快速近似）
        content = msg.get("content") or ""
        msg_tokens = len(str(content)) // 4 + 2  # +2 给 role 等元数据
        accumulated += msg_tokens
        if accumulated >= tail_tokens:
            tail_start = i
            break
    else:
        # 所有消息都在 tail 范围内——全部作为 tail，无 middle
        tail_start = 0

    # 边界对齐（tail 的开始位置也不能是 tool 消息）
    tail_start = _align_to_assistant_boundary(messages, tail_start)

    # ---- 切分 ----
    if head_end >= tail_start:
        # head 和 tail 重叠或紧挨着——无中间段
        return list(messages), [], []

    head = list(messages[:head_end])
    middle = list(messages[head_end:tail_start])
    tail = list(messages[tail_start:])

    return head, middle, tail


# ---------------------------------------------------------------------------
# Layer 3: LLM 摘要生成器
# ---------------------------------------------------------------------------


_SUMMARY_SYSTEM_PROMPT = """You are a context summarizer. Your job is to read a conversation segment and produce a structured JSON summary. The summary must be accurate, concise, and preserve all information needed to continue the task.

Output ONLY a valid JSON object with these fields:
{
  "goal": "What the current task is trying to accomplish (1 sentence)",
  "progress": "What has been completed so far (bullet points)",
  "key_decisions": "Important decisions made (bullet points)",
  "files_modified": "Files that were read, modified, or created (list of paths)",
  "next_steps": "What should happen next (bullet points)"
}

If any field has no content, use "(无)" as the value.

Do NOT include any text outside the JSON. Do NOT wrap in markdown code blocks."""


def _build_summary_request(
    middle: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """构建摘要请求的消息列表。"""
    # 将中间段序列化为可读文本
    lines: list[str] = []
    for msg in middle:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        name = msg.get("name", "")
        tool_calls = msg.get("tool_calls")

        if role == "user":
            lines.append(f"[User]: {content}")
        elif role == "assistant":
            if content:
                lines.append(f"[Assistant]: {content}")
            if tool_calls:
                for tc in (tool_calls if isinstance(tool_calls, list) else []):
                    fn = tc.get("function", {})
                    lines.append(f"[Tool Call]: {fn.get('name', '')}({fn.get('arguments', '')})")
        elif role == "tool":
            lines.append(f"[Tool Result ({name})]: {str(content)[:500]}")  # 截断过长输出

    conversation_text = "\n".join(lines)

    return [
        {"role": "system", "content": _SUMMARY_SYSTEM_PROMPT},
        {"role": "user", "content": f"Summarize this conversation segment:\n\n{conversation_text}"},
    ]


def summarize_middle(
    middle: list[dict[str, Any]],
    config: CompressionConfig,
) -> dict[str, str] | None:
    """调用辅助 LLM 对中间段生成结构化摘要。

    Returns:
        摘要 dict（字段: goal/progress/key_decisions/files_modified/next_steps），
        若 LLM 调用失败或返回格式无法解析则返回 None。
    """
    if not middle:
        return None

    try:
        client = OpenAI(
            api_key=config.aux_api_key or "sk-placeholder",
            base_url=config.aux_base_url,
            timeout=config.aux_timeout,
        )

        request_messages = _build_summary_request(middle)
        response = client.chat.completions.create(
            model=config.aux_model,
            messages=request_messages,
            temperature=0.0,
            max_tokens=1000,
        )

        raw = response.choices[0].message.content or ""
        # 尝试提取 JSON（可能被 markdown 代码块包裹）
        raw = raw.strip()
        if raw.startswith("```"):
            # 去掉 ```json 和结尾的 ```
            raw = raw.split("\n", 1)[-1] if "\n" in raw else raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3]
            raw = raw.strip()

        result = json.loads(raw)
        if not isinstance(result, dict):
            return None

        # 确保所有字段都存在
        for field in ("goal", "progress", "key_decisions", "files_modified", "next_steps"):
            if field not in result or not result[field]:
                result[field] = "(无)"

        return result

    except Exception:
        logger.warning("Layer 3 summarization failed, degrading to Layer 1+2 only", exc_info=True)
        return None


def _summary_to_message(summary: dict[str, str]) -> dict[str, Any]:
    """将摘要 dict 转换为一条 assistant 消息（替换被压缩的中间段）。"""
    text_parts = [
        f"## Goal\n{summary.get('goal', '(无)')}",
        f"## Progress\n{summary.get('progress', '(无)')}",
        f"## Key Decisions\n{summary.get('key_decisions', '(无)')}",
        f"## Files Modified\n{summary.get('files_modified', '(无)')}",
        f"## Next Steps\n{summary.get('next_steps', '(无)')}",
    ]
    return {
        "role": "assistant",
        "content": "[Context Compression Summary]\n\n" + "\n\n".join(text_parts),
    }


# ---------------------------------------------------------------------------
# 压缩编排器
# ---------------------------------------------------------------------------

_COMPRESSION_MIN_SHRINK = 0.9  # 必须降到原值 90% 以下


def compress(
    messages: list[dict[str, Any]],
    config: CompressionConfig,
) -> CompressionResult:
    """串联三层压缩，返回 CompressionResult。

    递进执行 Layer 1 → Layer 2 → Layer 3。
    每层执行后重新估算 token 数，若已低于触发阈值则跳过后续层。
    若压缩后 token 数未降到原值 COMPRESSION_MIN_SHRINK 以下，
    抛出 CompressionStuckError。

    异常安全：任何异常发生时回退到原始 messages（不丢失数据）。

    Raises:
        CompressionStuckError: 压缩无法有效缩小消息列表。
    """
    before = estimate_tokens(messages)
    threshold = config.trigger_threshold

    # 若本就低于阈值，不压缩
    if before <= threshold:
        return CompressionResult(
            before_tokens=before,
            after_tokens=before,
            layers_applied=[],
            after_dicts=list(messages),
        )

    original = list(messages)  # 深拷贝引用（消息 dict 本身不会被修改）
    current = list(messages)
    layers_applied: list[int] = []
    degraded = False
    degrade_reason = ""

    try:
        # ---- Layer 1: 工具输出裁剪 ----
        current = truncate_old_tool_outputs(current, config.max_rounds_layer1)
        layers_applied.append(1)
        after_l1 = estimate_tokens(current)
        if after_l1 <= threshold:
            return CompressionResult(
                before_tokens=before,
                after_tokens=after_l1,
                layers_applied=layers_applied,
                after_dicts=current,
            )

        # ---- Layer 2: 边界切分 ----
        head, middle, tail = find_boundaries(
            current, config.head_rounds_layer2, config.tail_tokens_layer2
        )

        if not middle:
            # 无中间段可压缩——但若 token 仍超阈值，视为 stuck
            after = estimate_tokens(current)
            if after >= int(before * _COMPRESSION_MIN_SHRINK) and after > threshold:
                raise CompressionStuckError(before, after)
            return CompressionResult(
                before_tokens=before,
                after_tokens=after,
                layers_applied=layers_applied,
                after_dicts=current,
            )

        layers_applied.append(2)

        # ---- Layer 3: LLM 摘要 ----
        summary = summarize_middle(middle, config)
        if summary is not None:
            summary_msg = _summary_to_message(summary)
            current = head + [summary_msg] + tail
            layers_applied.append(3)
        else:
            # 降级：仅 Layer 1 + 2（移除中间段，替换为简短占位）
            degraded = True
            degrade_reason = "Auxiliary LLM call failed or returned unparseable response"
            placeholder = {
                "role": "assistant",
                "content": "[Context compressed: intermediate conversation summarized due to token budget]",
            }
            current = head + [placeholder] + tail

        # ---- 验证压缩效果 ----
        after = estimate_tokens(current)
        if after >= int(before * _COMPRESSION_MIN_SHRINK):
            raise CompressionStuckError(before, after)

        return CompressionResult(
            before_tokens=before,
            after_tokens=after,
            layers_applied=layers_applied,
            after_dicts=current,
            degraded=degraded,
            degrade_reason=degrade_reason,
        )

    except CompressionStuckError:
        raise
    except Exception:
        # 未预期异常——回退到原始消息
        logger.warning("Compression failed, falling back to original messages", exc_info=True)
        return CompressionResult(
            before_tokens=before,
            after_tokens=before,
            layers_applied=[],
            after_dicts=list(messages),
            degraded=True,
            degrade_reason="Unexpected error during compression",
        )
