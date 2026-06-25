# Spec：Hermes Agent 对话循环

## 1. 背景与目标（Why）

语言模型本身只做"生成下一段内容"。如果没有一层代码在中间反复"调 API → 执行工具 → 写回结果 → 再调 API"，模型就只是一个"会说话的程序"，还不是一个"会干活的 agent"。

本项目（dezhu-agent）实现了这个核心对话循环，作为整个 agent 系统的基础设施。第一版已交付**可直接使用的对话循环**，包含同步执行、工具调用、DeepSeek 思考模式、流式输出和缓存命中追踪等特性。

**成功指标**：
- 用户输入消息后，循环能正确解析模型的 `finish_reason`（stop / tool_calls / length），按需执行工具并返回最终回复
- 支持 DeepSeek 思考模式（`THINKING_ENABLED`），清晰分离思维链与最终答案
- 支持流式输出（`STREAM_MODE`），逐 chunk 输出思考过程和回复内容
- 每次回复后展示 KV Cache 命中统计（`📊 缓存 xxx/xxx (xx%) 命中`）
- 全部验收通过：24 条测试（N1-N4, E1-E4, B1-B4, T1-T4, S1-S3, C1-C3）

**下一步方向**：Gateway 多平台入口、子 agent 嵌套、异步工具桥接、消息压缩与上下文管理。

---

## 2. 范围

### 做（In scope）

- 一个同步的 `run_conversation` 入口方法，接收用户消息和可选历史消息，返回最终回复和完整消息历史
- iteration budget 机制：默认最多 90 次 API 调用，budget 可被消耗和共享
- `messages` / `api_messages` 双份消息区分：内部完整账本 vs 发给 API 的清洗副本
- system prompt 每次 API 调用时重新拼装到消息前面
- 解析 `finish_reason`（`stop` / `tool_calls` / `length`）决定下一步
- 工具调用：解析模型返回的 `tool_calls`，执行本地函数，将结果写回消息历史
- **硬编码 1-2 个工具**（如 `read_file`）跑通流程，不透出注册 API
- 基于 **openai SDK** 调用 OpenAI 兼容格式的 API（如 DeepSeek）
- 模型名称通过**配置管理**（环境变量或配置文件读取），默认使用 `deepseek-flash`
- 仅 CLI 入口：支持 `python -m dezhu_agent` 启动
- 仅同步工具执行，通过桥接处理异步工具（第一版无异步工具）
- DeepSeek 思考模式：通过配置 `THINKING_ENABLED` 开启，`REASONING_EFFORT` 控制思考强度
- 流式输出：通过配置 `STREAM_MODE` 切换，`on_stream_chunk` 回调逐 chunk 输出
- `reasoning_content` 字段：支持 DeepSeek 思考模式下的思维链内容，有 tool_calls 时自动回传
- DeepSeek KV Cache 命中追踪：从 API 响应 `usage` 中提取 `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens`，存入 Message，并在每次回复后向用户展示缓存命中情况；流式模式下通过 `stream_options: {"include_usage": true}` 获取 usage

### 不做（Out of scope）

- Gateway 多平台消息入口（后续迭代）
- 子 agent / 嵌套 agent（后续迭代）
- 异步工具桥接的具体实现（第一版仅预留接口概念）
- 工具注册框架 / Tool 基类设计（第一版硬编码）
- 多轮复杂工具调用链的优化
- 消息压缩 / 上下文窗口管理
- 持久化消息历史到磁盘
- 流式输出（已实现，但仅限 CLI 逐 chunk 输出，不支持 WebSocket／SSE 协议）

---

## 3. 约束

### 技术栈
- **语言**：Python ≥3.10
- **包管理**：uv（遵循项目既有约定）
- **LLM 客户端**：openai Python SDK（用于调用 OpenAI 兼容格式的 API）
- **代码风格**：通过 ruff check（lint）和 ruff format（格式化），遵循项目既有配置
- **测试框架**：pytest

### 性能
- 单次 API 调用的超时默认值：30 秒
- iteration budget 上限：90（可配置，但默认值如无特殊理由不变更）
- 不对多轮对话做 Token 计数压缩（第一版不做上下文窗口管理）

### 安全
- API Key 通过项目根目录的 `.env` 文件或环境变量加载（`OPENAI_API_KEY`），不写入代码或提交到版本控制
- 工具的 whitelist 机制：只能调用硬编码登记的工具，不可动态注册
- 工具执行结果不经过二次校验直接写回消息（信任模型产生的工具参数——第一版不做参数校验）

### 向后兼容
- 第一版无用户，不涉及兼容问题
- 消息历史格式在后续迭代中**只增字段不删字段**

---

## 4. 既有决策

### 已选定
| 决策项 | 选定方案 | 理由 |
|--------|----------|------|
| 范围切割 | 仅 CLI + 同步工具 + 单 agent | 最小可行版本，降低首次交付复杂度 |
| 工具注册 | 硬编码 1-2 个工具 | 不透出注册 API，验证循环正确性优先 |
| 验收尺度 | 单工具调用场景 | 一条消息 → 一次工具调用 → 返回结果即为通过 |
| 产出物 | 仅 CLI（`python -m dezhu_agent`） | 兼顾可演示性和可集成性 |
| LLM 客户端 | openai SDK | 生态成熟，兼容 DeepSeek 等第三方 API |
| 模型配置 | 配置化管理（环境变量/配置文件） | 避免硬编码，便于切换 |
| 同步/异步 | 同步 `def` 实现 | 设计倾向，第一版做同步，后续可讨论迁移 |
| 项目归属 | dezhu-agent 为独立项目 | 与 Reasonix 无关 |

### 已否决
| 方案 | 否决理由 |
|------|----------|
| 第一版直接上 Gateway | 增加不必要的复杂性，影响核心循环的快速验证 |
| 第一版使用 httpx 裸调用 API | openai SDK 提供了更完善的错误处理和重试逻辑 |
| 第一版全异步 (async def) | 徒增心智负担，同步版本足以验证循环的正确性 |

---

## 5. 行为与验收

### 正常路径

- **N1 — 直接回复（无工具调用）**
  WHEN 用户发送一条不需要使用工具的消息（如"你好"），
  THE SYSTEM SHALL 调用一次 LLM API，
  AND 在模型返回 `finish_reason: stop` 后，
  AND 将模型的回复文本作为 `final_reply` 返回，
  AND 使用 iteration ≤ 1。

- **N2 — 单次工具调用**
  WHEN 用户发送一条需要调用工具的消息（如"读一下 README.md"），
  THE SYSTEM SHALL 调用 LLM API，
  AND 在模型返回 `finish_reason: tool_calls` 后，
  AND 解析 `tool_calls` 中的工具名和参数，
  AND 执行对应的硬编码工具函数，
  AND 将工具执行结果写回消息历史，
  AND 再次调用 LLM API，
  AND 在模型返回 `finish_reason: stop` 后，
  AND 返回最终回复，
  AND 使用的 iteration ≤ 3。

- **N3 — 模型输出截断**
  WHEN 模型返回 `finish_reason: length`，
  THE SYSTEM SHALL 将当前不完整回复追加到消息历史，
  AND 自动发起下一次 API 调用继续生成，
  AND 最多持续到 iteration budget 耗尽为止。

- **N4 — CLI 入口**
  WHEN 用户在终端执行 `python -m dezhu_agent`，
  THE SYSTEM SHALL 进入交互式循环，
  AND 通过 `input()` 读取用户消息，
  AND 调用 `run_conversation`，
  AND 将最终回复打印到 stdout，
  AND 在收到 EOF（Ctrl+D/Ctrl+Z）时退出循环。

### 异常路径

- **E1 — API 调用失败**
  WHEN LLM API 返回错误（网络超时、认证失败、rate limit），
  THE SYSTEM SHALL 抛出或向上传递异常，
  AND 不继续循环（fail-fast 策略）。

- **E2 — 工具不存在**
  WHEN 模型返回的 `tool_calls` 中引用了未注册的工具名，
  THE SYSTEM SHALL 向消息历史追加一条工具错误消息（内容：`Tool '<name>' not found`），
  AND 继续循环让模型修正。

- **E3 — 工具执行异常**
  WHEN 工具函数在执行过程中抛出异常，
  THE SYSTEM SHALL 捕获异常，
  AND 向消息历史追加一条工具错误消息（内容：`Error executing tool '<name>': <exception>`），
  AND 继续循环让模型处理。

- **E4 — Iteration budget 耗尽**
  WHEN 当前 iteration 数达到 budget 上限时模型仍未返回 `finish_reason: stop`，
  THE SYSTEM SHALL 停止循环，
  AND 返回当前消息历史中最后一条 assistant 消息作为最终回复（可能为空或截断）。

### 边界条件

- **B1 — 空消息**
  WHEN 用户发送空字符串消息，
  THE SYSTEM SHALL 跳过 API 调用，
  AND 返回一个指明"消息为空"的提示作为最终回复。

- **B2 — 消息历史包含非标准字段**
  WHEN `messages` 中包含 `reasoning`、`_internal_token_count` 等内部字段，
  THE SYSTEM SHALL 在组装 `api_messages` 时自动移除这些内部字段，
  AND 不报错、不丢失核心消息内容。

- **B3 — 单次 API 返回多个 tool_calls**
  WHEN 模型在一次响应中同时请求调用多个工具，
  THE SYSTEM SHALL 按顺序逐一执行所有工具，
  AND 将所有工具结果一起写回消息历史，
  AND 再发起一次 API 调用。

- **B4 — System prompt 包含工具定义**
  WHEN 系统中有已注册的工具，
  THE SYSTEM SHALL 在每次 API 调用前的 system prompt 末尾附上工具的 JSON schema 定义，
  AND 格式符合 OpenAI 兼容 API 的 `tools` 参数规范。

### 思考模式验收（DeepSeek）

> 参考文档：[DeepSeek 思考模式](https://api-docs.deepseek.com/zh-cn/guides/thinking_mode)

- **T1 — 思考模式正常回复**
  WHEN 开启 THINKING_ENABLED 且模型回复中包含 reasoning_content，
  THE SYSTEM SHALL 将该 reasoning_content 存入 Message 的 `reasoning_content` 字段，
  AND 最终回复仍以 `content` 为准。

- **T2 — 思考模式 + 工具调用**
  WHEN 思考模式下模型进行工具调用，
  THE SYSTEM SHALL 在 `to_api_dict()` 中保留 `reasoning_content` 字段，
  AND 在后续所有轮次中回传该 reasoning_content 给 API。

- **T3 — 思考模式关闭**
  WHEN THINKING_ENABLED 为 false，
  THE SYSTEM SHALL 不向 API 发送 `extra_body.thinking` 参数，
  AND 返回的 reasoning_content 应为 None。

- **T4 — 无工具调用时不回传 reasoning**
  WHEN 模型在思考模式下未进行工具调用（直接回复），
  THE SYSTEM SHALL 在下一轮请求的 API 消息中不包含上一轮的 reasoning_content
  （DeepSeek API 会忽略不带 tool_calls 的 reasoning_content）。

### 流式输出验收

> 思考模式及流式部分参考：[DeepSeek 思考模式](https://api-docs.deepseek.com/zh-cn/guides/thinking_mode)

- **S1 — 流式输出正常**
  WHEN 开启 STREAM_MODE 且传入 `on_stream_chunk` 回调，
  THE SYSTEM SHALL 调用 `call_llm_stream()` 替代 `call_llm()`，
  AND 通过回调逐 chunk 输出 `content_delta`。

- **S2 — 流式 + 思考模式**
  WHEN 流式模式下模型返回 thinking 内容，
  THE SYSTEM SHALL 通过回调的 `reasoning_delta` 字段逐 chunk 输出思维链内容，
  AND 最终 LLMResponse 的 reasoning_content 包含完整思维链。

- **S3 — 流式模式关闭**
  WHEN STREAM_MODE 为 false，
  THE SYSTEM SHALL 使用非流式 `call_llm()` 调用，
  AND 不触发 `call_llm_stream()`。

### KV Cache 验收（DeepSeek）

> 参考文档：[DeepSeek 上下文硬盘缓存](https://api-docs.deepseek.com/zh-cn/guides/kv_cache)

- **K1 — 非流式调用提取缓存统计**
  WHEN 使用非流式 `call_llm()` 调用 DeepSeek API，
  THE SYSTEM SHALL 从 `response.usage` 中读取 `prompt_cache_hit_tokens` 和 `prompt_cache_miss_tokens`，
  AND 将它们填入 `LLMResponse` 返回。

- **K2 — 流式调用提取缓存统计**
  WHEN 使用流式 `call_llm_stream()` 调用 DeepSeek API，
  THE SYSTEM SHALL 在 API 请求中携带 `stream_options: {"include_usage": true}`，
  AND 从最后一个 chunk 的 `usage` 中提取 `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens`，
  AND 将它们填入该 chunk 的 `StreamChunk` 和最终的 `LLMResponse`（StopIteration.value）。

- **K3 — Message 记录缓存统计**
  WHEN `run_conversation` 收到 `LLMResponse`，
  THE SYSTEM SHALL 将 `prompt_cache_hit_tokens` 和 `prompt_cache_miss_tokens` 存入该轮 assistant Message 对应字段，
  AND 这些字段不作为 API 消息发送（仅内部记录）。

- **K4 — CLI 展示缓存命中**
  WHEN 每次 assistant 回复完成（流式 finish chunk 或非流式返回），
  THE SYSTEM SHALL 在 stdout 输出缓存命中信息，
  AND 格式为 `📊 缓存命中: <N> tokens | 未命中: <M> tokens`。

- **K5 — 非 DeepSeek provider 兼容**
  WHEN 调用非 DeepSeek API（usage 中无 `prompt_cache_hit_tokens` 字段），
  THE SYSTEM SHALL 以 `getattr(..., 0)` 兜底，默认缓存命中/未命中为 0，
  AND 不抛出异常、不影响正常对话流程。

---

## 6. 任务拆解

1. **项目脚手架** — 创建 `src/dezhu_agent/` 包结构、`pyproject.toml`（含 CLI entry point `python -m dezhu_agent`）、`__init__.py`、`__main__.py`
2. **配置模块** — 实现配置读取（模型名、API base URL、API key、budget 上限、超时等），从环境变量 + 配置文件读取，提供合理的默认值
3. **消息模型** — 实现 `Message` 数据结构（role、content、tool_calls、tool_call_id 等），以及 `messages → api_messages` 清洗逻辑
4. **System prompt 组装** — 构建 system prompt 模板，包含人设 + 工具定义 + 使用规则
5. **LLM API 客户端** — 基于 openai SDK 封装 `call_llm()` 和 `call_llm_stream()`，返回 `LLMResponse`（含 reasoning_content），支持 DeepSeek 思考模式
6. **工具执行器** — 实现工具查找和执行逻辑，硬编码 `read_file` 和 `write_file` 两个工具（每个工具包含 name、description、parameters JSON schema、执行函数）
7. **核心循环** — 实现 `run_conversation(user_message, history?)`：接收消息 → 组装 system prompt → 调用 API → 判断 finish_reason → 执行工具/返回结果 → 重复直到 stop 或 budget 耗尽
8. **CLI 入口** — 实现 `__main__.py` 交互式循环：通过 `input()` 读取消息，调用 `run_conversation`，打印结果，EOF 退出
9. **测试** — 覆盖第 5 节中 N1-N4、E1-E4、B1-B4、T1-T4、S1-S3 每一条验收（使用 mock LLM API 响应，不依赖真实 API 调用）
10. **DeepSeek 思考模式** — 在 `llm.py` 中通过 `extra_body` 和 `reasoning_effort` 开启，在 `messages.py` 中处理 `reasoning_content` 的有条件回传
11. **流式输出** — 实现 `call_llm_stream()` 生成器，`loop.py` 中 `on_stream_chunk` 回调路径，`__main__.py` 中流式打印
12. **DeepSeek KV Cache 追踪** — 在 `llm.py` 中从 usage 提取缓存统计字段，在 `messages.py` 的 Message 中新增缓存字段，在 `loop.py` 中传递，在 `__main__.py` 中展示，流式模式下开启 `stream_options.include_usage`

---

---
## 实现完成状态

下列子任务已在首轮实现中完成（对应 `spec_plan` 阶段）：

| 子任务 | 状态 | 文件 |
|--------|------|------|
| 1. 项目脚手架 | ✅ | `pyproject.toml`, `src/dezhu_agent/__init__.py`, `src/dezhu_agent/__main__.py` |
| 2. 配置模块 | ✅ | `src/dezhu_agent/config.py`（.env + 环境变量加载，API Key 延迟 fail-fast） |
| 3. 消息模型 | ✅ | `src/dezhu_agent/messages.py`（Message dataclass + api_messages 清洗） |
| 4. System prompt 组装 | ✅ | `src/dezhu_agent/prompt.py` |
| 5. LLM API 客户端 | ✅ | `src/dezhu_agent/llm.py`（LLMResponse + call_llm_stream + 思考模式支持） |
| 6. 工具执行器 | ✅ | `src/dezhu_agent/tools.py`（read_file + write_file 白名单） |
| 7. 核心循环 | ✅ | `src/dezhu_agent/loop.py`（run_conversation + 流式回调 + reasoning_content 传递） |
| 8. CLI 入口 | ✅ | `src/dezhu_agent/__main__.py`（交互式 input() 循环，EOF 退出） |
| 9. 测试 | ✅ | `tests/test_loop.py`（21 条验收测试：14 原有 + 4 思考模式 + 3 流式） |
| 10. .env.example | ✅ | `.env.example`（含 THINKING_ENABLED / REASONING_EFFORT / STREAM_MODE） |
| 11. 思考模式 | ✅ | 全链路：config → llm._build_kwargs → Message.reasoning_content → 条件回传 |
| 12. 流式输出 | ✅ | call_llm_stream + loop._run_streaming_call + main._print_stream_chunk |
| 13. KV Cache 追踪 | ✅ | `llm.py`（usage 提取）+ `messages.py`（缓存字段）+ `loop.py`（传递）+ `__main__.py`（展示） |

<!-- SPEC_STATUS: reviewed — 结论：⚠️ 修后合并 -->

---

## 参考文档

| 文档 | 链接 |
|------|------|
| DeepSeek 上下文硬盘缓存（KV Cache） | <https://api-docs.deepseek.com/zh-cn/guides/kv_cache> |
| DeepSeek 思考模式（思维链 + 流式） | <https://api-docs.deepseek.com/zh-cn/guides/thinking_mode> |
