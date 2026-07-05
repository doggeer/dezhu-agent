"""Nudge 定期后台审查：每 N 轮用户对话后，后台线程审查对话历史并保存记忆."""

from __future__ import annotations

import json
import threading

from dezhu_agent.logging_config import get_logger
from dezhu_agent.memory.store import MemoryStore

logger = get_logger(__name__)

_REVIEW_PROMPT = """Review the conversation above and consider saving to memory if appropriate.

Focus on:
1. Has the user revealed things about themselves — their persona, desires, preferences, or personal details worth remembering?
2. Has the user expressed expectations about how you should behave, their work style, or ways they want you to operate?

If something stands out, use the `memory` tool to save it:
- Use `target="user"` for user preferences, personal details, work style
- Use `target="memory"` for project-level facts, environment details, conventions
- Use `action="write"` to add a new entry
- Use `action="read"` first to check current memory contents before writing
- If memory is full, delete less important entries with `action="delete"` before writing

If nothing is worth saving, just say 'Nothing to save.' and stop."""

# 审查 agent 配置
_MAX_REVIEW_ITERATIONS = 8
_REVIEW_TIMEOUT_PER_CALL = 5  # 秒


class NudgeManager:
    """管理定期后台审查。

    每 interval 轮用户对话触发一次后台审查。
    审查在独立线程中运行，使用独立 LLM 客户端 + 仅 memory 工具。
    """

    def __init__(
        self,
        store: MemoryStore,
        interval: int = 10,
    ) -> None:
        self._store = store
        self._interval = interval
        self._turn_count = 0
        self._lock = threading.Lock()

    @property
    def turn_count(self) -> int:
        return self._turn_count

    def reset_counter(self) -> None:
        """重置轮数计数器（nudge 触发后自动调用）."""
        with self._lock:
            self._turn_count = 0

    def on_user_turn(self, messages_snapshot: list) -> None:
        """用户每发送一轮消息后调用。

        计数器 +1，达到阈值时在后台线程中启动审查。
        """
        with self._lock:
            self._turn_count += 1
            current_count = self._turn_count

        if current_count >= self._interval:
            logger.info(
                "Nudge 触发: 用户对话 %d 轮（阈值 %d），启动后台审查",
                current_count,
                self._interval,
            )
            self.reset_counter()

            # 深拷贝消息快照（避免并发修改）
            msgs_copy = list(messages_snapshot) if messages_snapshot else []

            thread = threading.Thread(
                target=self._run_review,
                args=(msgs_copy,),
                daemon=True,
                name="memory-nudge",
            )
            thread.start()

    def _run_review(self, messages_snapshot: list) -> None:
        """后台审查——在独立线程中运行，最多 _MAX_REVIEW_ITERATIONS 轮。

        调用 LLM 审查对话历史，模型自行决定是否保存记忆。
        每轮 API 调用设 _REVIEW_TIMEOUT_PER_CALL 秒超时。
        输出通过日志记录，不发送到用户终端。
        """
        from dezhu_agent.llm import call_llm

        logger.info("Nudge 审查开始: 审查 %d 条消息", len(messages_snapshot))

        try:
            api_messages = self._build_review_messages(messages_snapshot)
            tools = self._get_memory_only_tools()

            for iteration in range(1, _MAX_REVIEW_ITERATIONS + 1):
                logger.debug("Nudge 审查 — 第 %d/%d 轮", iteration, _MAX_REVIEW_ITERATIONS)

                try:
                    response = call_llm(api_messages, tools=tools)
                except Exception as api_err:
                    logger.error(
                        "Nudge 审查 API 调用失败 (第 %d 轮): %s",
                        iteration,
                        api_err,
                    )
                    return

                if response.tool_calls:
                    # 追加 assistant 消息
                    api_messages.append(
                        {
                            "role": "assistant",
                            "content": response.content,
                            "tool_calls": response.tool_calls,
                        }
                    )

                    # 执行工具并追加 tool 结果
                    for tc in response.tool_calls:
                        fn = tc.get("function", {})
                        name = fn.get("name", "")
                        args_str = fn.get("arguments", "{}")
                        try:
                            args = json.loads(args_str) if args_str else {}
                        except json.JSONDecodeError:
                            args = {}

                        from dezhu_agent.tools import registry as tool_registry

                        content = tool_registry.execute(name, args)
                        logger.info(
                            "Nudge 审查 — 工具调用: %s(%s) → %s",
                            name,
                            args,
                            content[:200],
                        )
                        api_messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tc["id"],
                                "name": name,
                                "content": content,
                            }
                        )

                    continue  # 下一轮，让模型看到工具结果

                elif response.content and "Nothing to save" in response.content:
                    logger.info("Nudge 审查完成 (第 %d 轮): 无需保存", iteration)
                    return

                else:
                    logger.info(
                        "Nudge 审查完成 (第 %d 轮): 模型回复 '%s'",
                        iteration,
                        (response.content or "")[:100],
                    )
                    return

            # 达到最大轮数
            logger.info(
                "Nudge 审查完成: 达到最大轮数 %d 轮，停止审查",
                _MAX_REVIEW_ITERATIONS,
            )

        except Exception:
            logger.error("Nudge 审查异常", exc_info=True)

    def _build_review_messages(self, messages_snapshot: list) -> list[dict]:
        """构建审查 agent 的 API 消息列表。

        过滤掉 system 消息（避免主 agent 的人设/MEMORY 快照污染审查判断），
        只保留 user/assistant/tool 消息。
        """
        history_dicts: list[dict] = []

        for m in messages_snapshot:
            # 支持 Message dataclass 和普通 dict
            role = getattr(m, "role", None) or (m.get("role", "") if isinstance(m, dict) else "")

            if role == "system":
                continue  # 过滤 system 消息

            if hasattr(m, "to_api_dict"):
                d = m.to_api_dict()
            elif isinstance(m, dict):
                d = dict(m)
                d.pop("_internal", None)
            else:
                continue

            history_dicts.append(d)

        return [
            {"role": "system", "content": _REVIEW_PROMPT},
            *history_dicts,
        ]

    @staticmethod
    def _get_memory_only_tools() -> list[dict] | None:
        """返回仅含 memory 工具的 tool 列表（审查 agent 不应有 bash 等工具）."""
        from dezhu_agent.tools import registry as tool_registry

        memory_def = tool_registry._tools.get("memory")
        if memory_def is None:
            return None
        return [
            {
                "type": "function",
                "function": {
                    "name": memory_def.name,
                    "description": memory_def.description,
                    "parameters": memory_def.parameters,
                },
            }
        ]
