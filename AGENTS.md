# AGENTS.md — 德柱Agent (dezhu-agent)

## 项目概述

dezhu-agent 是一个 AI Agent 服务，基于 Python 3.12+ 构建。

## 技术栈

- **语言**: Python 3.12+
- **包管理器**: uv (`uv.lock` 锁版本)
- **配置管理**: pydantic-settings (从 `.env` 加载)
- **日志**: structlog
- **测试**: pytest + pytest-cov
- **Lint/格式化**: ruff (line-length=120, double-quote)
- **类型检查**: mypy (strict 模式)
- **Git Hooks**: pre-commit (ruff + mypy)

## 目录结构

```
dezhu-agent/
├── src/dezhu_agent/
│   ├── __init__.py          # 包入口，定义 __version__
│   ├── __main__.py          # python -m dezhu_agent 入口
│   ├── config.py            # Settings 类 (pydantic-settings)
│   ├── core/                # 核心逻辑模块
│   │   ├── __init__.py
│   │   ├── agent.py         # Agent 循环、对话管理与持久化
│   │   ├── prompt_builder.py # 6 层 System Prompt 组装器
│   │   └── tools/           # 工具实现
│   │       ├── __init__.py
│   │       └── terminal_tool.py  # 终端命令执行工具
│   ├── models/              # 数据模型 (pydantic)
│   │   ├── __init__.py
│   │   ├── message.py       # ConversationResult 模型
│   │   ├── session.py       # SessionInfo 模型
│   │   └── tool.py          # ToolDef、BaseTool、tool_error
│   ├── services/            # 业务服务层
│   │   ├── __init__.py
│   │   ├── session_store.py # 会话持久化服务 (SQLite + WAL)
│   │   └── tool_registry.py # 工具注册中心
│   └── utils/               # 工具函数
│       ├── __init__.py
│       └── tool_decorator.py # @register_tool 装饰器
├── tests/                   # 测试文件
│   ├── __init__.py
│   ├── conftest.py
│   └── test_prompt_builder.py
├── pyproject.toml           # 项目元数据与工具配置
├── .env.example             # 环境变量模板
├── .pre-commit-config.yaml  # Pre-commit 钩子
└── uv.lock                  # 依赖锁定
```

## 开发规范

### 代码风格
- **引号**: 双引号 (`"`)
- **缩进**: 空格
- **行宽**: 120 字符
- **编码**: UTF-8, 允许中文注释
- **命名**: 遵循 PEP 8 (`snake_case` 变量/函数, `PascalCase` 类)

### Ruff 规则
启用: E, W, F, I, N, UP, B, SIM, RUF
忽略: RUF001 (允许中文全角标点)

### Mypy 规则
- `strict = true`
- `ignore_missing_imports = true` (第三方库放宽检查)

### 数据建模规范 — 避免裸数据

禁止在业务代码中使用裸 `dict` 传递结构化数据。统一使用 **Pydantic 模型** + **工具函数**。

**规则：**

1. **结构化数据 → Pydantic 模型**
   - 返回值、核心类型、领域概念优先定义为 Pydantic `BaseModel`
   - 示例: `ConversationResult(final_response=..., messages=...)` 而非 `{"final_response": ..., "messages": ...}`
   - `models/` 目录下放置所有数据模型

2. **重复构造的 JSON 字符串 → 工具函数**
   - 避免散落 `json.dumps({"error": "..."})` 或 `json.dumps({"key": value})`
   - 提取为语义清晰的函数，放在对应模型文件的末尾
   - 示例: `tool_error("Unknown tool")` 而非 `json.dumps({"error": "Unknown tool"})`

3. **外部 API 边界例外**
   - OpenAI SDK 要求的字典格式可在调用处临时构造，但内部传递必须使用模型
   - 模型通过 `.model_dump()` 或 `.to_openai_format()` 转换到外部格式

**反例：**

```python
# 裸 dict 返回值
return {"final_response": text, "messages": msgs}      # ✗
result["final_response"]                                 # ✗

# 裸 json.dumps 散落各处
json.dumps({"error": f"Unknown tool: {name}"})           # ✗
```

**正例：**

```python
# Pydantic 模型
return ConversationResult(final_response=text, messages=msgs)  # ✓
result.final_response                                            # ✓

# 语义化工具函数
tool_error(f"Unknown tool: {name}")                              # ✓
```

### 配置模式
- Settings 类继承 `pydantic_settings.BaseSettings`，定义在 `src/dezhu_agent/config.py`
- 通过 `get_config()` (带 `lru_cache`) 单例获取
- 新配置项直接在 `Settings` 类中定义，环境变量 `.env` 自动映射

### 单例模式
- 使用模块级 `@lru_cache` 函数获取单例，风格参考 `get_config()` / `get_tool_registry()` / `get_session_store()`
- 避免在类内部用 `_instance` + `@classmethod` 实现单例

### 服务层规范

- 服务类放在 `services/` 目录，一个文件一个服务
- 服务通过模块级 `@lru_cache` 函数暴露单例（如 `get_tool_registry()`、`get_session_store()`）
- 服务内部所需配置通过 `get_config()` 获取，不在构造函数中传入配置对象
- 服务不直接打印输出，通过日志记录；输出层（如 CLI print）放在 `core/` 或 `__main__.py`

### 数据库规范

- 数据库引擎统一使用 SQLite
- 必须开启 WAL 模式：`conn.execute("PRAGMA journal_mode=WAL")` — 让写操作不阻塞读
- 连接管理使用私有 `_connect()` + `@contextmanager _get_conn()` 模式：
  - `_connect()` 负责创建连接、设置 `row_factory`、开启 WAL 和 foreign_keys
  - `_get_conn()` 作为 context manager，封装 commit/rollback/close 生命周期
- 所有 SQL 参数使用参数化查询（`?` 占位符），禁止字符串拼接
- 时间字段统一使用 TEXT 类型，存储 `yyyy-MM-dd HH:mm:ss` 格式字符串
- 查询结果通过 Pydantic 模型返回（如 `SessionInfo`），禁止返回裸 `sqlite3.Row` 或 `dict`

## 常用命令

```bash
# 开发运行
uv run python -m dezhu_agent

# 测试 (含覆盖率)
uv run pytest

# Lint 检查
uv run ruff check src/

# 格式化代码
uv run ruff format src/

# 类型检查
uv run mypy src/

# 完整检查 (lint + 格式化 + 类型)
uv run ruff check src/ && uv run ruff format --check src/ && uv run mypy src/

# 安装 pre-commit hooks
uv run pre-commit install
```

## 依赖说明

| 依赖 | 版本 | 用途 |
|------|------|------|
| pydantic | >=2 | 数据校验 |
| pydantic-settings | >=2 | 配置管理 |
| structlog | >=24 | 结构化日志 |
| python-dotenv | >=1 | .env 加载 |
| pytest | >=8 | 测试框架 |
| pytest-cov | >=6 | 测试覆盖率 |
| ruff | >=0.11 | Lint & 格式化 |
| mypy | >=1 | 类型检查 |
| pre-commit | >=4 | Git 钩子 |

## 开发工作流

Agent 实现功能的完整流程如下。所有步骤均由 Agent 自动执行，无需用户催促。

### 1. 理解需求 → 阅读代码

- 明确需求目标：做什么、在哪个模块、预期行为
- 阅读相关现有代码，理解模块职责、数据流和调用关系
- 确定实现方案：新建哪些文件、修改哪些文件、新增哪些模型

### 2. 实现功能

按照项目规范编写代码：

- **生产代码**: 严格遵循 AGENTS.md 中的开发规范：
  - 结构化数据使用 Pydantic 模型（放在 `models/`），禁止裸 dict
  - 重复 JSON 序列化提取为语义化工具函数
  - 服务用 `services/` 目录 + `@lru_cache` 单例模式
  - 数据库操作使用参数化查询、WAL 模式、context manager 管理连接
- **测试代码**: 与生产代码同步编写，放在 `tests/` 目录下：
  - 覆盖正常路径和边界情况
  - 覆盖错误路径
  - 测试文件命名: `test_{模块名}.py`
  - 使用项目 `conftest.py` 中的已有 fixtures

### 3. 自检

实现完成后立即执行完整检查链：

```bash
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run mypy src/ && uv run pytest
```

各检查项含义：
- `ruff check`: 代码风格、潜在错误、import 排序
- `ruff format --check`: 代码格式一致性
- `mypy`: 类型正确性（strict 模式）
- `pytest`: 所有测试（含新增和已有）必须通过

### 4. 修复（如自检失败）

自检失败时：

- 分析每个失败项的错误信息，定位根本原因
- 修复代码（生产代码或测试代码）
- 重新执行步骤 3，直到全部通过
- 如果修复涉及已有测试失败，优先保证不破坏现有行为

### 5. 自动提交

自检全部通过后，执行 Git 提交：

- `git add` 暂存所有本次修改的文件（不包含无关变更）
- 生成简洁的中文 commit message，格式: `模块: 做了什么`
- 执行 `git commit`

### 6. 跳过自检的情况

以下情况可跳过自检直接提交：
- 仅修改 AGENTS.md 自身
- 仅修改文档（README、注释、docstring），且未涉及任何逻辑变更

### 7. 分支命名

新功能使用 `codex/` 前缀创建分支: `git checkout -b codex/功能简述`
