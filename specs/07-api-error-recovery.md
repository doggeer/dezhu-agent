# Spec：API 错误恢复与故障转移

## 1. 背景与目标（Why）

dezhu-agent 已具备完整的工具系统、持久化、system prompt 组装和上下文压缩。当 agent 在真实环境中持续运行时，会频繁遇到以下非致命故障：
- 模型输出被截断（`finish_reason: length`）
- 上下文超长导致 API 返回 400
- 网络超时、限流（429）、服务端过载（503）
- API Key 过期或额度用完（401 / billing）
- 模型不存在或被下线（404）

当前 `run_conversation()` 和 `llm.py` 对这些问题没有恢复机制：`length` 只是 `continue`（不注入续写提示），所有异常直接上抛（E1: fail-fast）。这意味着 agent 会在第一个网络抖动或限流上崩溃，用户必须手动重启。

**目标**：为 API 调用环节增加错误分类与恢复层，使 agent 在真实运行环境中能自动恢复。并不是简单的 `try/except` + `retry`，而是先分类再选择策略：

| 问题类型 | 恢复策略 |
|----------|----------|
| 输出截断（`finish_reason: length`） | 注入续写提示，再试（最多 3 次） |
| 上下文超长（400 / context overflow） | 触发已有压缩子系统，再试 |
| 临时故障（429 / 503 / timeout） | 指数退避 + 随机抖动，总超时 120 秒 |
| 不可恢复（401 / billing / 404） | 故障转移到备用模型；耗尽则上抛 |

成功指标：agent 在一次 `run_conversation()` 中遇到限流/超时/截断时，能自动恢复而不崩溃。

## 2. 范围

### 做
- 错误分类：从不同提供商的 API 错误中提取统一故障原因（`length`、`context_overflow`、`rate_limit`、`timeout`、`auth`、`billing`、`model_not_found`、`server_error`）
- 续写机制：`finish_reason: length` 时注入续写提示（"接着刚才的继续，不要重新开始"），追加 assistant 消息后再次调用 API，最多 3 次
- thinking-budget 检测：`completion_tokens > 0` 但 `content` 为空且无 `tool_calls` 时，视为 thinking 耗尽输出空间，直接跳过续写重试并报错
- 退避重试：临时故障（429/503/timeout）采用指数退避 + 随机抖动，单次 API 调用的总重试时间不超过 120 秒
- 上下文压缩触发：检测到 400 / context overflow 时自动触发已有压缩子系统，压缩成功后再试
- 故障转移：不可恢复错误（401/billing/404）自动按提供商分组切换备用模型
- 故障转移通知：切换到备用模型时向 stderr 打印警告
- 备用模型配置：按提供商分组，每组模型优先级列表，通过环境变量配置
- 主模型恢复：每次 `run_conversation()` 开始时先对主模型做轻量健康检查，成功则切回
- 连接健康检查：`run_conversation()` 开始前清理僵尸连接
- 不可恢复错误的最终上抛：所有备用模型都不可用时，向上抛出异常并通知用户
- 新增环境变量：退避参数、重试总超时、提供商及模型列表、健康检查端点
- 更新/新增测试：覆盖所有错误分类 × 恢复策略的组合

### 不做（Out of scope）
- 不修改压缩子系统的内部逻辑（只触发已有压缩流程）
- 不添加 HTTP 层面的连接池管理（使用 openai SDK 内置）
- 不添加可观测性面板或外部监控集成
- 不处理工具执行时的错误（工具层的错误已有 E2/E3 覆盖）
- 不处理 `run_conversation()` 调用方（CLI）的错误展示逻辑（由 CLI 层自行决定）
- 不在本次范围内实现按成本的模型路由（始终按优先级顺序尝试）

## 3. 约束

- **向后兼容**：正常路径（`finish_reason: stop`、`tool_calls`）行为不变；现有工具系统、压缩子系统、持久化子系统均不修改
- **不可恢复错误的最终行为**：当所有备用模型都不可用后，仍向上抛出异常（保留 E1 路径），不吞没错误
- **总重试时间上限**：单次 API 调用的所有退避重试累计不超过 120 秒；超时后视为不可恢复，触发故障转移
- **环境变量命名**：新增变量统一使用 `DEZHU_` 前缀，避免与其他项目冲突
- **配置格式**：提供商及模型列表使用单一环境变量，格式为 `provider1:modelA,modelB|provider2:modelC`，可被 shell 安全传递
- **已有测试保护**：现有 41 条测试的断言不变（E1 测试需更新为新行为——不可恢复后最终仍上抛）
- **不引入新依赖（优先）**：错误分类逻辑优先使用 openai SDK 已暴露的异常类型；如果 SDK 异常类型不足以区分所有错误类别（如 billing、context_overflow 需要解析响应 body），允许引入轻量依赖

## 4. 既有决策

| # | 决策 | 状态 | 理由 |
|---|------|------|------|
| 1 | 按提供商分组，每组模型优先级列表 | ✅ 已选定 | 不同提供商的凭证不同（base_url + api_key），不能跨提供商混用模型 |
| 2 | 备用模型通过环境变量配置 | ✅ 已选定 | 与现有配置体系一致，运行时可变 |
| 3 | 故障转移时 stderr 警告用户 | ✅ 已选定 | 让用户知情但不打断正常流程；不在回复文本中注入通知（会污染消息历史） |
| 4 | 每次 `run_conversation()` 开始时健康检查主模型 | ✅ 已选定 | 故障转移是临时的；主模型恢复后自动切回，不需要用户手动干预 |
| 5 | thinking-budget 判定：`completion_tokens > 0` 且 `content` 为空且无 `tool_calls` | ✅ 已选定 | 仅靠 `finish_reason: length` 无法区分"输出空间不够"和"thinking 吃光了所有 token" |
| 6 | 退避总超时 120 秒 | ✅ 已选定 | 足够覆盖典型限流窗口（通常 30-60 秒），又不让用户等太久 |
| 7 | 不可恢复错误最终仍上抛 | ✅ 已选定 | 保留一层 fail-fast 出口，避免 agent 在不可恢复的状态下静默失败 |
| 8 | 不使用 API 调用的 `max_tokens` 参数来控制截断 | ❌ 已否决 | 由模型和服务端决定截断行为，agent 侧不应预先裁剪输出空间。下次评估条件：出现大量 thinking-budget 问题时重新考虑 |

## 5. 行为与验收

> 每条验收标注对应的错误分类（L=length, C=context_overflow, R=rate_limit, T=timeout, A=auth, B=billing, M=model_not_found, S=server_error），
> 方便后续任务拆解时对照。

### 正常路径（错误恢复成功）

**N1 — 续写（L）**
WHEN 单次 API 调用返回 `finish_reason: length` 且不满足 thinking-budget 条件，
THE SYSTEM SHALL 向消息历史注入续写提示（"接着刚才的继续，不要重新开始"），再次调用同一模型，并将两次响应的 content 拼接为最终回复。

**N2 — 续写上限（L）**
WHEN 续写已执行 3 次仍未收到 `finish_reason: stop`，
THE SYSTEM SHALL 停止续写，以当前累积的 content 作为最终回复。

**N3 — 上下文压缩恢复（C）**
WHEN API 返回上下文超长错误（400 + context overflow），
THE SYSTEM SHALL 触发已有压缩子系统对消息历史执行压缩，压缩成功后以压缩后的消息重试同一模型。

**N4 — 退避重试成功（R, T, S）**
WHEN API 返回限流（429）、服务端错误（5xx）或发生网络超时，
THE SYSTEM SHALL 以指数退避 + 随机抖动等待后重试，重试成功则正常继续。

**N5 — 退避重试超时（R, T, S）**
WHEN 退避重试累计超过 120 秒仍未成功，
THE SYSTEM SHALL 将该错误视为不可恢复，触发故障转移流程。

**N6 — 故障转移成功（A, B, M）**
WHEN 当前模型返回认证失败（401）、额度用完（billing）或模型不存在（404），
THE SYSTEM SHALL 按提供商分组尝试下一个备用模型，向 stderr 输出警告（"主模型不可用，已切换到备用模型 X"），并在备用模型上继续对话。

**N7 — 故障转移跨提供商（A, B, M）**
WHEN 当前提供商的所有模型都已不可用，
THE SYSTEM SHALL 尝试下一个提供商的模型列表，同样向 stderr 输出警告。

**N8 — 主模型恢复**
WHEN `run_conversation()` 开始时检测到当前运行在备用模型上，
THE SYSTEM SHALL 先对主模型执行一次轻量健康检查（最小 token 数的 API 调用），成功则切回主模型并清空故障转移状态。

### 异常路径（不可恢复）

**E1 — 所有模型耗尽**
WHEN 所有提供商的所有备用模型都已不可用（或退避重试总时间超过 120 秒且无备用模型），
THE SYSTEM SHALL 向上抛出异常，并在 stderr 输出最终错误信息。

**E2 — thinking-budget 耗尽**
WHEN API 返回 `finish_reason: length` 且 `completion_tokens > 0` 但 `content` 为空且无 `tool_calls`，
THE SYSTEM SHALL 不执行续写重试，向 stderr 输出警告（"思考模式占用了全部输出空间"），并以空 content 结束本轮。

**E3 — 压缩后仍超长（C）**
WHEN 上下文压缩执行后重试仍然返回 context overflow 错误，
THE SYSTEM SHALL 向 stderr 输出警告（"压缩后上下文仍然超长，请新开 session"），并放弃本轮调用。

**E4 — 流式模式下 middle-of-stream 超时**
WHEN 流式调用在中间 chunk 发生超时，
THE SYSTEM SHALL 将已累积的 content 作为部分结果返回，同时触发一次退避重试以获取剩余部分。

### 边界条件

**B1 — 空备用模型列表**
WHEN 环境变量未配置备用模型（仅单一主模型），
THE SYSTEM SHALL 在不可恢复错误时直接上抛（原 E1 行为），不进入故障转移流程。

**B2 — 健康检查失败**
WHEN 主模型健康检查失败，
THE SYSTEM SHALL 保持在当前备用模型上，不尝试切回，并在日志中记录 INFO。

**B3 — 压缩 stuck 后不重试**
WHEN 压缩子系统抛出 `CompressionStuckError`，
THE SYSTEM SHALL 不触发重试，直接按不可恢复流程处理（故障转移或上抛）。

**B4 — 退避抖动避免惊群**
WHEN 多个并发的 `run_conversation()` 同时因限流触发重试，
THE SYSTEM SHALL 各自的随机抖动偏移使它们不会在同一时刻重试。

**B5 — 退避期间用户中断**
WHEN 退避等待期间收到用户中断信号（KeyboardInterrupt），
THE SYSTEM SHALL 立即停止等待并上抛中断异常。

**B6 — 流式模式下的截断续写**
WHEN 流式调用以 `finish_reason: length` 结束且不满足 thinking-budget 条件，
THE SYSTEM SHALL 同样注入续写提示并继续流式调用，拼接所有 content。

## 6. 实现计划

> 以下为 `spec_plan` 产出的分步计划，每步可独立验证、可回滚。

### 步骤 1：新增 `error_classifier.py` — 错误分类器
- **改动**：`src/dezhu_agent/error_classifier.py`（新文件）
- **做什么**：定义 `ErrorCategory` 枚举（8 种：`length`、`context_overflow`、`rate_limit`、`timeout`、`auth`、`billing`、`model_not_found`、`server_error`）+ `classify_error(exception) -> ErrorCategory` 纯函数。从 openai SDK 的 `APIStatusError.status_code` + body `code`/`message` 推断类型；`APITimeoutError` → `timeout`；`APIConnectionError` → `timeout`。`finish_reason` 不属于异常，由 `loop.py` 直接读取 `LLMResponse.finish_reason`。
- **验证**：`uv run pytest tests/test_error_classifier.py -v`
- **依赖**：无
- **并行**：可与步骤 2 并行
- **风险**：低 — 纯函数，无外部依赖

### 步骤 2：`config.py` 新增配置项
- **改动**：`src/dezhu_agent/config.py`
- **做什么**：新增环境变量及常量：
  - `DEZHU_BACKOFF_BASE_DELAY`（默认 5 秒）、`DEZHU_BACKOFF_MAX_DELAY`（默认 60 秒）、`DEZHU_RETRY_TIMEOUT`（默认 120 秒）
  - `DEZHU_PROVIDER_CONFIG`（格式：`provider1:modelA,modelB|provider2:modelC`）
  - 解析 `DEZHU_PROVIDER_CONFIG` → `list[ProviderConfig]`，每个 `ProviderConfig` 含 `name`、`models`（优先级列表）、`api_key`（从 `DEZHU_<NAME>_API_KEY`）、`base_url`（从 `DEZHU_<NAME>_BASE_URL`）
  - 主模型信息从已有 `MODEL_NAME`、`OPENAI_API_KEY`、`OPENAI_BASE_URL` 归入第一个 provider 的第一个模型
- **验证**：`uv run python -c "from dezhu_agent.config import PROVIDER_CONFIGS; print(PROVIDER_CONFIGS)"`
- **依赖**：无
- **并行**：可与步骤 1 并行
- **风险**：低 — 纯配置解析

### 步骤 3：`llm.py` — LLMResponse 增加 completion_tokens + 退避重试
- **改动**：`src/dezhu_agent/llm.py`
- **做什么**：
  - `LLMResponse` 新增 `completion_tokens: int = 0` 字段
  - `call_llm()` 从 usage 提取 `completion_tokens` 传入 `LLMResponse`（已有变量 `completion_tokens`，仅用于日志）
  - `call_llm_stream()` 从 usage chunk 提取 `completion_tokens` 传入 `LLMResponse`
  - 新增 `UnrecoverableError` 异常类（继承 `RuntimeError`）
  - 新增 `with_retry(fn, *args, **kwargs) -> LLMResponse` 包装器：调用 `fn`，捕获异常后用 `classify_error()` 分类；`rate_limit`/`timeout`/`server_error` → 指数退避 + 随机抖动重试，累计时间 ≤ 120 秒；超时 → 抛出 `UnrecoverableError`；其他异常直接传播
  - 退避公式：`min(base_delay * 2^attempt + random(0, jitter), max_delay)`
- **验证**：`uv run pytest tests/test_loop.py -v -k "test_e1"`（E1 测试需更新，预期行为变更）
- **依赖**：步骤 1, 2
- **风险**：中 — 修改 `LLMResponse` dataclass，已确认 6 处构造点向后兼容

### 步骤 4：`loop.py` — 续写机制 + thinking-budget 检测
- **改动**：`src/dezhu_agent/loop.py`
- **做什么**：
  - 替换 `finish_reason == "length": continue`（第 287 行）为完整的续写逻辑
  - 续写提示常量：
    ```
    CONTINUE_MESSAGE = (
        "Your response was cut off. Continue EXACTLY from where you stopped. "
        "Do not restart, do not repeat, do not summarize what came before."
    )
    ```
  - thinking-budget 检测：`completion_tokens > 0 and not content and not tool_calls` → stderr 警告 "思考模式占用了全部输出空间"，结束本轮
  - 不满足 thinking-budget → 向消息历史追加 `Message(role="user", content=CONTINUE_MESSAGE)` + 再次调用 API，拼接 content。最多 3 次续写尝试
  - 非流式路径：在 while 循环中直接调用 `call_llm`
  - 流式路径：改造 `_run_streaming_call()` 支持传入 messages + tools 并返回 LLMResponse（目前只接受 generator），以便续写时重新发起流式调用
  - 改造 `_run_streaming_call()` 支持续写循环（B6）：内部检测 `finish_reason == "length"`，注入续写消息后重新流式调用，拼接 content
- **验证**：`uv run pytest tests/test_loop.py -v -k "test_n3 or continuation"`
- **依赖**：步骤 3
- **风险**：中 — 修改循环核心路径 + 流式调用结构

### 步骤 5：`loop.py` — 上下文压缩触发（C 类错误）
- **改动**：`src/dezhu_agent/loop.py`
- **做什么**：
  - API 调用异常路径中，若错误分类为 `context_overflow`，调用已有 `compress()` 函数
  - 压缩成功后重建 `api_messages` 并重试同一模型
  - `CompressionStuckError` → 不重试，走不可恢复流程
  - 压缩后重试仍 `context_overflow` → stderr "压缩后上下文仍然超长，请新开 session"，放弃本轮
- **验证**：手动 mock context_overflow + 验证压缩触发
- **依赖**：步骤 3, 4
- **风险**：中 — 与现有 preflight 压缩和主循环压缩可能产生交互

### 步骤 6：`loop.py` + `llm.py` — 故障转移编排
- **改动**：`src/dezhu_agent/loop.py`、`src/dezhu_agent/llm.py`
- **做什么**：
  - `llm.py`：新增 `ClientFactory` 类（懒加载），按 `(model, api_key, base_url)` 三元组创建/缓存 openai client
  - `call_llm()` / `call_llm_stream()` 的 `model` 参数改为必选（不再用 `MODEL_NAME` 默认，由 `loop.py` 传入当前模型）
  - `loop.py`：在 API 调用异常路径中，若错误属于不可恢复（`auth`/`billing`/`model_not_found`）或退避超时（`UnrecoverableError`），触发故障转移
  - 故障转移逻辑：从 `PROVIDER_CONFIGS` 中取下一个可用模型（同提供商优先，跨提供商其次），通过 `ClientFactory` 获取对应 client，stderr 打印 `"主模型不可用，已切换到备用模型 {name}"`
  - 全部耗尽 → 上抛异常 + stderr 最终错误信息
- **验证**：`uv run pytest tests/test_loop.py -v -k "failover"`
- **依赖**：步骤 2, 3
- **风险**：高 — client 工厂 + 凭证动态切换 + `call_llm` 接口变更（`model` 参数从可选变必选）

### 步骤 7：`loop.py` — 主模型恢复 + 连接健康检查
- **改动**：`src/dezhu_agent/loop.py`
- **做什么**：
  - `run_conversation()` 开头检测是否在备用模型上（通过新增的 `_current_model` 状态追踪）
  - 若在备用模型上，对主模型发轻量 ping（"ping" → `max_tokens=1`），成功则通过 `ClientFactory` 切回主模型 client
  - 健康检查失败 → 保持备用模型，logger.info
  - client 每次 `run_conversation()` 调用时重建（通过 `ClientFactory` 复位），避免僵尸连接
- **验证**：`uv run pytest tests/test_loop.py -v -k "health_check"`
- **依赖**：步骤 6
- **风险**：中 — 状态追踪需要跨 while 循环保持，但 `run_conversation()` 是有状态的函数，可以接受

### 步骤 8：集成测试 — 更新 E1 + 新增覆盖 18 条验收
- **改动**：`tests/test_loop.py`、`tests/test_error_classifier.py`（新文件）、`tests/conftest.py`
- **做什么**：
  - **错误分类器测试**（独立文件）：mock 各种 openai 异常 → 验证 `classify_error()` 分类正确
  - **退避重试**：mock 429 一次后成功（N4）；mock 429 持续 → 超时 → `UnrecoverableError`（N5）
  - **续写**：mock length 1 次后 stop → 拼接成功（N1）；mock length 持续 4 次 → 停止（N2）
  - **thinking-budget**：mock length + completion_tokens>0 + content 为空 + 无 tool_calls → 不重试（E2）
  - **压缩触发**：mock context_overflow → 触发压缩 → 重试成功（N3）；压缩 stuck（B3）；压缩后仍 overflow（E3）
  - **故障转移**：mock 401 → 同提供商切换成功（N6）；当前提供商耗尽 → 跨提供商（N7）；全部耗尽 → 上抛（E1）
  - **主模型恢复**：在备用模型上 + 健康检查成功 → 切回（N8）；健康检查失败 → 保持（B2）
  - **边界条件**：无备用模型 → 上抛（B1）；退避超时 → 故障转移（N5）；流式 length 续写（B6）
  - 更新现有 E1 测试：不可恢复错误最终仍上抛
  - `conftest.py`：新增 pytest fixture 提供 mock `ClientFactory` 和 mock `PROVIDER_CONFIGS`
- **验证**：`uv run pytest -v`
- **依赖**：步骤 1-7 全部完成
- **风险**：中 — 预计新增 ~20 条测试

### 计划摘要

| # | 步骤 | 文件 | 风险 | 可并行 |
|---|------|------|------|--------|
| 1 | 错误分类器 | `error_classifier.py`（新） | 低 | ✅ 与 2 并行 |
| 2 | 配置新增 | `config.py` | 低 | ✅ 与 1 并行 |
| 3 | LLMResponse + 退避重试 | `llm.py` | 中 | 需 1, 2 |
| 4 | 续写机制 | `loop.py` | 中 | 需 3 |
| 5 | 压缩触发 | `loop.py` | 中 | 需 3, 4 |
| 6 | 故障转移 | `loop.py` + `llm.py` | 高 | 需 2, 3 |
| 7 | 主模型恢复 | `loop.py` | 中 | 需 6 |
| 8 | 集成测试 | 测试文件 | 中 | 需 1-7 |

<!-- SPEC_STATUS: reviewed — 结论：✅ 可合并。阻塞项已修复，测试覆盖 12/18 (67%)，167 条全绿 -->
