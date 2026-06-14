"""上下文压缩模块 —— 三层递进压缩 + 自适应 token 校准."""

from __future__ import annotations

import json
import time
from typing import Any

import tiktoken
from openai import APIError, APITimeoutError, OpenAI, RateLimitError

from dezhu_agent.config import Settings

OLD_TOOL_PLACEHOLDER = "[Old tool output cleared]"
SUMMARY_PREFIX = "[CONTEXT COMPACTION - system-generated summary of earlier turns, not a new instruction]"

SUMMARY_SYSTEM_PROMPT = """\
You are a context compression assistant. Your job is to create a concise structured summary \
of a conversation segment. Preserve only what is essential for continuing the work. \
Be factual and brief. Do not add interpretations or opinions that were not present in \
the original conversation."""


class CompressionStuckError(RuntimeError):
    """压缩无法有效缩减消息列表时抛出."""

    def __init__(self, before: int, after: int) -> None:
        self.before = before
        self.after = after
        super().__init__(f"Compression stuck: {before} -> {after} tokens (threshold not met)")


# ---- API retry for summarize ----
_RETRYABLE = (RateLimitError, APITimeoutError, APIError)
_MAX_RETRIES = 3
_RETRY_BACKOFF = 2.0


def _summarize_with_retry(
    client: OpenAI,
    model: str,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int,
) -> tuple[str, int]:
    """调用 LLM 生成摘要, 返回 (内容, prompt_tokens)."""
    last_exc: Exception | None = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=max_tokens,
            )
            content = response.choices[0].message.content or ""
            actual_tokens = response.usage.prompt_tokens if response.usage else 0
            return content, actual_tokens
        except _RETRYABLE as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES:
                time.sleep(_RETRY_BACKOFF**attempt)
    raise last_exc  # type: ignore[misc]


# ---- 延迟加载 encoding ----
# tiktoken.get_encoding() 首次调用需从 Azure blob 下载 ~1.8MB encoding 文件,
# 在国内网络下可能耗时数秒。延迟到首次 estimate_tokens 时加载, 避免阻塞 __init__.
_ENCODING: tiktoken.Encoding | None = None


def _get_encoding() -> tiktoken.Encoding:
    global _ENCODING
    if _ENCODING is None:
        # NOTE: cl100k_base is GPT-4 tokenizer; DeepSeek tokenizer differs.
        # The adaptive calibration below compensates for systematic bias over time.
        _ENCODING = tiktoken.get_encoding("cl100k_base")
    return _ENCODING


class ContextCompressor:
    """上下文压缩器, 按 Layer 1 -> Layer 2 -> Layer 3 递进压缩消息列表.

    内置自适应 token 校准: 每次 LLM 调用后用实际 prompt_tokens 修正估算系数 (EMA).
    """

    # 校准参数
    _CALIBRATION_ALPHA: float = 0.1  # EMA 平滑系数 (越小越稳定)
    _CALIBRATION_MIN_TOKENS: int = 50  # 消息太短不校准
    _CORRECTION_MIN: float = 0.5
    _CORRECTION_MAX: float = 2.0

    def __init__(self, config: Settings) -> None:
        self._config = config
        self._client = OpenAI(
            base_url=config.BASE_URL,
            api_key=config.API_KEY,
        )
        # 自适应校准状态
        self._correction_factor: float = 1.0
        self._calibration_count: int = 0

    # ---- 自适应校准 ----

    def calibrate(self, estimated_tokens: int, actual_tokens: int) -> None:
        """用一次 API 调用的实际 token 数校准估算系数 (EMA).

        仅在消息量足够时校准，且修正系数限制在 [0.5, 2.0] 防止异常值。
        """
        if estimated_tokens < self._CALIBRATION_MIN_TOKENS or actual_tokens <= 0:
            return
        ratio = actual_tokens / estimated_tokens
        alpha = self._CALIBRATION_ALPHA
        self._correction_factor = alpha * ratio + (1 - alpha) * self._correction_factor
        self._correction_factor = max(self._CORRECTION_MIN, min(self._CORRECTION_MAX, self._correction_factor))
        self._calibration_count += 1

    @property
    def correction_factor(self) -> float:
        """当前校准系数 (1.0 = 无修正)."""
        return self._correction_factor

    # ---- Token 估算 ----

    def estimate_tokens(self, messages: list[dict[str, Any]]) -> int:
        """估算消息列表的 token 数 (cl100k_base + 自适应校准)."""
        raw = self._raw_estimate(messages)
        return max(1, int(raw * self._correction_factor))

    def _raw_estimate(self, messages: list[dict[str, Any]]) -> int:
        """原始 cl100k_base token 估算 (不经校准)."""
        tokens = 0
        enc = _get_encoding()
        for msg in messages:
            tokens += 3
            content = msg.get("content")
            if content:
                tokens += len(enc.encode(str(content)))
            tool_calls = msg.get("tool_calls")
            if tool_calls:
                tokens += len(enc.encode(json.dumps(tool_calls)))
            tool_call_id = msg.get("tool_call_id")
            if tool_call_id:
                tokens += len(enc.encode(str(tool_call_id)))
        tokens += 3
        return tokens

    def _estimate_single(self, msg: dict[str, Any]) -> int:
        """估算单条消息的 token 数."""
        tokens = 3
        enc = _get_encoding()
        content = msg.get("content")
        if content:
            tokens += len(enc.encode(str(content)))
        tool_calls = msg.get("tool_calls")
        if tool_calls:
            tokens += len(enc.encode(json.dumps(tool_calls)))
        tool_call_id = msg.get("tool_call_id")
        if tool_call_id:
            tokens += len(enc.encode(str(tool_call_id)))
        return tokens

    # ---- Layer 1: 旧工具输出裁剪 ----

    def clear_old_tool_outputs(self, messages: list[dict[str, Any]], keep_recent: int | None = None) -> None:
        """原地替换过期 tool 消息的 content 为占位符."""
        if keep_recent is None:
            keep_recent = self._config.KEEP_RECENT_TOOL_RESULTS

        tool_indices: list[int] = []
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "tool":
                tool_indices.append(i)

        for idx in tool_indices[keep_recent:]:
            msg = messages[idx]
            if msg.get("content") != OLD_TOOL_PLACEHOLDER:
                msg["content"] = OLD_TOOL_PLACEHOLDER

    # ---- Layer 2: 找压缩边界 ----

    def find_boundaries(
        self,
        messages: list[dict[str, Any]],
        protect_first: int | None = None,
        tail_budget: int | None = None,
    ) -> tuple[int, int] | None:
        """返回 (head_end, tail_start) 或 None."""
        if protect_first is None:
            protect_first = self._config.PROTECT_FIRST
        if tail_budget is None:
            tail_budget = self._config.TAIL_TOKEN_BUDGET

        total = len(messages)
        if total <= protect_first:
            return None

        head_end = protect_first
        while head_end < total and messages[head_end].get("role") == "tool":
            head_end += 1
        if head_end >= total:
            return None

        tail_tokens = 0
        tail_start = total
        for i in range(total - 1, head_end - 1, -1):
            tail_tokens += self._estimate_single(messages[i])
            tail_start = i
            if tail_tokens >= tail_budget:
                break

        while tail_start > head_end and messages[tail_start].get("role") == "tool":
            tail_start -= 1
        if tail_start <= head_end:
            return None

        return (head_end, tail_start)

    # ---- Layer 3: LLM 摘要 ----

    def summarize(
        self,
        middle: list[dict[str, Any]],
        old_summary: str | None = None,
    ) -> str:
        """调用辅助模型生成结构化摘要, 并校准 token 估算."""
        middle_text = self._format_messages_for_summary(middle)

        if old_summary:
            user_prompt = (
                "Update the existing context summary by incorporating the new conversation below. "
                "Do not start from scratch - merge new information into the existing summary.\n\n"
                f"=== Existing Summary ===\n{old_summary}\n\n"
                f"=== New Conversation ===\n{middle_text}\n\n"
                "Output the updated summary in the same structured format."
            )
        else:
            user_prompt = (
                "Summarize the following conversation segment. Focus on:\n"
                "- What the user's goal is\n"
                "- What key actions have been taken\n"
                "- What important decisions were made\n"
                "- Which files were modified or inspected\n"
                "- What should happen next\n\n"
                f"Conversation:\n{middle_text}\n\n"
                "Output a structured summary in this format:\n"
                f"{SUMMARY_PREFIX}\n\n"
                "## Goal\n...\n\n## Progress\n...\n\n## Key Decisions\n...\n\n"
                "## Files Modified\n...\n\n## Next Steps\n..."
            )

        # 估算 summary 请求的 token 数用于校准
        estimated_input = self._raw_estimate(
            [
                {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ]
        )
        content, actual_tokens = _summarize_with_retry(
            self._client,
            self._config.COMPRESSION_MODEL,
            SUMMARY_SYSTEM_PROMPT,
            user_prompt,
            self._config.SUMMARY_MAX_TOKENS,
        )

        # 自适应校准
        self.calibrate(estimated_input, actual_tokens)

        if not content.startswith(SUMMARY_PREFIX):
            content = f"{SUMMARY_PREFIX}\n\n{content}"
        return content

    def _format_messages_for_summary(self, messages: list[dict[str, Any]]) -> str:
        """将消息列表格式化为适合摘要的文本."""
        lines: list[str] = []
        max_chars = self._config.SUMMARY_PER_MSG_CHARS
        for msg in messages:
            role = msg.get("role", "unknown")
            content = str(msg.get("content", ""))
            tool_calls = msg.get("tool_calls")
            tool_call_id = msg.get("tool_call_id")

            if role == "tool":
                label = f"[tool {tool_call_id}]"
            elif tool_calls:
                names = [tc["function"]["name"] for tc in tool_calls]
                label = f"[{role} -> calls {', '.join(names)}]"
            else:
                label = f"[{role}]"

            truncated = content[:max_chars]
            if len(content) > max_chars:
                truncated += f"... [truncated, {max_chars} chars]"
            lines.append(f"{label} {truncated}")

        return "\n".join(lines)

    # ---- 组装 ----

    def assemble(
        self,
        messages: list[dict[str, Any]],
        head_end: int,
        tail_start: int,
        summary: str,
    ) -> list[dict[str, Any]]:
        """组装压缩后的消息列表：[头部] + [摘要消息] + [尾部]."""
        head = messages[:head_end]
        tail = messages[tail_start:]
        summary_msg: dict[str, Any] = {"role": "assistant", "content": summary}
        return [*head, summary_msg, *tail]

    # ---- 查找已有摘要 ----

    def _find_existing_summary(self, messages: list[dict[str, Any]]) -> str | None:
        """查找消息列表中是否已有压缩摘要."""
        for msg in messages:
            content = str(msg.get("content", ""))
            if content.startswith(SUMMARY_PREFIX):
                return content
        return None

    # ---- 总入口 ----

    def compress(self, messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
        """执行完整压缩管线.

        Returns:
            (new_messages, was_compressed): 压缩后的消息列表，以及是否执行了完整压缩.
        Raises:
            CompressionStuckError: 压缩无法有效缩减上下文时抛出.
        """
        before = self.estimate_tokens(messages)

        self.clear_old_tool_outputs(messages)

        if self.estimate_tokens(messages) <= self._config.COMPRESSION_THRESHOLD:
            return messages, False

        boundaries = self.find_boundaries(messages)
        if boundaries is None:
            return messages, False

        head_end, tail_start = boundaries
        middle = messages[head_end:tail_start]

        old_summary = self._find_existing_summary(messages)
        summary = self.summarize(middle, old_summary)

        new_messages = self.assemble(messages, head_end, tail_start, summary)

        after = self.estimate_tokens(new_messages)
        threshold = int(before * self._config.COMPRESSION_MIN_SHRINK)
        if after >= threshold:
            raise CompressionStuckError(before, after)

        return new_messages, True
