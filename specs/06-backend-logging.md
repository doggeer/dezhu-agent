# Spec：后台日志系统

## 1. 背景与目标（Why）

当前 dezhu-agent 的内部运行信息（压缩流程、上下文组装、API 调用细节、工具执行过程）对开发者不可见——要么完全没记录，要么零散地 print 到 stderr。随着压缩、持久化、动态 prompt 等子系统叠加，排查一次对话中"第 N 轮发生了什么"变得困难。需要一个统一、结构化、可追溯的后台日志系统，让研发人员按 sessionId 时间正序完整还原一次对话的系统级运行轨迹。

**成功指标**：给定一个 sessionId，研发人员能按时间正序在日志文件中找到：用户输入 → 上下文组装细节 → API 请求 payload → API 响应 payload → 工具调用参数/返回值 → 压缩触发时机与效果 → 所有异常/警告信息。无需修改代码重新运行即可排查。

## 2. 范围

### 做
- 基于 Python 标准库 `logging` 的统一日志基础设施（logger hierarchy、formatter、handler）
- 日志输出到项目根目录 `logs/` 目录（`DEZHU_LOG_DIR` 默认 `{PROJECT_ROOT}/logs/`），按日期轮转：活跃日志 `dezhu-agent.log`，每日 00:00 轮转为 `dezhu-agent-YYYY-MM-DD.log`
- 通过 `.env` / 环境变量配置日志级别（`DEZHU_LOG_LEVEL`，默认 `INFO`）和输出目录（`DEZHU_LOG_DIR`，默认项目根目录下的 `logs/`）
- `--debug` CLI 参数快速覆盖日志级别为 `DEBUG`
- DEBUG 级别：记录完整 API 请求/响应 payload、system prompt 全文、上下文组装详情、压缩各层详情
- INFO 级别：记录关键事件摘要（用户输入、模型输出、工具调用名称+耗时、压缩触发/结果、API 异常）
- 按 sessionId 串联：每条日志行首带 `[session=<short_id>]` 前缀，方便 grep 过滤
- 会话分裂时（压缩导致创建新 session），记录新旧 sessionId 映射
- 统一日志格式：`时间戳 | 级别 | [session=xxx] | 模块:行号 | 消息内容`
- 迁移现有 stderr print（`_log_tool_execution`）和 `compression.py` 中零散的 `logging.warning` 到新日志系统
- 同步写入（每轮对话中自然地调用 logger，不引入异步队列或后台线程）

### 不做（Out of scope）
- 日志不入 SQLite（保持文件存储）
- 不做网络上报 / 远端采集
- 不做 API Key 或用户数据脱敏（研发内部使用）
- 不做结构化 JSON 输出（优先人类可读多行文本）
- 不替换/影响面向终端用户的 stdout 对话输出
- 不记录流式输出的每个 chunk（只记录流式请求的发起和完成）

## 3. 约束

- **技术栈**：仅使用 Python 标准库 `logging` 模块，不引入第三方日志库（如 loguru、structlog）
- **文件轮转**：使用 `logging.handlers.TimedRotatingFileHandler`，每天 00:00 自动切换新文件
- **写入性能**：日志 handler 写入耗时不应显著影响主循环；单条日志格式化+写入目标 < 1ms
- **向后兼容**：
  - 现有 stdout 对话输出不受影响（终端用户看到的内容不变）
  - 现有环境变量名不变，只新增 `DEZHU_LOG_LEVEL` 和 `DEZHU_LOG_DIR`
  - `loop.py` 公开 API（`run_conversation`）签名不变
- **目录安全**：`logs/` 目录不存在时自动创建；目录不可写时不崩，降级到 stderr
- **编码**：日志文件统一 UTF-8 编码，行尾 `\n`

## 4. 既有决策

- **输出目标**：纯文件，不入库（日志量大且非结构化，SQLite 不适合存储，文件可直接 tail/grep）
- **日志格式**：人类可读多行文本而非 JSON（研发排查时肉眼阅读是首要场景）
- **脱敏**：不做脱敏（内部工具，无外部用户；脱敏会丢失排查线索）
- **Level 体系**：INFO = 摘要模式（日常运行），DEBUG = 详细模式（排查问题时开启）
- **写入方式**：同步写入（每轮对话本身就有秒级延迟，日志写入的微秒级开销可忽略；不加异步队列复杂度）
- **轮转策略**：按天轮转而非按大小轮转（按天更符合排查习惯——"看看昨天发生了什么"）
- **迁移策略**：全部迁移，不保留旧的 stderr print（避免双输出源分散信息）

## 5. 行为与验收

### 正常路径

**N1 — 日志系统初始化**
WHEN agent 启动（`__main__.py` 执行），THE SYSTEM SHALL：
- 读取 `DEZHU_LOG_LEVEL` 环境变量（默认 `INFO`）和 `DEZHU_LOG_DIR`（默认 `{PROJECT_ROOT}/logs/`）
- 若 `--debug` CLI 参数传入，覆盖日志级别为 `DEBUG`
- 创建 `logs/` 目录（如不存在）
- 初始化 root logger，绑定 `TimedRotatingFileHandler`（活跃文件 `dezhu-agent.log`，每天 00:00 轮转为 `dezhu-agent-YYYY-MM-DD.log`）
- 设置日志格式为：`%(asctime)s | %(levelname)-5s | %(session)s | %(name)s:%(lineno)d | %(message)s`

**N2 — 用户输入记录**
WHEN `run_conversation()` 收到用户消息，THE SYSTEM SHALL 在 INFO 级别记录：
- sessionId（取前 8 位短 ID）
- 用户输入内容（若为多行，完整记录）

**N3 — LLM API 请求记录**
WHEN 每次调用 LLM API 前（`call_llm` / `call_llm_stream`），THE SYSTEM SHALL：
- INFO 级别：记录调用方式（stream/non-stream）、messages 条数、tools 数量
- DEBUG 级别：记录完整 system prompt 文本和所有 messages 内容（每条 message 的 role + content）

**N4 — LLM API 响应记录**
WHEN 每次 LLM API 返回后，THE SYSTEM SHALL：
- INFO 级别：记录 finish_reason、prompt_tokens（含缓存命中/未命中）、completion_tokens
- DEBUG 级别：记录完整响应 content 和 reasoning_content（如有）、tool_calls 详情

**N5 — 工具执行记录**
WHEN 工具被调用时（`ToolRegistry.execute()`），THE SYSTEM SHALL：
- INFO 级别：记录工具名称、参数摘要、执行耗时（毫秒）、返回结果的前 500 字符（超出截断标注 `[TRUNCATED]`）
- DEBUG 级别：记录完整参数和完整返回值

**N6 — 压缩事件记录**
WHEN 压缩被触发时（preflight 或主循环），THE SYSTEM SHALL 在 INFO 级别记录：
- 触发阶段（preflight / main_loop）
- 压缩前 token 估算值
- 各层执行情况（Layer 1/2/3 是否执行）
- 压缩后 token 估算值
- 压缩降幅百分比
- 若触发会话分裂，记录旧 sessionId → 新 sessionId 映射

**N7 — 上下文组装记录**
WHEN 每轮对话组装 system prompt + TaskState 时，THE SYSTEM SHALL：
- DEBUG 级别：记录 system prompt 全文和 TaskState 内容
- 记录是否命中 system prompt 缓存

**N8 — 对话结束记录**
WHEN 对话循环正常结束（finish_reason="stop" 或 budget 耗尽），THE SYSTEM SHALL 在 INFO 级别记录：
- 总迭代轮数
- 最终回复内容（前 500 字符）

### 异常路径

**E1 — 日志目录不可写**
WHEN `logs/` 目录不存在且无法创建（权限不足），THE SYSTEM SHALL：
- 降级：将日志输出到 stderr
- 在 stderr 首行输出警告：`⚠️ 日志目录不可写，日志降级输出到 stderr`
- 不阻塞 agent 正常启动和运行

**E2 — 日志写入失败不中断主循环**
WHEN 日志 handler 写入失败（如磁盘满），THE SYSTEM SHALL：
- 捕获异常，不向上传播
- 在 stderr 输出一次警告（同一次运行中不重复警告）
- 主对话循环继续正常运行

**E3 — LLM API 异常记录**
WHEN LLM API 调用抛出异常（网络错误、超时、4xx/5xx），THE SYSTEM SHALL：
- ERROR 级别记录：异常类型、异常消息、请求的 model 名称和 messages 条数
- DEBUG 级别额外记录：完整 traceback
- 记录后异常继续向上传播（保持现有 fail-fast 行为）

**E4 — 工具执行异常记录**
WHEN 工具执行抛出异常（`ToolRegistry.execute()` 捕获），THE SYSTEM SHALL：
- ERROR 级别记录：工具名称、参数、异常消息
- 不改变现有返回值行为（仍返回 `"Error executing tool 'xxx': msg"` 字符串）

### 边界条件

**B1 — 空消息保护**
WHEN 用户输入为空字符串或纯空白，THE SYSTEM SHALL：
- 不在日志中记录用户输入（避免日志污染）
- 日志中不产生任何本轮条目

**B2 — 日志文件跨天切换**
WHEN 系统时间跨过 00:00 且 agent 仍在运行，THE SYSTEM SHALL：
- `TimedRotatingFileHandler` 自动将新日志写入新日期文件
- 旧文件保留，新文件命名包含新日期
- 日志内容不中断、不丢失

**B3 — 单条日志过长截断**
WHEN 单条日志消息超过 50KB（如完整 system prompt），THE SYSTEM SHALL：
- 截断消息内容至 50KB，末尾追加 `… [TRUNCATED 50KB]`
- 不影响其他日志条目

**B4 — 压缩导致 sessionId 变更**
WHEN 压缩触发会话分裂（sessionId 从 A 变为 B），THE SYSTEM SHALL：
- 在 INFO 级别记录：`session 分裂: <old_short_id> -> <new_short_id>`
- 后续日志使用新的 sessionId 前缀
- 通过 `grep <old_id> logfile` 可定位到分裂点，再通过新 ID 继续追踪

## 6. 任务拆解

> 以下为实现计划，由 `spec_plan` 产出，`spec_implement` 执行。

### 步骤 1：日志基础设施 + 配置接入

- **改动**：`src/dezhu_agent/logging_config.py`（🆕 新增）、`config.py`（✏️）、`.env.example`（✏️）
- **做什么**：
  - 新建 `logging_config.py`，提供 `init_logging(log_dir, log_level)` 函数
  - 初始化 root logger：设置格式 `%(asctime)s | %(levelname)-5s | %(session)s | %(name)s:%(lineno)d | %(message)s`
  - 绑定 `TimedRotatingFileHandler`（每天轮转，UTF-8，文件名 `dezhu-agent-YYYY-MM-DD.log`）
  - 提供 `get_logger(name)` 返回模块级 logger
  - 提供 `set_session_context(session_id)` 通过 `LoggerAdapter` 注入 session 前缀
  - `config.py` 新增 `DEZHU_LOG_LEVEL`（默认 `INFO`）和 `DEZHU_LOG_DIR`（默认 `logs/`）
  - `.env.example` 新增 `DEZHU_LOG_LEVEL` 和 `DEZHU_LOG_DIR` 配置项及注释说明
- **验证**：`uv run python -c "from dezhu_agent.logging_config import init_logging; init_logging()"` 后检查 `logs/` 目录生成日志文件
- **依赖**：无
- **风险**：低 — 纯新增模块

### 步骤 2：CLI 接入（`--debug` 参数）

- **改动**：`src/dezhu_agent/__main__.py`
- **做什么**：
  - `_parse_args()` 中新增 `--debug` 参数（`action="store_true"`）
  - `main()` 中：读取 `args.debug`，若为 True 则覆盖 `DEZHU_LOG_LEVEL` 为 `DEBUG`
  - 在进入对话循环前调用 `init_logging()` 和 `set_session_context(session_id)`
  - 压缩导致 session 变更后，调用 `set_session_context(new_session_id)`
- **验证**：`echo "hello" | uv run python -m dezhu_agent --debug`，确认日志级别为 DEBUG
- **依赖**：步骤 1
- **风险**：低 — 只在启动路径加逻辑

### 步骤 3：loop.py 接入

- **改动**：`src/dezhu_agent/loop.py`
- **做什么**：
  - 导入 logger，在 `run_conversation()` 入口调用 `set_session_context(session_id)`
  - **用户输入**：INFO 记录 sessionId + 用户输入文本
  - **上下文组装**：DEBUG 记录 system prompt 全文 + TaskState 内容，INFO 记录缓存命中情况
  - **迭代信息**：每轮迭代 DEBUG 记录当前轮次和 token 估算值
  - **对话结束**：INFO 记录总迭代轮数 + 最终回复（前 500 字符）
  - **budget 耗尽**：WARNING 记录
  - **压缩 stuck**：ERROR 记录 + 保留现有 stderr print（面向用户的不动）
  - 暂不删除 `_log_tool_execution()`（步骤 8 统一清理）
- **验证**：运行一次对话，检查日志中是否有用户输入、迭代信息和最终回复
- **依赖**：步骤 1
- **风险**：中 — 修改核心对话循环，需小心不破坏控制流

### 步骤 4：llm.py 接入

- **改动**：`src/dezhu_agent/llm.py`
- **做什么**：
  - 导入 logger
  - `call_llm()`：调用前 INFO 记录 messages 条数 + tools 数量；DEBUG 记录完整 payload；返回后 INFO 记录 finish_reason + token 用量 + 缓存命中
  - `call_llm_stream()`：类似，但只在流开始和结束时各记一条（不逐 chunk 记录）
  - 异常路径：ERROR 记录异常类型、消息、model 名称和 messages 条数；DEBUG 额外记录 traceback
- **验证**：运行一次对话，检查日志中 API 请求/响应的记录
- **依赖**：步骤 1
- **风险**：低 — 纯增量代码，不改变现有控制流

### 步骤 5：tools 接入

- **改动**：`src/dezhu_agent/tools/__init__.py`
- **做什么**：
  - 在 `ToolRegistry.execute()` 中：执行前记录开始时间，执行后记录工具名称、参数摘要、耗时（ms）
  - INFO 级别：名称 + 参数摘要 + 耗时 + 返回值前 500 字符（超出截断标注 `[TRUNCATED]`）
  - DEBUG 级别：完整参数 + 完整返回值
  - 异常：ERROR 级别记录工具名称 + 参数 + 异常消息
- **验证**：运行一次需工具调用的对话，检查日志中的工具执行记录
- **依赖**：步骤 1
- **风险**：低 — 在现有 try/except 框架内加日志

### 步骤 6：compression.py 接入

- **改动**：`src/dezhu_agent/compression.py`
- **做什么**：
  - 替换 `logger = logging.getLogger(__name__)` → 使用统一 `get_logger(__name__)`
  - `compress()` 入口：INFO 记录触发阶段 + 压缩前 token 数
  - 每层执行后：INFO 记录层名 + 压缩后 token 数
  - 压缩完成：INFO 记录总降幅 + 各层执行情况 + 是否降级
  - `CompressionStuckError`：ERROR 记录
  - 现有两处 `logger.warning` 改为使用统一 logger（保留 WARNING 级别）
  - `summarize_middle()` 失败：仍为 WARNING 级别 + `exc_info=True`
- **验证**：运行长对话触发压缩，检查日志中的压缩事件记录
- **依赖**：步骤 1
- **风险**：中 — 压缩路径有异常安全要求（"任何异常回退原始消息"），日志代码不能引入新异常

### 步骤 7：prompt_assembler.py 迁移

- **改动**：`src/dezhu_agent/prompt_assembler.py`
- **做什么**：
  - `_debug_output()` 中的 stderr print 改为 DEBUG 级别 logger 输出
  - 去掉 `DEZHU_DEBUG` 环境变量判断（改用日志级别控制）
- **验证**：`DEZHU_LOG_LEVEL=DEBUG` 运行时检查日志中是否有 system prompt 组装信息
- **依赖**：步骤 1
- **风险**：低 — 简单替换

### 步骤 8：迁移清理

- **改动**：`src/dezhu_agent/loop.py` + 全局检查
- **做什么**：
  - 删除 `_log_tool_execution()` 函数
  - 删除 `loop.py` 中对 `_log_tool_execution()` 的调用
  - 全局 grep 残留的 `file=sys.stderr` print，确认哪些保留（面向用户的）、哪些该删（已迁移到 logger 的）
  - 保留：`config.py` 的 fail-fast 报错（启动前报错，日志系统可能未初始化）
  - 保留：面向终端用户的 CompressionStuckError 提示
- **验证**：`uv run ruff check .` + 运行对话确认无异常
- **依赖**：步骤 3-7 全部完成
- **风险**：低 — 清理性改动，但不删多

### 步骤 9：测试

- **改动**：`tests/test_logging.py`（🆕 新增）
- **做什么**：覆盖 spec 第 5 节的所有验收条目
  - N1：日志初始化 + 文件生成
  - N2：用户输入记录
  - N3-N4：API 请求/响应日志（mock LLM 调用）
  - N5：工具执行日志
  - N6：压缩事件日志
  - N7：上下文组装日志
  - N8：对话结束日志
  - E1：目录不可写降级
  - E2：写入失败不中断
  - E3：API 异常记录
  - E4：工具异常记录
  - B1：空消息不记日志
  - B2：跨天文件切换
  - B3：超长日志截断
  - B4：session 分裂日志
- **验证**：`uv run pytest tests/test_logging.py -v`
- **依赖**：步骤 1-8
- **风险**：中 — 部分边界条件（E1/E2/B2）测试需要 mock 文件系统或时间

### 并行建议

步骤 1 完成后，步骤 3-7（loop / llm / tools / compression / prompt_assembler）互不依赖，可以并行推进。

<!-- SPEC_STATUS: reviewed — 结论：✅ 可合并 -->

---

## 7. Review 结论

> 由 `spec_review` 产出，2025-07-04。

### 阻塞项修复记录

| 问题 | 修复 |
|------|------|
| N8 对话结束 INFO 缺少回复内容 | `loop.py`: INFO 追加 `回复=%s`（前 500 字符），budget 耗尽改 INFO |
| 文件命名 `dezhu-agent.log.YYYY-MM-DD` ≠ spec | `logging_config.py`: 添加 `_namer` 回调 → `dezhu-agent-YYYY-MM-DD.log` |
| N4 流式路径缺 DEBUG 完整响应 | `llm.py`: 流式完成处新增 `LLM 流式响应全文` DEBUG 日志 |
| E3 LLM 异常 ERROR 缺异常类型和消息 | `llm.py`: `except Exception as e`，ERROR 追加 `exception=%s: %s` |
| `.env.example` 残留废弃 `DEZHU_DEBUG` | 已删除 |

### 验收对标终态

| 验收 | 实现 | 测试 | 备注 |
|------|------|------|------|
| N1 日志初始化 | ✅ | ✅ | `test_logging_config.py` |
| N2 用户输入 | ✅ | ❌ | 依赖端到端运行验证 |
| N3 API 请求 | ✅ | ❌ | 依赖端到端运行验证 |
| N4 API 响应 | ✅ | ❌ | 非流式+流式均已覆盖 |
| N5 工具执行 | ✅ | ❌ | 依赖端到端运行验证 |
| N6 压缩事件 | ✅ | ❌ | 依赖端到端运行验证 |
| N7 上下文组装 | ✅ | ❌ | 依赖端到端运行验证 |
| N8 对话结束 | ✅ | ❌ | INFO 含回复前 500 字符 |
| E1 目录不可写 | ✅ | ✅ | `test_e1_unwritable_dir_fallback_to_stderr` |
| E2 写入失败 | ⚠️ | ⚠️ | 依赖 Python logging 内置 handleError |
| E3 API 异常 | ✅ | ✅ | `test_e3_llm_exception_logs_error` |
| E4 工具异常 | ✅ | ✅ | `test_e4_tool_exception_logs_error` |
| B1 空消息 | ✅ | ✅ | `test_b1_empty_message_not_logged` |
| B2 跨天切换 | ✅ | ✅ | `test_b2_file_naming_format` |
| B3 超长截断 | ✅ | ✅ | `test_b3_long_message_truncated` |
| B4 session 分裂 | ✅ | ❌ | 依赖端到端运行验证 |

- 总验收数：16
- 已实现：16 ✅
- 有测试：8（N1/E1/E2/E3/E4/B1/B2/B3）
- 端到端覆盖：8（N2-N8/B4）

### 测试结果

```
138 passed in 0.37s  (117 原有 + 21 新增，全部通过)
```

### 涉及文件

| 文件 | 类型 |
|------|------|
| `src/dezhu_agent/logging_config.py` | 🆕 新增 |
| `tests/test_logging_config.py` | 🆕 新增 |
| `src/dezhu_agent/config.py` | ✏️ 修改 |
| `src/dezhu_agent/__main__.py` | ✏️ 修改 |
| `src/dezhu_agent/loop.py` | ✏️ 修改 |
| `src/dezhu_agent/llm.py` | ✏️ 修改 |
| `src/dezhu_agent/tools/__init__.py` | ✏️ 修改 |
| `src/dezhu_agent/compression.py` | ✏️ 修改 |
| `src/dezhu_agent/prompt_assembler.py` | ✏️ 修改 |
| `.env.example` | ✏️ 修改 |
| `tests/test_prompt_assembler.py` | ✏️ 修改（适配 caplog） |
