# Spec：动态 System Prompt 组装

## 1. 背景与目标（Why）

当前 `src/dezhu_agent/prompt.py` 中 system prompt 是一个写死的字符串常量 `SYSTEM_PROMPT_TEMPLATE`。但真实 agent 运行时，prompt 需要聚合来自多个独立来源的信息：

- **人设**（`~/.hermes/SOUL.md`）：agent 的身份和行为风格
- **项目规则**（`AGENTS.md`）：当前项目的技术栈、命令、约定
- **启用的技能清单**（`.reasonix/skills/`）：后续实现，本迭代预留拼入点

> 注意：工具列表不写入 system prompt 文本。工具通过 OpenAI API 的 `tools` 参数单独传递（`build_tools_for_api()`），无需在 prompt 文本中重复列出。

这些信息各自独立演变。硬编码在一起意味着每次调整人设、规则或工具列表都要改 `prompt.py` 代码。目标是将 system prompt 改为**启动时从多个来源组装、会话内保持稳定**的机制。

**成功指标**：
- 修改 `~/.hermes/SOUL.md` 或项目 `AGENTS.md` 后，重启 agent 即生效，无需改代码
- Debug 模式下能输出每个来源对最终 prompt 的贡献片段（模块级可观测性）
- 同一 session 内 system prompt 不变，Anthropic prompt cache 命中率不因本机制下降
- AGENTS.md 内容过长时自动截断，避免挤占上下文窗口

## 2. 范围

### 做
- 启动/新会话时从 `~/.hermes/SOUL.md`、`<project>/AGENTS.md`、已启用的技能清单配置文件组装 system prompt
- system prompt 写入 session 记录（共用 `.dezhu-agent/dezhu-agent.db`），会话内复用，保证稳定性
- 现有 `SYSTEM_PROMPT_TEMPLATE` 作为默认 fallback：SOUL.md 不存在时使用内置人设
- AGENTS.md 拼接在 SOUL.md 之后（顺序：人设 → 项目规则 → 技能预留）；AGENTS.md 过长时做长度管理和截断
- Debug 模式：通过环境变量 `DEZHU_DEBUG=1` 启用，在 stderr 输出各模块贡献的 prompt 片段
- 技能清单预留拼入点（本迭代不实现技能内容拼入，仅保留接口占位）
- `build_system_prompt()` 不再将工具列表格式化为文本拼入 prompt。工具定义仅通过 `build_tools_for_api()` 以 OpenAI tools 参数传递

### 不做（Out of scope）
- 热更新（运行中不重启 agent 就更换人设/规则）
- 项目级 `.hermes/SOUL.md` 覆盖全局人设（仅全局 `~/.hermes/SOUL.md`）
- 多租户（同一进程内为不同用户拼不同 prompt）
- Prompt 版本管理 / A/B 测试
- 模板引擎（Jinja2 等复杂条件/循环逻辑）
- 技能清单的实际内容拼入（技能系统本身是后续迭代）

## 3. 约束

- **Python 3.10+**，使用项目已有的 `uv` 管理依赖，不新增第三方依赖（文件读取用标准库 `pathlib`）
- **DB 共用**：system prompt 存储在现有 `sessions` 表中（新增 `system_prompt TEXT` 列），不新建表。SQLite schema 升级必须向后兼容已有数据库（`ALTER TABLE ... ADD COLUMN` 带默认值）
- **缓存稳定性**：同一 session 内 system prompt 字节级不变。如果首轮 API 调用能命中 prompt cache，后续轮次必须保持命中（不因 prompt 内容变化导致缓存失效）
- **文件不存在 = 不报错**：`~/.hermes/SOUL.md` 缺失时静默跳过对应来源，不阻塞 agent 启动
- **AGENTS.md 长度上限**：原始内容超过 2000 字符时截断至前 2000 字符，末尾追加 `…（已截断，完整内容见项目 AGENTS.md 文件）`
- **不能破坏现有行为**：`build_system_prompt()` 在不提供任何外部文件时，输出必须与当前硬编码的 `SYSTEM_PROMPT_TEMPLATE` 字面一致（向后兼容）
- **不能修改** `tools_config.yaml` 的现有格式约定和 `apply_config()` 逻辑。工具启用/禁用逻辑不受本次改动影响

## 4. 既有决策

- **全局 SOUL.md**：选定 `~/.hermes/SOUL.md` 作为人设文件路径。否决项目级覆盖（理由：人设是 agent 身份，跨项目统一，避免每个项目各维护一份人设副本）
- **AGENTS.md 直拼**：AGENTS.md 原文直接拼入 system prompt（在 SOUL.md 之后）。否决编译/精简版本（理由：AGENTS.md 本身就是面向模型的清晰指令，无需二次处理；长度由截断机制控制）
- **DB 而非内存**：system prompt 写入 session 记录而非仅内存变量。否决纯内存方案（理由：`--continue` 恢复会话时需要相同的 system prompt 保证缓存稳定）
- **Debug 走 stderr**：可观测性输出到 stderr。否决写到日志文件或 stdout（理由：stdout 被流式输出占用，stderr 已有工具执行日志先例；文件日志增加复杂度且本迭代不需要）
- **不引入 PromptBuilder 抽象类**：第一版直接实现，不在 `prompt.py` 中引入抽象层。否决过度工程化（理由：当前仅一种组装策略——本地文件 → 字符串拼接。等出现第二组装策略时再抽象，下次评估条件：需要从远程 URL / DB 动态拉取 prompt 片段）

## 5. 行为与验收

### 正常路径

- WHEN agent 启动（新会话、非 `--continue`），THE SYSTEM SHALL 从 `~/.hermes/SOUL.md`、项目根 `AGENTS.md`、已启用的技能清单配置中读取内容，组装为完整 system prompt，写入当前 session 的 `system_prompt` 字段。
- WHEN `~/.hermes/SOUL.md` 不存在，THE SYSTEM SHALL 使用现有硬编码的 `SYSTEM_PROMPT_TEMPLATE` 作为人设部分，其余来源正常拼接。
- 工具定义不写入 system prompt 文本。THE SYSTEM SHALL 仅通过 `build_tools_for_api()` 以 OpenAI API `tools` 参数传递工具定义。
- WHEN 环境变量 `DEZHU_DEBUG=1`，THE SYSTEM SHALL 在 stderr 输出每个来源的名称 + 贡献的 prompt 字节数 + 前 120 字符预览。
- WHEN agent 通过 `--continue` 恢复已有会话，THE SYSTEM SHALL 直接从 sessions 表读取已存储的 `system_prompt`，不重新组装（保证缓存一致性）。

### 异常路径

- WHEN `AGENTS.md` 文件不存在，THE SYSTEM SHALL 跳过项目规则来源，其余来源正常拼接，不报错。
- WHEN `~/.hermes/SOUL.md` 文件存在但内容为空，THE SYSTEM SHALL 视为"无自定义人设"，使用 `SYSTEM_PROMPT_TEMPLATE` 作为 fallback。
- WHEN `AGENTS.md` 原始内容超过 2000 字符，THE SYSTEM SHALL 截断至前 2000 字符并追加截断提示，不阻塞启动。

### 边界条件

- WHEN 组装后的 system prompt 包含特殊字符（`{` `}` `"` `\n` 等），THE SYSTEM SHALL 保持原样不转义、不损坏。
- WHEN SOUL.md 使用 UTF-8 BOM 编码，THE SYSTEM SHALL 正确处理，不将 BOM 字节拼入 prompt。
- WHEN SOUL.md 或 AGENTS.md 的换行符为 `\r\n`（Windows 风格），THE SYSTEM SHALL 保持原样，不自动转换。
- 现有 `build_system_prompt()` 在不提供工具列表（`tools=None`）时，仅输出人设 + 规则 + 技能预留部分。
- `build_system_prompt(tools=...)` 参数保留以兼容现有调用方，但 tools 参数仅用于决定是否启用技能清单的格式化（不拼入工具文本）。

## 6. 任务拆解（实现计划）

### 步骤 1：storage.py — sessions 表新增 system_prompt 列 + 读写方法

- **改动**：`src/dezhu_agent/storage.py`
- **做什么**：
  1. `_SCHEMA_SQL` 的 `sessions` 表中新增 `system_prompt TEXT NOT NULL DEFAULT ''`
  2. `_ensure_db()` 末尾追加迁移逻辑：执行 `ALTER TABLE sessions ADD COLUMN system_prompt TEXT NOT NULL DEFAULT ''`，捕获 `sqlite3.OperationalError`（列已存在）静默跳过
  3. 新增 `save_system_prompt(self, session_id: str, system_prompt: str) -> None` — `UPDATE sessions SET system_prompt = ? WHERE session_id = ?`
  4. 新增 `load_system_prompt(self, session_id: str) -> str` — `SELECT system_prompt FROM sessions WHERE session_id = ?`，查不到返回 `""`
  5. `StorageBackend` 抽象基类新增对应的 `@abstractmethod` 声明
- **验证**：`uv run pytest tests/test_storage.py -v` 现有测试全通过。追加测试：save 后 load 一致、新 DB 列存在、旧 DB（无列）自动迁移后列为空字符串
- **依赖**：无
- **风险**：低 — 纯新增列和读写方法，不碰现有查询逻辑
- **并行**：可与步骤 2 并行

### 步骤 2：prompt_assembler.py — 新建多来源组装模块

- **改动**：`src/dezhu_agent/prompt_assembler.py`（新文件）
- **做什么**：
  1. `assemble_system_prompt(tools: list[dict] | None = None) -> str`
  2. 组装顺序：`_load_soul()` → `_load_agents()` → `_format_skills(tools)` → 拼接
  3. `_load_soul()`：读 `~/.hermes/SOUL.md`，存在且非空→返回内容；否则返回 `SYSTEM_PROMPT_TEMPLATE`
  4. `_load_agents()`：读 `<PROJECT_ROOT>/AGENTS.md`，存在→截断至 2000 字符（超长追加提示）；否则返回 `""`
  5. `_format_skills(tools)`：本迭代返回 `""`；签名预留 `tools` 参数供后续技能清单格式化
  6. Debug：`DEZHU_DEBUG=1` 时 `sys.stderr.write` 每来源名称 + 字节数 + 前 120 字符
  7. BOM 处理：`encoding="utf-8-sig"` 自动剥离 UTF-8 BOM
- **验证**：`uv run python -c "from dezhu_agent.prompt_assembler import assemble_system_prompt; print(len(assemble_system_prompt()))"` 输出 >0
- **依赖**：无（仅依赖 `config.PROJECT_ROOT` 和 `prompt.SYSTEM_PROMPT_TEMPLATE`）
- **风险**：中 — 新建核心模块；独立性强，不碰现有代码
- **并行**：可与步骤 1 并行

### 步骤 3：prompt.py — build_system_prompt 委托给 assembler，移除工具文本格式化

- **改动**：`src/dezhu_agent/prompt.py`
- **做什么**：
  1. `build_system_prompt()` 内部改为调用 `prompt_assembler.assemble_system_prompt(tools)` 并返回
  2. **删除**现有 `if tools: prompt += "\n\n## 可用工具\n" ...` 格式化代码块
  3. 保留 `SYSTEM_PROMPT_TEMPLATE` 常量（assembler fallback 引用）
  4. 保留 `_tool_to_openai_tool()` 和 `build_tools_for_api()` 不变
  5. 添加 `from dezhu_agent.prompt_assembler import assemble_system_prompt`
- **验证**：`uv run pytest tests/test_loop.py -v` — B4 测试 `"DeZhu Agent" in messages[0]["content"]` 必须通过。更新 B4 测试名 `test_b4_tools_passed_via_api_param`，docstring "B4: 工具通过 API tools 参数传递，不写入 system prompt 文本"
- **依赖**：步骤 2
- **风险**：低 — 仅改 `build_system_prompt` 实现，签名和返回类型不变

### 步骤 4：loop.py — 集成 session 级 system prompt 的存储与复用

- **改动**：`src/dezhu_agent/loop.py`
- **做什么**：
  1. `run_conversation()` 第 55-58 行区域：当 `storage` 和 `session_id` 均非 None：
     - `storage.load_system_prompt(session_id)` — 非空→直接用作 `system_prompt`
     - 为空→`build_system_prompt(tools)` 组装后 `storage.save_system_prompt(session_id, ...)` 持久化
  2. 当 storage 为 None（现有测试路径），行为不变—每次调用 `build_system_prompt()`
  3. `system_prompt` 变量保持在循环外（当前已在第 57 行，无需移动）
- **验证**：`uv run pytest tests/test_loop.py -v` 全部通过。新增测试：mock storage 验证新会话写入 system_prompt、恢复会话读取已有 prompt 不重新组装
- **依赖**：步骤 1 + 步骤 3
- **风险**：中 — 改动核心循环的 prompt 获取逻辑；storage=None 路径行为不变，风险可控

### 步骤 5：__main__.py — 确认 CLI 层集成（最小改动，主要是验证）

- **改动**：`src/dezhu_agent/__main__.py`
- **做什么**：确认 `_run_continue()` 和默认模式均已将 `session_id` 传入 `run_conversation()`，loop 层自动处理 system_prompt 的组装/复用。无需额外代码改动。
- **验证**：手动测试 — 新会话后检查 `.dezhu-agent/dezhu-agent.db` 中 `sessions.system_prompt` 非空；`--continue` 恢复后 `DEZHU_DEBUG=1` 确认无重新组装日志
- **依赖**：步骤 4
- **风险**：低 — 集成验证，代码改动极少（最多加注释）

### 步骤 6：测试 — test_prompt_assembler.py（新）+ 更新 test_loop.py B4

- **改动**：`tests/test_prompt_assembler.py`（新）、`tests/test_loop.py`（更新 B4）
- **做什么**：
  - `test_prompt_assembler.py`：用 `tmp_path` fixture mock SOUL.md / AGENTS.md，覆盖 12 个场景
  - 更新 B4 测试名和 docstring
- **验证**：`uv run pytest tests/test_prompt_assembler.py tests/test_loop.py -v`
- **依赖**：步骤 2
- **风险**：低

---

## 7. Review 记录

### 审查结论：✅ 可合并

六维审查 + 三视角（产品/工程/QA）并行审查，发现 3 阻塞 + 4 建议，全部已修复。

### 发现与修复

| # | 维度 | 严重度 | 位置 | 问题 | 修复 |
|---|------|--------|------|------|------|
| 1 | QA | 🔴 阻塞 | `prompt_assembler.py:84` | `Path.read_text()` 将 `\r\n` 转换为 `\n`，违反 spec 验收 #11 | 改用 `read_bytes().decode("utf-8-sig")` 保留原始换行 |
| 2 | QA | 🔴 阻塞 | `loop.py:59-63` | `save_system_prompt`/`load_system_prompt` 路径零测试覆盖（验收 #1/#5） | 新增 `test_p5`（新会话写入）+ `test_p6`（复用不重装） |
| 3 | 工程 | 🔴 阻塞 | `storage.py:99` | `except OperationalError: pass` 过宽，静默吞磁盘 I/O 错误 | 检查 `"duplicate column name"` 才 pass，否则 raise |
| 4 | 工程 | 🟡 建议 | `prompt_assembler.py:56,67` | `_load_soul`/`_load_agents` 仅捕获 `FileNotFoundError`，其他 OSError 传播崩溃 | 改用 `except OSError` 覆盖权限/编码错误 |
| 5 | 产品 | 🟡 建议 | `tests/` | 特殊字符 `{ } "` 无专项回归测试（验收 #9） | 新增 `test_special_characters_preserved` |
| 6 | QA | 🟡 建议 | `tests/` | Debug 输出格式验证不完整（缺字节数/120字符截断） | 新增 `test_debug_output_shows_byte_count_and_preview_truncation` |
| 7 | 产品 | 🟡 建议 | `prompt_assembler.py:94` | Debug 标注 "字节" 但实际是 `len()` 字符数 | 改用 `len(s.encode("utf-8"))` 真实字节数 |

### 需用户澄清

- [x] **AGENTS.md 路径**：`PROJECT_ROOT` 指向 dezhu-agent 包源码根而非用户 `$CWD`。→ **已解决**：新增 `config.PROJECT_DIR`，通过 `DEZHU_PROJECT_DIR` 环境变量指定，默认 `Path.cwd()`。`_load_agents()` 改用 `PROJECT_DIR / "AGENTS.md"`。

### 验收对标结果

| 总验收 | 已实现 | 已测试 | 备注 |
|--------|--------|--------|------|
| 13 | 13 | 13 | 修复后全覆盖；验收 #1/#5 通过 P5/P6 补充覆盖 |

### 最终测试

`uv run pytest tests/ -q` → **91 passed**

---

<!-- SPEC_STATUS: reviewed — 结论：✅ 可合并（3 个阻塞项已修复，建议项非阻塞） -->
