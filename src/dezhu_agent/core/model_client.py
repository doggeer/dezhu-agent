"""ModelClient —— 以容错方式调用 LLM API.

职责:
- 单次 API 调用 (带可恢复错误退避重试)
- finish_reason=length 续写
- 故障转移 (主模型不可用时切备用模型)
- 主模型冷却期后自动恢复
- 连接健康检查 (lightweight ping)
"""

from __future__ import annotations

import random
import time
from typing import Any

import structlog
from openai import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    NotFoundError,
    OpenAI,
    PermissionDeniedError,
    RateLimitError,
)

from dezhu_agent.config import Settings
from dezhu_agent.models.error import ApiCallResult, ErrorCategory

logger = structlog.get_logger(__name__)

# ---- 续写提示 ----
CONTINUE_MESSAGE = (
    "Your response was cut off. Continue EXACTLY from where you stopped. "
    "Do not restart, do not repeat, do not summarize what came before."
)


class ModelClient:
    """以容错方式调用 LLM API.

    封装了退避重试、续写、故障转移、主模型恢复和健康检查。
    agent_loop / run_conversation 通过此 client 调用 API,
    无需关心内部的重试细节和当前使用的模型.
    """

    def __init__(self, config: Settings) -> None:
        self._config = config
        self._current_model: str = config.MODEL
        self._is_fallback: bool = False
        self._fallback_at: float | None = None
        self._backup_models: list[str] = self._parse_backup_models()
        self._client: OpenAI = self._create_client()

    # ---- 公开方法 ----

    def call(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None) -> ApiCallResult:
        """单次 API 调用, 对可恢复错误自动退避重试.

        不可恢复错误 (401/403/404) 立即返回失败, 不做无意义重试.
        finish_reason=length 时检测 thinking-budget 问题.
        """
        max_retries = self._config.MAX_RETRIES

        for attempt in range(max_retries + 1):
            try:
                response = self._client.chat.completions.create(
                    model=self._current_model,
                    messages=messages,  # type: ignore[arg-type]
                    tools=tools or None,  # type: ignore[arg-type]
                )
                choice = response.choices[0]
                finish_reason = choice.finish_reason or ""

                # ---- thinking-budget 检测 ----
                if finish_reason == "length" and response.usage:
                    completion_tokens = response.usage.completion_tokens or 0
                    if completion_tokens == 0 and self._has_reasoning_tokens(response.usage):
                        return ApiCallResult(
                            success=False,
                            category=ErrorCategory.THINKING_BUDGET,
                            error_message=(
                                "所有输出 token 被 reasoning 消耗, 回复 token 为零。"
                                "请增大 max_tokens 或减少 reasoning 预算。"
                            ),
                            finish_reason="length",
                        )

                return ApiCallResult(
                    success=True,
                    response=response,
                    finish_reason=finish_reason,
                    needs_continuation=(finish_reason == "length"),
                )

            except RateLimitError as exc:
                if attempt < max_retries:
                    self._backoff(attempt, "rate limit (429)")
                    continue
                return ApiCallResult(success=False, category=ErrorCategory.RETRYABLE, error_message=str(exc))

            except APITimeoutError as exc:
                if attempt < max_retries:
                    self._backoff(attempt, "timeout")
                    continue
                return ApiCallResult(success=False, category=ErrorCategory.RETRYABLE, error_message=str(exc))

            except APIConnectionError as exc:
                if attempt < max_retries:
                    self._backoff(attempt, "connection error")
                    continue
                return ApiCallResult(success=False, category=ErrorCategory.RETRYABLE, error_message=str(exc))

            except InternalServerError as exc:
                if attempt < max_retries:
                    self._backoff(attempt, "server error (5xx)")
                    continue
                return ApiCallResult(success=False, category=ErrorCategory.RETRYABLE, error_message=str(exc))

            except BadRequestError as exc:
                return ApiCallResult(success=False, category=ErrorCategory.CONTEXT_OVERFLOW, error_message=str(exc))

            except AuthenticationError as exc:
                return ApiCallResult(success=False, category=ErrorCategory.AUTH_FAILURE, error_message=str(exc))

            except NotFoundError as exc:
                return ApiCallResult(success=False, category=ErrorCategory.MODEL_NOT_FOUND, error_message=str(exc))

            except PermissionDeniedError as exc:
                return ApiCallResult(success=False, category=ErrorCategory.FATAL, error_message=str(exc))

            except APIError as exc:
                return ApiCallResult(success=False, category=ErrorCategory.FATAL, error_message=str(exc))

        # 理论上不可达
        return ApiCallResult(success=False, category=ErrorCategory.FATAL, error_message="unreachable")

    def call_with_continuation(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None
    ) -> ApiCallResult:
        """调用 API 并在输出截断时自动续写.

        续写内部会向 messages 追加 CONTINUE_MESSAGE (修改传入列表).
        返回的 ApiCallResult.response 中, choices[0].message.content
        已包含所有续写轮次积累的完整内容.
        """
        result = self.call(messages, tools)
        if not result.success or not result.needs_continuation:
            return result

        # thinking-budget 已在 call() 中检测
        if result.category == ErrorCategory.THINKING_BUDGET:
            return result

        assert result.response is not None
        choice = result.response.choices[0]
        has_tool_calls = bool(choice.message.tool_calls)

        if has_tool_calls:
            logger.warning("Truncated tool_calls detected, discarding and retrying")
            messages.append({"role": "user", "content": CONTINUE_MESSAGE})
            return self.call(messages, tools)

        # 纯文本被截断 — 多轮续写积累内容
        accumulated: str = choice.message.content or ""
        max_attempts = self._config.MAX_CONTINUATION_ATTEMPTS

        for _ in range(max_attempts - 1):
            messages.append({"role": "user", "content": CONTINUE_MESSAGE})
            result = self.call(messages, tools)
            if not result.success:
                return result
            assert result.response is not None
            new_content = result.response.choices[0].message.content or ""
            accumulated += new_content
            if result.finish_reason != "length":
                # 续写成功, 将积累内容回填到响应对象
                result.response.choices[0].message.content = accumulated
                return result

        # 续写次数耗尽
        if result.success:
            assert result.response is not None
            result.response.choices[0].message.content = accumulated
        return ApiCallResult(
            success=False,
            category=ErrorCategory.TRUNCATED,
            error_message=f"Response still truncated after {max_attempts} continuation attempts.",
            finish_reason="length",
        )

    def try_fallback(self) -> bool:
        """故障转移: 切换到下一个可用的备用模型.

        Returns:
            True 如果成功切换到备用模型, False 如果所有备用模型都不可用.
        """
        if not self._backup_models:
            logger.error("没有配置备用模型 (BACKUP_MODELS)")
            return False

        original_model = self._current_model

        for model in self._backup_models:
            if model == original_model:
                continue
            logger.info("故障转移: 尝试切换到备用模型 %s", model)
            self._current_model = model
            self._is_fallback = True
            self._fallback_at = time.time()
            self._client = self._create_client()

            if self.check_health():
                logger.info("故障转移成功: 已切换到 %s", model)
                return True

            logger.warning("备用模型 %s 也不可用", model)

        # 全部备用模型失败, 恢复原状
        self._current_model = original_model
        self._is_fallback = bool(self._backup_models)
        self._client = self._create_client()
        logger.error("故障转移失败: 所有备用模型均不可用")
        return False

    def try_recover_main(self) -> None:
        """冷却期过后尝试切回主模型.

        仅在当前处于 fallback 状态且冷却期已过时尝试.
        先对主模型做健康检查, 确认可用再切换, 避免来回 flapping.
        """
        if not self._is_fallback:
            return
        if self._fallback_at is None:
            return

        elapsed = time.time() - self._fallback_at
        if elapsed < self._config.MAIN_MODEL_COOLDOWN_SECONDS:
            return

        logger.info("冷却期已过 (%.0fs), 检查主模型 %s 是否恢复", elapsed, self._config.MODEL)

        test_client = OpenAI(base_url=self._config.BASE_URL, api_key=self._config.API_KEY)
        try:
            test_client.chat.completions.create(
                model=self._config.MODEL,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1,
            )
            self._current_model = self._config.MODEL
            self._is_fallback = False
            self._fallback_at = None
            self._client = self._create_client()
            logger.info("主模型已恢复: %s", self._config.MODEL)
        except Exception as exc:
            logger.warning("主模型 %s 仍不可用: %s", self._config.MODEL, exc)
            self._fallback_at = time.time()

    def check_health(self) -> bool:
        """轻量级健康检查: 用 1-token completion 验证 API 连通性."""
        try:
            self._client.chat.completions.create(
                model=self._current_model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1,
            )
            return True
        except Exception:
            return False

    # ---- 内部方法 ----

    def _backoff(self, attempt: int, reason: str) -> None:
        """指数退避 + 随机抖动."""
        delay = self._config.RETRY_BASE_DELAY * (2**attempt)
        delay = min(delay, self._config.RETRY_MAX_DELAY)
        jitter = delay * self._config.RETRY_JITTER * (2 * random.random() - 1)
        delay += jitter
        delay = max(0, delay)
        logger.warning("API %s (第 %d 次重试), %.1fs 后重试", reason, attempt + 1, delay)
        time.sleep(delay)

    def _parse_backup_models(self) -> list[str]:
        """解析 BACKUP_MODELS 配置 (逗号分隔字符串)."""
        raw = self._config.BACKUP_MODELS.strip()
        if not raw:
            return []
        return [m.strip() for m in raw.split(",") if m.strip()]

    def _create_client(self) -> OpenAI:
        """创建当前模型对应的 OpenAI client."""
        return OpenAI(base_url=self._config.BASE_URL, api_key=self._config.API_KEY)

    @staticmethod
    def _has_reasoning_tokens(usage: Any) -> bool:
        """检测 usage 中是否有 reasoning_tokens > 0."""
        details = getattr(usage, "completion_tokens_details", None)
        if details is None:
            return False
        reasoning = getattr(details, "reasoning_tokens", 0) or 0
        return reasoning > 0
