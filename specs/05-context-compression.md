# Spec：上下文压缩子系统

## 1. 背景与目标（Why）

dezhu-agent 在多轮工具调用后会面临上下文膨胀问题：读大文件塞进大量文本，长命令产生大段输出，多轮调用后旧结果堆积。没有压缩机制会导致：(a) 模型注意力被旧输出淹没，(b) API 请求越来越重越来越贵，(c) 最终撞上上下文上限导致任务中断。

**目标**：在不丢失工作连续性的前提下，为 agent 主循环提供三层递进的上下文压缩机制，把活跃上下文重新腾出空间。

**成功指标**：
- 端到端验收测试（固定剧本，50 轮工具调用模拟）同时满足：(a) 任务完成率不劣于无压缩场景，(b) 峰值 token 用量显著降低，(c) 压缩后模型不重复读文件或重做已完成步骤
- 压缩后 token 数降至原值 90% 以下，否则抛出 `CompressionStuckError` 并退出当前轮
- 压缩前后模型输出的内容一致性（TaskState 验证）

## 2. 范围

### 做

- **三层压缩**：
  1. 纯字符串裁剪旧工具输出（不需 LLM，最便宜）
  2. 边界对齐：保护头部（任务定义）和尾部（最近 ~20K tokens），只压缩中间已消费轮次。边界对齐时保证 `assistant.tool_calls` 与 `tool` 响应配对，避免孤儿消息
  3. 辅助 LLM 对中间部分生成结构化摘要（Goal / Progress / Key Decisions / Files Modified / Next Steps），辅助模型可配置，默认 `deepseek-v4-flash`，同步阻塞执行
- **TaskState 机制**：不进消息流的独立任务状态，每轮拼入 system prompt 末尾，永不压缩。模型通过 `task_set_goal` / `todo_write` / `todo_update` 三个工具自行维护
- **触发范围**：覆盖用户-agent 多轮对话累积 + agent 内部工具调用循环膨胀两种场景
- **Preflight 压缩**：`run_conversation()` 进入主循环前以更保守阈值（80%）检查；若头部本身就超标，尽力而为后抛 `CompressionStuckError`
- **Session 分裂**：压缩时创建新 session，通过 `parent_session_id` 指向旧 session，旧历史通过链条可追溯
- **System prompt 重建**：压缩后缓存失效，重新组装 system prompt（因记忆可能已变）
- **防御性早退**：`CompressionStuckError` 在 token 未降到 90% 以下时抛出，主循环捕获后退出当轮并提示用户
- **存储层扩展**：`StorageBackend` 的 session 表新增 `parent_session_id` 可选字段

### 不做（Out of scope）

- 跨 session 的历史归档/检索（属于 memory 子系统职责）
- 对用户在聊天框中的输入做裁剪
- 对 system prompt 本身做压缩（只重建，不裁剪）
- 异步压缩（当前版本保持同步）
- ZIP/二进制压缩（"压缩"仅指语义摘要化）

## 3. 约束

- **上下文窗口**：默认 200K tokens，不同模型可配置各自的窗口大小。压缩阈值（70% / 80%）均基于当前模型配置的窗口大小计算，而非硬编码值
- **延迟上限**：辅助 LLM 摘要调用 ≤ 5 秒（单次 HTTP 请求超时），三层压缩总耗时 ≤ 10 秒。超时则降级为仅 Layer 1 + Layer 2，跳过 Layer 3
- **消息合法性**：压缩后的消息列表必须满足 LLM API 的角色序列约束——`assistant` 的 `tool_calls` 与紧随的 `tool` 角色消息必须成对出现，不得拆散
- **向后兼容**：`parent_session_id` 为新可选字段，默认 `NULL`。现有 session 的查询/写入逻辑不受影响
- **不可触碰**：`StorageBackend` 的公共接口签名保持兼容——`create_session` 可扩展参数但不可移除已有参数；消息的 `role` 和 `content` 字段语义不可变
- **辅助模型配置**：辅助模型通过配置项指定（provider + model name），默认 `deepseek-v4-flash`，允许部署者覆盖。必须复用现有 LLM 调用基础设施，不引入新的 HTTP 客户端
- **压缩不可递归**：一次压缩产生的摘要消息自身不触发再次压缩（避免摘要 → 摘要 → 摘要的死循环）

## 4. 既有决策

- **三层递进架构**（已选定，理由：第 1 层最便宜不花钱，第 2 层是纯计算，第 3 层才调 LLM——最大化性价比）
- **TaskState 不进消息流**（已选定，理由：消息流可有损、可压缩；任务目标与进度必须无损。两层分离后摘要退化为"中段对话备忘"，不再承担连续性关键载体的职责）
- **结构化摘要模板**（已选定，理由：自由文本摘要信息熵衰减快，结构化字段让模型更容易提取关键信息。模板：Goal / Progress / Key Decisions / Files Modified / Next Steps）
- **边界对齐算法 `_align_to_assistant_boundary`**（已选定，理由：从源头保证消息流合法——切点吸附到下一个非 tool 消息，比"压完再修孤儿"更彻底）
- **CompressionStuckError 早退**（已选定，理由：极端情况头部本身就吃掉了大部分预算，再多压几轮只是空转。比较前后 token 估算，未降到 90% 以下即抛异常，主循环退出当轮）
- **Session 分裂 + parent_session_id**（已选定，理由：旧历史不删除，通过链条可追溯；新 session 享有干净的消息列表）
- **System prompt 重建**（已选定，理由：压缩后记忆可能已变，缓存失效需重新组装）
- **Preflight 80% 阈值**（已选定，理由：比主循环压缩阈值更保守，主动防御而非被动等 API 报错）
- **同步阻塞执行**（已选定，理由：当前版本不做异步，简化状态管理。后续可评估异步化——下次评估条件：辅助 LLM 延迟中位数 > 3 秒时重新讨论）
- **辅助 LLM 失败降级为无摘要压缩**（已选定，理由：压缩不能因辅助模型不可用而阻塞主任务。降级意味着只执行 Layer 1 + Layer 2，跳过 Layer 3）

## 5. 行为与验收

### 正常路径

- **WHEN** agent 主循环中当前消息列表 token 估算 ≥ 压缩触发阈值（可配置，默认值为当前模型配置的上下文窗口 × 70%），**THE SYSTEM SHALL** 启动三层压缩流程
- **WHEN** 压缩启动，**THE SYSTEM SHALL** 按 Layer 1 → Layer 2 → Layer 3 顺序递进执行：每层执行后重新估算 token 数，若已低于阈值则跳过后续层
- **WHEN** Layer 1 执行，**THE SYSTEM SHALL** 将超过 N 轮（可配置，默认 20 轮）之前的 `tool` 角色消息的 `content` 替换为占位文本 `[Old tool output cleared]`，不删除消息本身
- **WHEN** Layer 2 执行，**THE SYSTEM SHALL** 计算边界——头部保留前 M 条消息（可配置，默认包含系统消息 + 前 3 轮用户-助手交互），尾部保留最近约 20K tokens 的消息——将剩余中间部分标记为可压缩段
- **WHEN** 边界切点落在 `tool` 角色消息上，**THE SYSTEM SHALL** 将切点吸附至下一个 `role != "tool"` 的消息，确保 `assistant.tool_calls` 与其 `tool` 响应始终成对出现在同一侧（同属头部、同属尾部、或同属中间段）
- **WHEN** 中间段非空，**THE SYSTEM SHALL** 调用配置的辅助 LLM，传入中间段消息，要求生成结构化摘要（字段：Goal / Progress / Key Decisions / Files Modified / Next Steps），并将中间段替换为单条摘要消息
- **WHEN** 压缩完成（三层中任意一层使 token 降至阈值以下即视为完成），**THE SYSTEM SHALL** 创建新 session（`parent_session_id` 指向旧 session），将压缩后的消息列表写入新 session，后续 API 调用使用新 session
- **WHEN** 压缩导致 session 分裂，**THE SYSTEM SHALL** 使 system prompt 缓存失效，调用 `build_system_prompt()` 重新组装（因为记忆可能已更新）
- **WHEN** 每轮 LLM API 调用前，**THE SYSTEM SHALL** 将 `task_state.render()` 的输出拼接到 system prompt 末尾，格式为 `# Task State (live, never compressed)` 后跟结构化 TODO 列表
- **WHEN** `run_conversation()` 进入主循环前，消息列表 token 估算 ≥ 当前模型配置的上下文窗口 × 80%，**THE SYSTEM SHALL** 执行 preflight 压缩（流程同主循环压缩，但使用 80% 作为阈值）

### 异常路径

- **WHEN** 压缩后 token 估算 ≥ 压缩前 token 估算 × 90%（即压缩率不足 10%），**THE SYSTEM SHALL** 抛出 `CompressionStuckError`，主循环捕获后退出当前轮，向用户输出提示"会话已无法压缩，请新开 session"
- **WHEN** preflight 压缩中，头部消息（系统提示 + 首条用户输入）本身已超过 80% 阈值且 Layer 1 裁剪后仍无法降到阈值以下，**THE SYSTEM SHALL** 尽力执行 Layer 2 后抛 `CompressionStuckError`
- **WHEN** 辅助 LLM 调用失败（超时 ≥ 5 秒、HTTP 错误、返回格式无法解析），**THE SYSTEM SHALL** 降级为仅 Layer 1 + Layer 2，跳过 Layer 3，不阻塞主循环，并在日志中记录降级原因
- **WHEN** 压缩过程中发生未预期异常，**THE SYSTEM SHALL** 回退到压缩前的消息列表，不丢失数据，并向用户输出告警

### 边界条件

- **WHEN** 中间段为空（头部 + 尾部已覆盖全部消息，无可压缩段），**THE SYSTEM SHALL** 跳过 Layer 3，不抛异常，视为压缩完成
- **WHEN** 消息列表 token 估算始终未达触发阈值，**THE SYSTEM SHALL** 全程不触发压缩，行为与无压缩机制时一致
- **WHEN** 辅助 LLM 返回的摘要中某字段为空（如无文件修改），**THE SYSTEM SHALL** 保留该字段并以 `(无)` 填充，不省略字段
- **WHEN** 同一 session 生命周期内发生多次压缩，**THE SYSTEM SHALL** 每次均创建新的子 session（形成 session 链：`s1 → s2 → s3`），不覆盖已有 session

## 6. 任务拆解

1. **Token 估算工具**：在消息列表上实现 `estimate_tokens(messages) -> int`，基于字符数/tiktoken 的近似算法。独立可测——给定固定消息列表，返回值在合理范围内
2. **Layer 1：工具输出裁剪器**：实现 `truncate_old_tool_outputs(messages, max_rounds) -> list`。对超过 N 轮的 tool 消息替换 content。单测覆盖：边界轮次的消息不被裁剪
3. **Layer 2：边界查找与切分**：实现 `find_boundaries(messages, head_rounds, tail_tokens) -> (head, middle, tail)`，包含 `_align_to_assistant_boundary`。单测覆盖：(a) 孤儿 tool_calls 场景 (b) 中间段为空场景 (c) 头尾重叠场景
4. **Layer 3：LLM 摘要生成器**：实现 `summarize_middle(middle_messages, model_config) -> dict`，调用辅助 LLM，解析为结构化摘要字典。单测覆盖：(a) 正常返回 (b) 超时降级 (c) 格式解析失败降级 (d) 空字段补齐
5. **压缩编排器**：实现 `compress(messages, config) -> (new_messages, CompressionResult)`，串联三层，包含 `CompressionStuckError` 逻辑。单测覆盖：(a) Layer 1 即达标则跳过后续 (b) 不足 90% 降幅抛异常 (c) 中间段为空 (d) 异常回退
6. **TaskState 数据结构 + 渲染**：实现 `TaskState` dataclass（goal / todos）与 `render()` 方法（输出 `# Task State (live, never compressed)` + 结构化 TODO）。同时实现 `task_set_goal` / `todo_write` / `todo_update` 三个工具的注册。单测覆盖 render 输出格式与 todo 状态标记
7. **Session 分裂与存储层**：`StorageBackend` 和 `SQLiteBackend` 的 `create_session` 新增可选参数 `parent_session_id`；数据库 schema 新增列。单测覆盖：(a) 新建无 parent 的 session (b) 新建有 parent 的 session (c) 查询 session 链
8. **System prompt 重建**：修改 `run_conversation()` 中的 prompt 缓存逻辑——压缩后调用 `build_system_prompt()` 重建，并将 TaskState 渲染拼入。单测覆盖：压缩后 prompt 被重新计算而非复用缓存
9. **Preflight 压缩**：在 `run_conversation()` 主循环前插入 preflight 检查（80% 阈值），复用压缩编排器。单测覆盖：(a) 未超阈值不触发 (b) 超阈值触发并成功 (c) 头部过大抛异常
10. **主循环集成**：将压缩触发逻辑嵌入 `run_conversation()` 主循环（每轮 API 调用前检查 token 数），串联所有组件。集成测试：用 50 轮工具调用模拟验证 (a) 压缩被触发 (b) 任务完成 (c) 无重复读文件 (d) session 链完整
11. **回归验收**：逐条验证第 5 节「行为与验收」中全部正常路径、异常路径、边界条件，确保无遗漏

<!-- SPEC_STATUS: reviewed — 结论：⚠️ 3 阻塞已修，5 建议 + 8 测试缺口待后续 -->

---

## 7. 实现计划

### 步骤 1：新增压缩相关配置项
- **改动**：`src/dezhu_agent/config.py`
- **做什么**：新增 6 个环境变量驱动的配置常量：
  - `COMPRESSION_ENABLED`（默认 `true`）
  - `COMPRESSION_TRIGGER_RATIO`（默认 `0.7`，即窗口 70% 触发）
  - `COMPRESSION_PREFLIGHT_RATIO`（默认 `0.8`）
  - `COMPRESSION_AUX_MODEL`（默认 `"deepseek-v4-flash"`）
  - `COMPRESSION_AUX_API_KEY`（默认复用 `OPENAI_API_KEY`）
  - `COMPRESSION_AUX_BASE_URL`（默认复用 `OPENAI_BASE_URL`）
- **验证**：`python -c "from dezhu_agent.config import COMPRESSION_ENABLED; print(COMPRESSION_ENABLED)"`
- **依赖**：无
- **风险**：低 — 纯新增，不碰现有逻辑
- **并行**：可与步骤 2、3、8、9 并行

### 步骤 2：定义 CompressionStuckError 和数据类
- **改动**：新增 `src/dezhu_agent/compression.py`
- **做什么**：定义 `CompressionStuckError(RuntimeError)`、`CompressionResult` dataclass（字段：`before_tokens`, `after_tokens`, `layers_applied`, `new_session_id`）、`CompressionConfig` dataclass（聚合步骤 1 的所有配置）
- **验证**：`python -c "from dezhu_agent.compression import CompressionStuckError, CompressionResult; print('ok')"`
- **依赖**：无
- **风险**：低 — 纯数据定义
- **并行**：可与步骤 1、3、8、9 并行

### 步骤 3：实现 Token 估算工具
- **改动**：`src/dezhu_agent/compression.py`（追加）
- **做什么**：实现 `estimate_tokens(messages: list[dict]) -> int`。用字符数/4 的近似算法（与 tiktoken 误差在 ±20% 内即可——阈值有 10% 安全边际）。接受的消息格式与 `messages_to_api_messages` 一致（`list[dict]`，含 `role`/`content`/`tool_calls` 等）
- **验证**：`python -c "from dezhu_agent.compression import estimate_tokens; n = estimate_tokens([{'role':'user','content':'hello world'}]); assert n > 0; print(n)"`
- **依赖**：无
- **风险**：低 — 独立纯函数
- **并行**：可与步骤 1、2、8、9 并行

### 步骤 4：Layer 1 — 工具输出裁剪器
- **改动**：`src/dezhu_agent/compression.py`（追加）
- **做什么**：实现 `truncate_old_tool_outputs(messages: list[dict], max_rounds: int = 20) -> list[dict]`。遍历消息，对超过 `max_rounds` 轮之前的 `role == "tool"` 消息，将其 `content` 替换为 `[Old tool output cleared]`。保留消息结构（不删除），保留 `tool_call_id` 和 `name`
- **验证**：`python -m pytest tests/test_compression.py::test_layer1_truncate -x`（先写单测，再写实现）
- **依赖**：步骤 3（token 估算用于后续编排器的递进判断）
- **风险**：低 — 纯字符串替换，不涉及 API 调用
- **并行**：可与步骤 5、6 并行

### 步骤 5：Layer 2 — 边界查找与切分
- **改动**：`src/dezhu_agent/compression.py`（追加）
- **做什么**：实现 `find_boundaries(messages: list[dict], head_rounds: int = 3, tail_tokens: int = 20000) -> tuple[list, list, list]`。计算头部（系统消息 + 前 N 轮用户-助手交互）、尾部（最近 ~20K tokens）。实现 `_align_to_assistant_boundary(messages, index) -> int`——当切点落在 `role == "tool"` 时，吸附到下一个非 tool 消息。返回 `(head, middle, tail)`
- **验证**：`python -m pytest tests/test_compression.py::test_layer2_boundaries -x`
- **依赖**：步骤 3（token 估算）
- **风险**：中 — 边界对齐逻辑涉及消息流合法性，需要丰富的单测覆盖孤儿 tool_calls、空 middle、头尾重叠
- **并行**：可与步骤 4、6 并行

### 步骤 6：Layer 3 — LLM 摘要生成器
- **改动**：`src/dezhu_agent/compression.py`（追加）
- **做什么**：实现 `summarize_middle(middle: list[dict], config: CompressionConfig) -> dict`。使用 `openai.OpenAI` 客户端（`api_key=config.aux_api_key`, `base_url=config.aux_base_url`, `timeout=5`）调用 `config.aux_model`。system prompt 固定要求输出 JSON：`{"goal":"...", "progress":"...", "key_decisions":"...", "files_modified":"...", "next_steps":"..."}`。超时/网络错误/解析失败时返回 `None`（调用方降级）
- **验证**：`python -m pytest tests/test_compression.py::test_layer3_summarize -x`（需要 mock LLM 响应）
- **依赖**：步骤 3（token 估算）
- **风险**：中 — 依赖外部 LLM，需要 mock 测试；timeout 5s 需验证不阻塞主循环
- **并行**：可与步骤 4、5 并行

### 步骤 7：压缩编排器
- **改动**：`src/dezhu_agent/compression.py`（追加）
- **做什么**：实现 `compress(messages: list[dict], config: CompressionConfig) -> CompressionResult`。串联 Layer 1 → Layer 2 → Layer 3，每层执行后调用 `estimate_tokens`——若已低于 `config.trigger_ratio * window_size` 则跳过后续层。若压缩后 token ≥ 90% 原值则抛 `CompressionStuckError`。异常时回退到原始 messages
- **验证**：`python -m pytest tests/test_compression.py::test_orchestrator -x`
- **依赖**：步骤 2、4、5、6
- **风险**：中 — 编排逻辑的异常回退需要仔细测试，确保不丢数据
- **并行**：不可并行（依赖 Layer 1/2/3 全部完成）

### 步骤 8：TaskState 数据结构 + 工具注册
- **改动**：`src/dezhu_agent/compression.py`（追加 TaskState）、新增 `src/dezhu_agent/tools/task_state.py`
- **做什么**：
  - 在 `compression.py` 中定义 `TaskState` dataclass（`goal: str`, `todos: list[dict]`）和 `render() -> str` 方法——输出 `# Task State (live, never compressed)` + `## Goal` + `## TODO` 列表（带 `[x]`/`[~]`/`[ ]` 标记）
  - 在 `tools/task_state.py` 中用 `@tool` 装饰器实现 `task_set_goal`、`todo_write`、`todo_update` 三个工具，操作模块级 `TaskState` 单例
- **验证**：`python -m pytest tests/test_compression.py::test_task_state -x` + `python -c "from dezhu_agent.tools.task_state import task_set_goal, todo_write; ..."`
- **依赖**：无
- **风险**：低 — 纯 Python 对象 + 标准工具注册模式
- **并行**：可与步骤 1、2、3、9 并行

### 步骤 9：Session 分裂与存储层扩展
- **改动**：`src/dezhu_agent/storage.py`
- **做什么**：
  - `_SCHEMA_SQL` 中 `sessions` 表新增 `parent_session_id TEXT` 列（迁移策略：`ALTER TABLE sessions ADD COLUMN parent_session_id TEXT`）
  - `StorageBackend.create_session` 签名改为 `create_session(self, parent_session_id: str = "") -> str`
  - `SQLiteBackend.create_session` 实现更新，插入 `parent_session_id`（空字符串按 `NULL` 处理）
  - 新增 `list_session_chain(self, session_id: str) -> list[dict]` ——沿 `parent_session_id` 递归查询 session 链
- **验证**：`python -m pytest tests/test_storage.py -x -k "session"`（扩展现有测试）
- **依赖**：无
- **风险**：中 — schema 变更涉及向后兼容，`ALTER TABLE` 需用 try/except 处理列已存在的情况；`create_session` 签名变更需确保所有调用方兼容（当前仅有 `loop.py:48` 和 `__main__.py` 调用）
- **并行**：可与步骤 1、2、3、8 并行

### 步骤 10：System prompt 重建 + TaskState 拼接
- **改动**：`src/dezhu_agent/loop.py`（局部修改）、`src/dezhu_agent/prompt.py`
- **做什么**：
  - 在 `prompt.py` 的 `build_system_prompt()` 末尾拼接 `task_state.render()` 输出
  - 在 `loop.py` 中，压缩完成后调用 `build_system_prompt(tools)` 重建 system prompt（不再从 storage 缓存加载），并重新持久化
- **验证**：`python -m pytest tests/test_loop.py -x -k "prompt"` + 手动验证 system prompt 末尾能看到 TaskState 块
- **依赖**：步骤 8
- **风险**：中 — system prompt 缓存逻辑是 DeepSeek disk cache 的关键依赖，需确保重建后仍保持缓存友好（prompt 前缀不变，仅末尾 TaskState 块可变）
- **并行**：不可并行（依赖步骤 8）

### 步骤 11：Preflight 压缩
- **改动**：`src/dezhu_agent/loop.py`（局部修改）
- **做什么**：在 `run_conversation()` 主循环 `while` 之前，调用 `estimate_tokens` 检查消息列表。若 ≥ `window_size * PREFLIGHT_RATIO`，调用 `compress(messages, preflight_config)`。preflight 压缩不创建新 session，直接在当前 session 上替换 messages
- **验证**：`python -m pytest tests/test_loop.py -x -k "preflight"`（mock 一个超长 history）
- **依赖**：步骤 7、9、10
- **风险**：中 — preflight 在主循环前执行，失败需要优雅退出，不能静默继续
- **并行**：不可并行（依赖多个前置步骤）

### 步骤 12：主循环压缩集成
- **改动**：`src/dezhu_agent/loop.py`（局部修改）
- **做什么**：在主循环 `while` 体内、每轮 API 调用前，调用 `estimate_tokens(api_messages)`。若 ≥ `window_size * TRIGGER_RATIO`，则：
  1. 调用 `compress(messages, config)` → 获得 `CompressionResult`
  2. 创建新 session（`parent_session_id = 旧 session_id`）
  3. 持久化压缩后的 messages 到新 session
  4. 重建 system prompt
  5. 更新 `session_id` 为新 session
  6. 捕获 `CompressionStuckError` → 向用户输出提示，退出循环
- **验证**：`python -m pytest tests/test_loop.py -x -k "compression_integration"`（用 mock LLM + mock 工具模拟 50 轮循环，验证压缩触发、session 链、CompressionStuckError 退出）
- **依赖**：步骤 7、9、10、11
- **风险**：高 — 改动核心循环路径，需要保留现有 E1（fail-fast）、E2（stop）、E3（tool_calls）、E4（budget exhausted）四条路径的语义不变
- **并行**：不可并行（最后集成步骤）

### 步骤 13：端到端回归验收
- **改动**：`tests/test_compression.py`（扩展）、`tests/test_loop.py`（扩展）
- **做什么**：逐条对照 spec 第 5 节「行为与验收」的所有 18 条验收标准（正常 10 + 异常 4 + 边界 4），确保每条都有对应的测试用例。重点：50 轮工具调用集成测试，验证 (a) 任务完成率 (b) 峰值 token (c) 无重复读文件
- **验证**：`python -m pytest tests/ -x -v`
- **依赖**：步骤 12
- **风险**：低 — 测试代码，不影响生产路径
- **并行**：不可并行（依赖所有实现步骤）

---

## 8. Review 记录

### 2026-07-01 · 首次 review（spec_review）

**结论**：⚠️ 修后合并 — 3 阻塞 + 5 建议 + 8 测试缺口

#### 阻塞项（已修复）

- ✅ **session_id 泄露**：`run_conversation()` 返回值从 `tuple[str, list[Message]]` 改为 `tuple[str, list[Message], str | None]`，调用方 `__main__.py` 同步更新。压缩分裂后的新 session_id 正确传递回调用方。
- ✅ **history_start_len 过期**：preflight 压缩和主循环压缩后均执行 `history_start_len = 0`，`_persist()` 切片基准正确。
- ✅ **TaskState 冻结**：`system_prompt` 变量保持 base（不含 TaskState），`sys_msg` 每轮通过 `get_task_state().render()` 动态拼接。模型调用 `todo_update` 后下一轮立即可见。

#### 建议项（待后续处理）

- [ ] 递归压缩防护：摘要消息加 `_compressed` 标记，压缩检查时跳过
- [ ] 10 秒总超时：`compress()` 入口加 `time.monotonic()` 守卫
- [ ] 未预期异常用户告警：`degraded=True` 时 `print(..., file=sys.stderr)`
- [ ] 无 storage 时压缩行为：明确写入 spec 或支持无 storage 压缩
- [ ] 测试覆盖缺口：8 条验收无测试、5 条部分覆盖（详见 QA 审查）

<!-- SPEC_STATUS: reviewed — 结论：⚠️ 3 阻塞已修，5 建议 + 8 测试缺口待后续 -->
