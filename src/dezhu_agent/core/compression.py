"""上下文压缩模块 —— 三层递进压缩：Layer 1 清旧工具输出 → Layer 2 找边界 → Layer 3 LLM 摘要."""

from __future__ import annotations

import json
from typing import Any

import tiktoken
from openai import OpenAI

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


class ContextCompressor:
    """上下文压缩器，按 Layer 1 -> Layer 2 -> Layer 3 递进压缩消息列表."""

    def __init__(self, config: Settings) -> None:
        self._config = config
        self._encoding = tiktoken.get_encoding("cl100k_base")
        self._summary_client = OpenAI(
            base_url=config.BASE_URL,
            api_key=config.API_KEY,
        )

    # ---- Token 估算 ----

    def estimate_tokens(self, messages: list[dict[str, Any]]) -> int:
        """估算消息列表的 token 数（cl100k_base encoding）."""
        tokens = 0
        for msg in messages:
            tokens += 3
            content = msg.get("content")
            if content:
                tokens += len(self._encoding.encode(str(content)))
            tool_calls = msg.get("tool_calls")
            if tool_calls:
                tokens += len(self._encoding.encode(json.dumps(tool_calls)))
            tool_call_id = msg.get("tool_call_id")
            if tool_call_id:
                tokens += len(self._encoding.encode(str(tool_call_id)))
        tokens += 3
        return tokens

    def _estimate_single(self, msg: dict[str, Any]) -> int:
        """估算单条消息的 token 数."""
        tokens = 3
        content = msg.get("content")
        if content:
            tokens += len(self._encoding.encode(str(content)))
        tool_calls = msg.get("tool_calls")
        if tool_calls:
            tokens += len(self._encoding.encode(json.dumps(tool_calls)))
        tool_call_id = msg.get("tool_call_id")
        if tool_call_id:
            tokens += len(self._encoding.encode(str(tool_call_id)))
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
        """返回 (head_end, tail_start) 或 None.

        head_end 不含，tail_start 含，中间段 [head_end:tail_start) 将被摘要替代.
        边界自动对齐：跳过 tool 消息，确保 assistant.tool_calls 与其 tool 响应始终同区.
        """
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
        """调用辅助模型生成结构化摘要."""
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

        response = self._summary_client.chat.completions.create(
            model=self._config.COMPRESSION_MODEL,
            messages=[
                {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=self._config.SUMMARY_MAX_TOKENS,
        )

        content = response.choices[0].message.content or ""
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
