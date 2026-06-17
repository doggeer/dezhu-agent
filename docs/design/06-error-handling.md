# 06 错误处理

## 解决的问题

Agent 执行过程中会遇到多种异常，如果没有错误恢复机制，主循环会在第一个错误上直接崩溃：

- 模型输出写到一半被截断（finish_reason: length）
- 上下文太长，API 直接返回 400
- 网络超时、限流、服务抖动
- API key 过期或额度用完
- 模型不存在或被下线

错误处理的目标是让 Agent 在可恢复的错误后自动恢复，继续执行；不可恢复时给出明确的错误信息，而非静默崩溃。

## 核心设计

### 处理流程

```
API call
  |
  +-- 成功，finish_reason: stop
  |      → 正常结束
  |
  +-- 成功，finish_reason: tool_calls
  |      → 执行工具，继续循环
  |
  +-- 成功，finish_reason: length
  |      → 续写（最多 3 次）
  |
  +-- 失败，可恢复
  |      → 分类 → 退避 / 压缩 / 换凭证
  |
  +-- 失败，不可恢复
         → 故障转移 / 放弃
```

### 错误分类

每一类错误对应不同的恢复策略：

| 分类 | 触发条件 | 策略 |
|------|----------|------|
| `RETRYABLE` | 429 限流 / 503 过载 / 超时 / 连接错误 | 指数退避 + 随机抖动重试 |
| `CONTEXT_OVERFLOW` | 400 Bad Request（疑似上下文溢出） | 触发压缩后重试一次；仍失败则升级为 FATAL |
| `TRUNCATED` | finish_reason=length，续写次数耗尽 | 返回错误 |
| `AUTH_FAILURE` | 401 认证失败 | 故障转移到备用模型；无备用则放弃 |
| `MODEL_NOT_FOUND` | 404 模型不存在 | 故障转移到备用模型；无备用则放弃 |
| `THINKING_BUDGET` | reasoning 消耗全部输出 token，回复 token 为零 | 直接报错，不做续写 |
| `FATAL` | 403 / 其他未知错误 | 放弃，返回错误给用户 |

### 与 OpenAI 异常的映射

利用 OpenAI SDK 的语义化异常类型做双重判断（异常类型 + HTTP 状态码）：

| 异常类 | 分类 |
|--------|------|
| `RateLimitError` (429) | RETRYABLE |
| `APITimeoutError` | RETRYABLE |
| `APIConnectionError` | RETRYABLE |
| `InternalServerError` (500+) | RETRYABLE |
| `BadRequestError` (400) | CONTEXT_OVERFLOW |
| `AuthenticationError` (401) | AUTH_FAILURE |
| `NotFoundError` (404) | MODEL_NOT_FOUND |
| `PermissionDeniedError` (403) | FATAL |
| `APIError`（其他） | FATAL |

## 关键策略

### 退避重试

出错后不立刻重试，而是等待一段时间。等待策略：**指数递增 + 随机抖动**。

- 首次等待 5 秒，第二次 10 秒，第三次 20 秒……（base=5s, exponent=2^n）
- 上限 60 秒
- 每次加入 ±10% 随机抖动，避免多个 session 同时重试撞在一起

### 故障转移

当前的模型或提供商出了不可恢复的问题（401/404）时，自动切换到备用模型。

备用模型通过 `BACKUP_MODELS` 配置项指定（逗号分隔）。切换后进入 fallback 状态，记录切换时间戳。

### 主模型恢复

故障转移到备用模型后，下一轮 `run_conversation()` 开始时，先尝试切回主模型：

1. 检查是否处于 fallback 状态
2. 检查冷却期（默认 300 秒）是否已过
3. 用临时 client 对主模型做 health check
4. 健康则切换回主模型；否则保持 fallback，重置冷却时间

冷却期防止主模型间歇性故障造成的来回 flapping。

### 续写

`finish_reason: length` 表示模型这轮输出空间不够、内容被截断。续写就是追加一条消息告诉模型「接着刚才的继续，不要重新开始」，然后再调一次 API。

```python
CONTINUE_MESSAGE = (
    "Your response was cut off. Continue EXACTLY from where you stopped. "
    "Do not restart, do not repeat, do not summarize what came before."
)
```

两种截断场景的处理：

| 场景 | 处理 |
|------|------|
| 纯文本被截断 | 保留已输出内容，注入 CONTINUE_MESSAGE，多轮续写（最多 3 次），累积全部内容 |
| tool_calls 被截断 | 丢弃残缺的 tool_calls 消息，注入 CONTINUE_MESSAGE，让模型重新生成 |

续写内容会累积合并到最终的 response 中，调用方无需关心内部续写细节。

### thinking-budget 检测

有些支持 reasoning 的模型可能把所有输出 token 都花在思考上，留给回复的 token 为零。此时 `finish_reason=length` 但续写没有意义。

检测逻辑：`completion_tokens == 0` 且 `reasoning_tokens > 0` → 直接报 THINKING_BUDGET 错误，不浪费续写重试。

### 连接健康检查

`agent_loop()` 启动时做一次轻量健康检查：发送 1-token 的 completion 请求验证 API 连通性。失败时打印警告但不阻断启动。

## 架构

### 类设计

#### `ErrorCategory` (`models/error.py`)

```python
class ErrorCategory(StrEnum):
    RETRYABLE = "retryable"
    CONTEXT_OVERFLOW = "context_overflow"
    TRUNCATED = "truncated"
    AUTH_FAILURE = "auth_failure"
    MODEL_NOT_FOUND = "model_not_found"
    THINKING_BUDGET = "thinking_budget"
    FATAL = "fatal"
```

#### `ApiCallResult` (`models/error.py`)

封装一次 API 调用的结果，供 agent 循环判断下一步动作：

| 字段 | 类型 | 说明 |
|------|------|------|
| `success` | `bool` | 调用是否成功（含续写成功） |
| `response` | `Any \| None` | OpenAI chat completion 对象 |
| `category` | `ErrorCategory \| None` | 失败时的错误分类 |
| `error_message` | `str` | 失败时的人类可读描述 |
| `finish_reason` | `str` | API 返回的 finish_reason |
| `needs_continuation` | `bool` | 是否需要续写 |

#### `ModelClient` (`core/model_client.py`)

以容错方式调用 LLM API，agent_loop 通过此 client 调用 API，无需关心内部的重试细节和当前使用的模型。

```
ModelClient
├── call(messages, tools) → ApiCallResult
│   └── 单次 API 调用 + 可恢复错误退避重试 + thinking-budget 检测
├── call_with_continuation(messages, tools) → ApiCallResult
│   └── call() + 输出截断时自动续写
├── try_fallback() → bool
│   └── 切换到下一个可用的备用模型
├── try_recover_main() → None
│   └── 冷却期过后尝试切回主模型
└── check_health() → bool
    └── 轻量级 1-token ping
```

### 与 agent 循环的集成

`ModelClient` 持有自己的 `OpenAI` 客户端。故障转移时在内部重建 client，对 agent_loop 透明。

**agent_loop** 的职责：
- 启动时 `model_client.check_health()`
- 每轮对话前 `model_client.try_recover_main()`
- 将 `model_client`（替代 `OpenAI client`）传入 `run_conversation()`

**run_conversation** 的职责：
- 调用 `model_client.call_with_continuation()` 获取模型响应
- 失败时调用 `_recover_from_error()` 执行分类恢复：
  - `CONTEXT_OVERFLOW` → 压缩后重试一次
  - `AUTH_FAILURE` / `MODEL_NOT_FOUND` → 故障转移后重试
  - 其他失败 → 返回 `ConversationResult(error=...)`

### 错误恢复函数

`_recover_from_error()` 是 agent.py 内部的辅助函数，根据 `ErrorCategory` 执行对应的恢复策略：

```python
def _recover_from_error(result, api_messages, messages, tools,
                         augmented_system, model_client, compressor, is_active):
    if result.category == CONTEXT_OVERFLOW:
        compressor.compress(messages) → retry call
    if result.category in (AUTH_FAILURE, MODEL_NOT_FOUND):
        model_client.try_fallback() → retry call_with_continuation
    return result
```

### 数据模型变更

`ConversationResult` 新增 `error` 字段：

```python
class ConversationResult(BaseModel):
    final_response: str
    messages: list[dict[str, Any]]
    compression_triggered: bool = False
    error: str | None = None
```

发生不可恢复错误时，`final_response` 为空，`error` 包含错误描述。agent_loop 根据此字段向用户展示错误信息。

## 配置项

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `MAX_RETRIES` | 3 | 可恢复错误最大重试次数 |
| `RETRY_BASE_DELAY` | 5.0 | 首次退避等待秒数 |
| `RETRY_MAX_DELAY` | 60.0 | 退避等待上限秒数 |
| `RETRY_JITTER` | 0.1 | 抖动比例 |
| `MAX_CONTINUATION_ATTEMPTS` | 3 | 续写最大尝试次数 |
| `BACKUP_MODELS` | "" | 备用模型列表（逗号分隔） |
| `MAIN_MODEL_COOLDOWN_SECONDS` | 300 | 故障转移后切回主模型的冷却秒数 |

## 测试覆盖

24 个测试用例覆盖以下维度：

- **ErrorCategory / ApiCallResult 模型**：枚举值完整性、默认字段、各种构造方式
- **BACKUP_MODELS 解析**：空字符串、空格、单值、多值、逗号边界
- **API 调用错误分类**：每种异常类型 → 正确的 ErrorCategory（含重试成功和重试耗尽）
- **续写**：不需要续写、文本累积续写、tool_calls 截断丢弃、续写次数耗尽
- **故障转移**：无备份、切换成功、全部不可用、跳过当前模型
- **主模型恢复**：非 fallback 状态不操作、冷却期内不恢复、恢复成功、恢复失败保持 fallback
- **健康检查**：连通正常、连通失败
