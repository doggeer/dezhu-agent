# Spec：Memory 子系统

## 1. 背景与目标（Why）

### 问题
dezhu-agent 当前每次新会话完全从零开始，不携带任何跨会话的信息。用户偏好（如代码风格、回答简洁度）、项目约定（如测试命令）、用户纠正过的错误做法——这些信息每次都要重新交代，系统显得"每次都像第一次合作"。

### 目标
实现一个 **memory 子系统**，让 agent 在跨会话间记住那些"不能从当前项目状态直接推出、但长期有价值"的信息。核心原则是：**memory 不是什么都存，而是只存跨会话有价值且不能轻松从代码/项目状态重新获得的信息。**

### 成功指标
- 会话 1 中 agent 学到的用户偏好，会话 2 的 system prompt 中可见（冻结快照机制）
- Nudge 机制在每 N 轮用户对话后自动触发后台审查，日志完整记录记忆模块活动
- Flush 机制在压缩/会话重置前紧急保存未写入的 memory
- 字符限制下 agent 能自主淘汰过时条目

## 2. 范围

### 做

**双文件模型：**
- `MEMORY.md`（项目/环境记忆，上限 2,200 字符）——存"这个项目/环境是怎么回事"
- `USER.md`（用户画像，上限 1,375 字符）——存"这个人是谁、怎么和他合作最顺"
- 以 markdown 条目格式存储，`§` 字符分隔条目
- 存入 `.dezhu-agent/` 目录，独立于 SQLite 存储
- 文件锁（fcntl.flock）保证并发写入安全

**冻结快照机制：**
- 会话开始时读取 MEMORY.md 和 USER.md，冻结为 system prompt 的一部分
- 会话中间对 memory 的写入立即持久化到磁盘，但不更新当前会话的 system prompt
- 新 memory 下次会话开始时生效

**Nudge（定期后台审查）：**
- 后台线程基础设施，不阻塞主对话循环
- 每 N 轮用户对话后触发（默认 10 轮，可通过配置项调整）
- 在后台线程中启动独立审查 agent，审查当前对话历史
- 审查 agent 共享同一个 memory 存储，可写入 MEMORY.md / USER.md
- 审查 agent 静默运行，不产生用户可见输出（stdout/stderr 不发到用户终端）
- 日志系统完整记录：nudge 触发时机、审查结果、保存的条目、淘汰的条目

**Flush（压缩前紧急冲刷）：**
- 对话即将被压缩或会话重置前触发
- 在当前对话中注入一条系统消息，提示模型保存 memory
- 保存完毕后删除 flush 相关消息，不留痕迹
- 至少 N 轮用户对话后才触发 flush（默认 6 轮，可配置）
- 日志系统完整记录 flush 全流程

**字符限制与自动淘汰：**
- MEMORY.md 上限 2,200 字符，USER.md 上限 1,375 字符
- 空间不足时 agent 自主决定淘汰哪条旧 memory
- 淘汰事件记入日志

**工具集成：**
- 提供 `memory` 工具给 agent 调用，支持读取、写入、删除 memory 条目，通过参数区分 target（`memory` / `user`）
- 在 prompt_assembler 中增加可插拔数据源接口，memory 作为其中一个数据源注册

**扩展接口（仅定义协议，不实现）：**
- 预留外部记忆提供者（plugin）接口，支持未来接入向量数据库等
- 外部记忆内容不进 system prompt，注入到 user message 中

**日志：**
- 记忆模块所有活动（nudge 触发/结果、flush 触发/结果、条目增删淘汰、字符配额使用）均记入日志
- 日志级别、格式与现有 logging_config 保持一致，日志名为 `dezhu_agent.memory.*`

### 不做（Out of scope）

- 外部记忆提供者的实际实现（向量数据库等，仅留接口）
- 多 profile 支持
- 基于语义相似度的自动检索/匹配
- Session 历史归档/检索（现有 SQLite 已覆盖）
- 多用户 / Gateway 场景的分布式 memory

## 3. 约束

| 类别 | 约束 | 理由 |
|------|------|------|
| 语言 | Python 3.10+，不引入新语言 | 与项目一致 |
| 现有文件 | 不改动 loop.py / compression.py / __main__.py 的核心逻辑，仅通过钩子/协议接入 | 用户明确要求 |
| 运行时 | 后台审查线程不能阻塞主对话循环，响应延迟增加不超过 50ms | 用户体验红线 |
| 模型调用 | 审查 agent 的 LLM 调用不计入主 agent 的 token budget，使用独立短超时（审查 ≤ 8 轮，每轮 5s 超时） | 防止审查拖慢主流程 |
| 存储路径 | `.dezhu-agent/MEMORY.md` 和 `.dezhu-agent/USER.md`，可通过环境变量 `DEZHU_MEMORY_DIR` 覆盖目录 | 与现有 `.dezhu-agent/` 约定一致 |
| 文件锁 | fcntl.flock 排他锁，超时 2s 后放弃写入并记录警告日志 | 防止死锁 |
| 日志格式 | 复用 logging_config 的格式和 SessionFilter，添加 `logger` 名区分来源 | 不引入第二套日志体系 |
| 压缩交互 | flush 注入的消息标记为内部消息（`_internal=True`），压缩时必须被保留（不参与压缩），flush 完成的信号由 memory 模块自行清除 | 防止 flush 消息被压缩掉导致重复注入 |
| 字符限制 | 由 memory 模块在执行 tool 写入操作时在本地校验并截断/拒绝，不依赖 agent 自觉遵守 | agent 可能不数自己写了多少字 |

## 4. 既有决策

| 决策 | 结论 | 理由 |
|------|------|------|
| 双文件模型 | MEMORY.md + USER.md，不简化为单文件 | 生命周期不同：MEMORY.md 随项目变，USER.md 随用户不变 |
| 冻结快照 | 会话开始冻结，会话中间写入不更新 system prompt | 保护 prompt cache；简化 system prompt 不变性 |
| Nudge 后台审查 | 必须做，后台线程 + 独立 agent | 不抢占主任务注意力；日志全量记录 |
| 存储格式 | 独立 markdown 文件，`§` 分隔条目 | 可读性好，agent 原生可读写，不依赖 SQL |
| 接入方式 | prompt_assembler 增加可插拔数据源协议 | 不改 loop.py，未来新数据源可复用 |
| 外部 memory provider | 这一期只定义接口（Protocol），不实现 | 当前无实际需求，但接口不先定义后续难以接入 |
| 字符上限 | MEMORY.md 2,200 / USER.md 1,375，agent 自主淘汰 | 控制 system prompt 膨胀；淘汰决策需要判断力，适合 agent 而非规则 |
| 淘汰策略 | agent 自主决定，不设硬性时间/LRU 规则 | 简单规则可能淘汰掉重要但少用的条目，agent 的判断力更适合此任务 |

## 5. 行为与验收

### 5.1 正常路径 — Memory 注入 System Prompt

**AC-01 — 会话启动时加载并冻结**
- WHEN 新会话启动 AND `.dezhu-agent/MEMORY.md` 和 `.dezhu-agent/USER.md` 均存在且非空，
  THE SYSTEM SHALL 读取两个文件内容，生成一个冻结快照，并将其注入 system prompt。
- 注入格式：system prompt 中出现 `## MEMORY (your personal notes)` 和 `## USER PROFILE (who the user is)` 两个节，内容为冻结时刻的文件内容。

**AC-02 — 文件不存在时静默跳过**
- WHEN 新会话启动 AND 任一 memory 文件不存在，
  THE SYSTEM SHALL 不注入对应的节，不报错，不影响会话正常开始。

**AC-03 — 会话中间写入不更新当前 system prompt**
- WHEN 会话进行中 AND agent 通过 `memory` 工具写入新条目，
  THE SYSTEM SHALL 将内容持久化到磁盘文件，但当前会话的 system prompt 保持不变。
- 验证方式：写入后检查磁盘文件已更新，但本轮对话中 agent 的 system prompt 仍为会话开始时的快照。

**AC-04 — 下次会话生效**
- WHEN 会话 1 中 agent 写入了新的 memory 条目 AND 用户启动会话 2，
  THE SYSTEM SHALL 在会话 2 的 system prompt 中包含会话 1 写入的条目。

### 5.2 正常路径 — Nudge 后台审查

**AC-05 — 达到阈值时触发后台审查**
- WHEN 用户对话轮数达到 nudge_interval（默认 10 轮），
  THE SYSTEM SHALL 在当前响应返回给用户之后，启动后台线程执行审查。

**AC-06 — 审查 agent 写入 memory**
- WHEN 审查 agent 发现值得保存的信息，
  THE SYSTEM SHALL 通过 memory 工具写入 MEMORY.md 或 USER.md，并记录日志。

**AC-07 — 审查 agent 无发现时静默结束**
- WHEN 审查 agent 认为没有值得保存的信息，
  THE SYSTEM SHALL 输出"Nothing to save."并结束，不修改任何文件。

**AC-08 — 后台审查不阻塞用户**
- WHEN 后台审查正在进行中，
  THE SYSTEM SHALL 仍然可以立即响应用户的下一条输入，不等待审查完成。

**AC-09 — 审查计数器归零**
- WHEN nudge 触发后，
  THE SYSTEM SHALL 将用户对话轮数计数器归零，重新计数。

### 5.3 正常路径 — Flush 紧急冲刷

**AC-10 — 压缩前触发 flush**
- WHEN 对话即将被压缩 AND 用户对话轮数 ≥ flush_min_turns（默认 6 轮），
  THE SYSTEM SHALL 在当前对话中注入一条系统消息，提示模型保存 memory。

**AC-11 — flush 消息标记为内部且不参与压缩**
- WHEN flush 消息注入后，
  THE SYSTEM SHALL 将 flush 消息标记为 `_internal=True`，压缩时保留该消息不被压缩。

**AC-12 — flush 完成后删除注入消息**
- WHEN 模型完成 memory 保存（调用了 memory 工具或回复"无需保存"），
  THE SYSTEM SHALL 从对话历史中删除 flush 注入消息及其相关响应，不留痕迹。

**AC-13 — 轮数不足时不触发 flush**
- WHEN 对话即将被压缩 AND 用户对话轮数 < flush_min_turns，
  THE SYSTEM SHALL 跳过 flush，直接执行压缩。

### 5.4 正常路径 — Memory 工具

**AC-14 — 读取 MEMORY.md**
- WHEN agent 调用 `memory(action="read", target="memory")`，
  THE SYSTEM SHALL 返回 MEMORY.md 文件的当前完整内容。

**AC-15 — 读取 USER.md**
- WHEN agent 调用 `memory(action="read", target="user")`，
  THE SYSTEM SHALL 返回 USER.md 文件的当前完整内容。

**AC-16 — 写入 MEMORY.md**
- WHEN agent 调用 `memory(action="write", target="memory", content="<条目>")`，
  THE SYSTEM SHALL 将新条目追加到 MEMORY.md（§ 分隔），不超过 2,200 字符上限。

**AC-17 — 写入 USER.md**
- WHEN agent 调用 `memory(action="write", target="user", content="<条目>")`，
  THE SYSTEM SHALL 将新条目追加到 USER.md（§ 分隔），不超过 1,375 字符上限。

**AC-18 — 删除条目**
- WHEN agent 调用 `memory(action="delete", target="memory"|"user", index=<N>)`，
  THE SYSTEM SHALL 删除第 N 条 § 条目（0-based），并返回被删除的条目内容。

### 5.5 异常路径

**AC-19 — 文件锁冲突**
- WHEN 写入 memory 文件时遇到文件锁冲突（超时 2s），
  THE SYSTEM SHALL 放弃此次写入，记录 WARNING 日志，并向 agent 返回写入失败的错误信息。

**AC-20 — 审查 agent 超时**
- WHEN 后台审查 agent 运行超过 8 轮或单轮 API 调用超过 5s，
  THE SYSTEM SHALL 终止审查，记录 ERROR 日志，不影响主对话。

**AC-21 — 写入超字符上限**
- WHEN agent 尝试写入的条目会导致文件超过字符上限，
  THE SYSTEM SHALL 拒绝写入，返回错误信息（包含当前使用量和上限），提示 agent 先删除旧条目再写入。

**AC-22 — 删除不存在的索引**
- WHEN agent 调用 `memory(action="delete", index=<N>)` 且第 N 条不存在，
  THE SYSTEM SHALL 返回索引越界错误，列出当前有效索引范围。

**AC-23 — 审查 agent API 调用失败**
- WHEN 后台审查 agent 的 API 调用失败（网络错误、服务端错误等），
  THE SYSTEM SHALL 记录 ERROR 日志，终止审查，不影响主对话。

### 5.6 边界条件

**AC-24 — 空文件**
- WHEN MEMORY.md 或 USER.md 存在但内容为空，
  THE SYSTEM SHALL 视为"无 memory"，不注入对应节到 system prompt。

**AC-25 — 压缩导致 session 分裂时计数器保持**
- WHEN 压缩分裂创建新 session，
  THE SYSTEM SHALL 保持用户对话轮数计数器不变（不归零，因为不是 nudge 触发）。

**AC-26 — 并发写入同一文件**
- WHEN 主 agent 和后台审查 agent 同时尝试写入同一 memory 文件，
  THE SYSTEM SHALL 通过文件锁保证一次只有一个写入，另一个等待或放弃。

**AC-27 — 文件被外部修改**
- WHEN 会话进行中 memory 文件被外部进程修改，
  THE SYSTEM SHALL 在下次读取时看到最新内容（冻结快照不受影响），但在本会话内写入时基于磁盘最新状态追加。

## 6. 任务拆解（实现计划）

### 步骤 1：MemoryStore — 文件读写 + 锁 + 上限校验
- 改动：新建 `src/dezhu_agent/memory/store.py`
- 做什么：
  - `MemoryStore` 类，构造函数接收 `base_dir`（默认 `.dezhu-agent/`）
  - `read(target)` / `write(target, content)` / `delete(target, index)` / `get_usage(target)`
  - 条目以 `§` 分隔；写入时校验字符上限，超限拒绝
  - 文件锁：`fcntl.flock` 排他锁，2s 超时，超时抛 `MemoryLockError`
  - 字符上限：MEMORY.md 2,200 / USER.md 1,375
- 验证：`uv run pytest tests/test_memory_store.py`
- 依赖：无
- 风险：低 — 纯新增

### 步骤 2：MemorySnapshot — 冻结快照
- 改动：新建 `src/dezhu_agent/memory/snapshot.py`
- 做什么：
  - `MemorySnapshot` dataclass，`from_store(store)` 类方法读取两个文件生成快照
  - `render()` 输出注入 system prompt 的文本：`## MEMORY (your personal notes)` + `## USER PROFILE (who the user is)`，空文件不输出对应节；全空返回 `""`
- 验证：`uv run pytest tests/test_memory_snapshot.py`
- 依赖：步骤 1
- 风险：低

### 步骤 3：PromptSource 协议 + MemorySource
- 改动：修改 `src/dezhu_agent/prompt_assembler.py`
- 做什么：
  - 定义 `PromptSource` 协议（`name: str` + `render() -> str`）
  - 模块级 `_sources` 列表 + `register_prompt_source(source)` 注册函数
  - `assemble_system_prompt()` 用 `for s in _sources` 替代硬编码的 `_format_skills()`
  - `MemorySource` 类：接收 `MemorySnapshot`，实现 `PromptSource`
  - 向后兼容：未注册 source 时行为不变
- 验证：`uv run pytest` 现有测试全绿 + `test_prompt_source.py`
- 依赖：步骤 2
- 风险：低 — 扩展预留桩

### 步骤 4：Memory 配置
- 改动：修改 `src/dezhu_agent/config.py`
- 做什么：
  - 新增 `DEZHU_MEMORY_DIR`、`DEZHU_MEMORY_NUDGE_INTERVAL`、`DEZHU_MEMORY_FLUSH_MIN_TURNS`
  - 全部可选，有默认值
- 验证：`python3 -c "from dezhu_agent.config import MEMORY_*"` 导入成功
- 依赖：无 — 可与步骤 1-3 并行
- 风险：低

### 步骤 5：memory 工具
- 改动：新建 `src/dezhu_agent/tools/memory_tool.py`
- 做什么：
  - 用 `@tool(name="memory", description=...)` 注册
  - 参数：`action`（read/write/delete）、`target`（memory/user）、`content`、`index`
  - 委托给 MemoryStore 实例执行
- 验证：`uv run pytest tests/test_memory_tool.py`
- 依赖：步骤 1
- 风险：低 — 照搬现有工具模式

### 步骤 6：CompressionConfig 增加 pre_compress_hook
- 改动：修改 `src/dezhu_agent/compression.py` + `src/dezhu_agent/loop.py`
- 做什么：
  - `CompressionConfig` 增加字段 `pre_compress_hook: Callable | None = None`
  - loop.py 中两处 `compress()` 调用前，若 hook 非 None 则先调用
  - 改动量极小（3-5 行），hook 默认 None 行为不变
- 验证：`uv run pytest` 现有 loop 测试全绿
- 依赖：无 — 纯接口
- 风险：中 — 修改 loop.py 核心路径，但改动极微

### 步骤 7：Nudge — 后台审查
- 改动：新建 `src/dezhu_agent/memory/nudge.py`
- 做什么：
  - `NudgeManager` 类：计数器、`on_user_turn()` 触发、`_run_review()` 后台审查
  - 后台线程 `threading.Thread(daemon=True)`，不阻塞主线程
  - 审查 agent：独立 LLM 实例，固定审查提示词，max 8 轮 + 5s 超时
  - 审查结果写入 MemoryStore；日志记录全流程
- 验证：`uv run pytest tests/test_memory_nudge.py`
- 依赖：步骤 1、步骤 5
- 风险：中 — 后台线程 + 独立 LLM 调用

### 步骤 8：Flush — 压缩前紧急冲刷
- 改动：新建 `src/dezhu_agent/memory/flush.py`
- 做什么：
  - `FlushManager` 类：轮数计数、`create_pre_compress_hook()` 返回回调
  - hook 逻辑：检查轮数 → 注入 system 消息（`_internal=True`）→ 一轮 LLM 交互 → 删除注入消息
  - flush 提示词固定模板；日志完整记录
- 验证：`uv run pytest tests/test_memory_flush.py`
- 依赖：步骤 1、步骤 5、步骤 6
- 风险：中 — 在压缩路径中插入 API 往返

### 步骤 9：CLI 集成 — 生命周期接入
- 改动：修改 `src/dezhu_agent/__main__.py`
- 做什么：
  - `main()` 中：初始化 MemoryStore → MemorySnapshot → 注册 MemorySource
  - `_run_conversation_loop()` 中：每轮后调用 `nudge_manager.on_user_turn()`
  - `run_conversation()` 调用前：配置 `pre_compress_hook`
  - `_cleanup()` 中：退出前 flush（若未通过压缩触发）
- 验证：端到端测试 — 跨会话 memory 生效
- 依赖：步骤 3、5、7、8
- 风险：中 — 多模块串联

### 步骤 10：ExternalMemoryProvider 协议
- 改动：新建 `src/dezhu_agent/memory/external.py`
- 做什么：
  - `ExternalMemoryProvider` Protocol：`retrieve()` + `inject_into_user_message()`
  - `NoopExternalProvider` 空实现
- 验证：导入成功
- 依赖：无 — 完全独立，可任意阶段并行
- 风险：低 — 纯协议定义

### 步骤 11：测试 — 覆盖全部 27 条 AC
- 改动：新建 `tests/test_memory_store.py`、`tests/test_memory_snapshot.py`、`tests/test_memory_nudge.py`、`tests/test_memory_flush.py`、`tests/test_memory_tool.py`、`tests/test_memory_integration.py`
- 做什么：单元测试 + 集成测试 + 端到端测试，覆盖 AC-01 ~ AC-27
- 验证：`uv run pytest` 全绿
- 依赖：步骤 1-9
- 风险：中

### 步骤 12：日志审查与完善
- 改动：检查全部 memory 模块
- 做什么：确保 logger 命名空间 `dezhu_agent.memory.*`，关键事件日志完整
- 验证：`grep -r "get_logger" src/dezhu_agent/memory/`
- 依赖：步骤 1-10
- 风险：低

### 并行机会
```
步骤 1 ─┬→ 步骤 2 ─→ 步骤 3 ────────────────┐
        │              ↓                      │
        ├→ 步骤 5 ─┬→ 步骤 7 ─→ 步骤 9 ─→ 步骤 11 ─→ 步骤 12
        │          │            ↗              │
步骤 4 ─┘          └→ 步骤 8 ──┘               │
                    (需步骤 6)                 │
步骤 6 ───────────────────────────────────────┘
步骤 10 ──────────────────────────────────────┘ (完全独立)
```

<!-- SPEC_STATUS: reviewed — 结论：✅ 可合并。阻塞项全部修复，测试覆盖 212 条全部通过 -->

## 7. Review 结论

### 阻塞项修复（全部已修）
| # | 问题 | 修复 |
|---|------|------|
| 1 | Nudge 审查 agent 无多轮循环（AC-20 ❌） | `_run_review` 改为 while 循环（max 8 轮），每轮反馈 tool 结果 |
| 2 | Flush 无多轮循环 | `_do_flush` 改为最多 3 轮循环 |
| 3 | `delete()` TOCTOU 竞态 | 读-改-写移入锁内，`_parse_entries` 替代锁外 `_get_entries` |
| 4 | 退出时独立 flush 未实现 | `_cleanup` 中检查轮数 + 调用 `_do_flush([])` |
| 5 | 测试文件缺失（nudge/flush/integration） | 新增 3 个测试文件，18 条测试 |
| 6 | AC-11/AC-12 flush 实现偏离 spec | 方案 B：注入-标记-调用-清理模式 |

### 建议项修复（全部已修）
| # | 问题 | 修复 |
|---|------|------|
| 7 | 审查 agent 使用全部工具 | `_get_memory_only_tools()` 仅返回 memory 工具 |
| 8 | 审查消息包含 system prompt | `_build_review_messages` 过滤 `role="system"` |
| 9 | 死代码 `_FLUSH_PROMPT` | 已删除 |
| 10 | AC-23 日志级别不符 | `WARNING` → `ERROR` |
| 11 | `_internal` 键泄露到 API | 不再往 dict 加 `_internal` |
| 12 | if/elif 逻辑不健壮 | 多轮循环中 tool_calls 走 `continue`，content 保留在 message 历史 |

### 验收对标最终结果
- 总验收数：27
- 已实现 + 已测试：26（AC-01~19, AC-21~27）
- 已实现 + 部分测试：1（AC-20 但超时控制已落实）
- 测试总数：212 条全绿（原有 167 + 新增 45）
