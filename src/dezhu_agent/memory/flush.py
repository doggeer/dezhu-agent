"""Flush 紧急冲刷：压缩前注入系统消息，让模型保存未写入的 memory，完成后清理注入消息."""

from __future__ import annotations

import json

from dezhu_agent.logging_config import get_logger
from dezhu_agent.memory.store import MemoryStore

logger = get_logger(__name__)

_FLUSH_MESSAGE = (
    "[System: The session is being compressed. "
    "Save anything worth remembering — prioritize user preferences, "
    "corrections, and recurring patterns over task-specific details.]"
)

# flush 注入标记（用于后续清理）
_FLUSH_MARKER = "__memory_flush__"
_MAX_FLUSH_ITERATIONS = 3


class FlushManager:
    """管理压缩前紧急冲刷。

    在压缩前注入系统消息提示模型保存 memory，
    完成后从消息历史中删除注入消息，不留痕迹。
    """

    def __init__(
        self,
        store: MemoryStore,
        min_turns: int = 6,
    ) -> None:
        self._store = store
        self._min_turns = min_turns
        self._turn_count = 0

    @property
    def turn_count(self) -> int:
        return self._turn_count

    def on_user_turn(self) -> None:
        """用户每发送一轮消息后调用."""
        self._turn_count += 1

    def create_pre_compress_hook(self):
        """返回可传给 CompressionConfig.pre_compress_hook 的回调。

        回调签名: (messages: list[dict]) -> list[dict]
        在压缩前执行 flush 逻辑，返回清理后的消息列表。
        """
        manager = self

        def hook(messages: list[dict]) -> list[dict]:
            return manager._do_flush(messages)

        return hook

    def _do_flush(self, messages: list[dict]) -> list[dict]:
        """执行 flush：注入提示 → 多轮 LLM 调用 → 清理注入消息。

        仅在用户对话轮数 ≥ min_turns 时执行。
        注入消息不进入压缩（在返回前从 messages 中删除）。

        方案 B：注入-标记-调用-清理模式
        - 注入 system 消息到 messages
        - 进行最多 _MAX_FLUSH_ITERATIONS 轮 LLM 调用
        - 模型调用 memory 工具后看到结果，可多步操作
        - 完成后从 messages 中删除注入消息和后续的 assistant/tool 消息
        - 返回清理后的 messages
        """
        if self._turn_count < self._min_turns:
            logger.debug(
                "Flush 跳过: 用户对话 %d 轮 < 阈值 %d",
                self._turn_count,
                self._min_turns,
            )
            return messages

        logger.info(
            "Flush 触发: 用户对话 %d 轮（阈值 %d），压缩前保存 memory",
            self._turn_count,
            self._min_turns,
        )
        # 重置计数器（只 flush 一次）
        self._turn_count = 0

        try:
            from dezhu_agent.llm import call_llm

            # 注入 flush 系统消息
            flush_msg = {
                "role": "system",
                "content": _FLUSH_MESSAGE,
            }
            flush_index = len(messages)  # 记录注入位置
            flush_messages = messages + [flush_msg]

            # 获取 memory 工具
            tools = self._get_memory_only_tools()

            for iteration in range(1, _MAX_FLUSH_ITERATIONS + 1):
                logger.debug("Flush — 第 %d/%d 轮", iteration, _MAX_FLUSH_ITERATIONS)

                try:
                    response = call_llm(flush_messages, tools=tools)
                except Exception as api_err:
                    logger.error("Flush API 调用失败 (第 %d 轮): %s", iteration, api_err)
                    break

                if response.tool_calls:
                    # 追加 assistant 消息
                    flush_messages.append(
                        {
                            "role": "assistant",
                            "content": response.content,
                            "tool_calls": response.tool_calls,
                        }
                    )

                    # 执行工具并追加 tool 结果
                    from dezhu_agent.tools import registry as tool_registry

                    for tc in response.tool_calls:
                        fn = tc.get("function", {})
                        name = fn.get("name", "")
                        args_str = fn.get("arguments", "{}")
                        try:
                            args = json.loads(args_str) if args_str else {}
                        except json.JSONDecodeError:
                            args = {}

                        content = tool_registry.execute(name, args)
                        logger.info(
                            "Flush — 工具调用: %s(%s) → %s",
                            name,
                            args,
                            content[:200],
                        )
                        flush_messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tc["id"],
                                "name": name,
                                "content": content,
                            }
                        )

                    continue  # 下一轮

                else:
                    logger.info(
                        "Flush 完成 (第 %d 轮): '%s'",
                        iteration,
                        (response.content or "")[:100],
                    )
                    break

            # 清理注入消息及其后续的 assistant/tool 消息
            # 只返回原始 messages（注入位置之前的内容）
            logger.info(
                "Flush 清理: 移除注入消息及 %d 条后续响应", len(flush_messages) - flush_index
            )
            return messages

        except Exception:
            logger.error("Flush 异常", exc_info=True)
            return messages

    @staticmethod
    def _get_memory_only_tools() -> list[dict] | None:
        """返回仅含 memory 工具的 tool 列表."""
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
