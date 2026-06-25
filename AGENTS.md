# dezhu-agent

AI agent 项目（Python），实现 Hermes Agent 对话循环。

## 项目
- **技术栈：** Python 3.10+，使用 [uv](https://docs.astral.sh/uv/) 管理环境和依赖。
- **依赖：** `openai`（LLM API 客户端）、`python-dotenv`（.env 加载）、`pyyaml`（配置解析）、`socksio`（SOCKS 代理支持）

## 命令
- **同步依赖：** `uv sync`
- **运行（交互式，从 stdin 读取）：** `echo "你好" | uv run python -m dezhu_agent`
- **测试：** `uv run pytest`（41 条，含 loop + registry 测试）
- **测试（真实 API）：** `echo "读一下 pyproject.toml" | uv run python -m dezhu_agent`
- **Lint：** `uv run ruff check .`
- **格式化：** `uv run ruff format .`

## 架构
- `src/dezhu_agent/` — 核心源码包
  - `__init__.py` — 包入口
  - `__main__.py` — CLI 入口（交互式，逐行 `input()` 读取，空行跳过，Ctrl+D 退出）
  - `config.py` — 配置管理（`.env` + 环境变量，API Key 延迟 fail-fast）
  - `messages.py` — 消息模型（`Message` dataclass + `to_api_dict()` 清洗内部字段）
  - `prompt.py` — System prompt 组装 + OpenAI tools 格式转换
  - `llm.py` — LLM API 客户端（openai SDK 封装，单例 client）
  - `loop.py` — 核心对话循环（`run_conversation`，stop/tool_calls/length 三路分支 + budget 管理 + 工具执行 stderr 日志）
  - `tools/` — 可插拔工具系统（原 `tools.py` 已替换为 package）
    - `__init__.py` — `ToolDef` dataclass、`ToolRegistry` 类、`@tool` 装饰器、`scan_tools()` 自动发现、模块级单例 `registry`
    - `read_file.py` — 读取文件内容
    - `write_file.py` — 覆盖写入文件
    - `edit_file.py` — 精确文本替换（old_string 必须唯一出现）
    - `shell.py` — 执行 shell 命令（超时 60s，输出截断至 10k 字符）
  - `tools_config.py` — YAML 配置加载（`load_config`、`apply_config`、`load_scan_paths`）
- `tests/` — 测试
  - `conftest.py` — 共享 fixture（自动设置环境变量 + 用独立 ToolRegistry 实例隔离测试）
  - `test_loop.py` — 24 条验收测试（N1-N4, E1-E4, B1-B4, T1-T4, S1-S3, C1-C3）
  - `test_tool_registry.py` — 17 条测试（N1-N9, E1-E4, B1-B5，使用独立 ToolRegistry 实例）
- `.env.example` — 环境变量模板
- `specs/agent-loop.md` — 六要素 spec（已完成 brainstorm → write → implement → review 全流程）
- `specs/pluggable-tools.md` — 可插拔工具系统 spec（已实现）

## 约定
- 行宽 100，使用 ruff 管理 lint 和格式
- 消息模型使用 `Message` dataclass，内部字段以下划线前缀标记（`reasoning`、`_internal`）
- API Key 通过 `.env` 文件或环境变量传入，不写入代码
- API Key 采用延迟 fail-fast：`config.py` import 时不检查，`llm._get_client()` 首次调用时才校验
- **新增工具**：在 `src/dezhu_agent/tools/` 下创建 `.py` 文件，使用 `@tool(name=..., description=...)` 装饰函数，自动注册到 `registry`，零改动 `loop.py`
- `@tool` 装饰器从函数签名自动生成 `parameters`：`str→string`、`int→integer`、`bool→boolean`，有默认值的参数自动为非 required
- **工具启用/禁用**：通过 `tools_config.yaml` 配置，重启生效
- **测试隔离**：registry 测试使用独立 `ToolRegistry()` 实例；loop 测试由 conftest 的 autouse fixture 将 `dezhu_agent.tools.registry` 替换为独立实例
- 测试中 `_TOOL_REGISTRATIONS` 列表在每次 @tool 装饰器使用时需先 `.clear()`，避免跨测试污染
- **数据库文件**：默认存放在 `.dezhu-agent/dezhu-agent.db`，可通过 `DEZHU_DB_PATH` 环境变量或 `--db-path` 参数覆盖。自动创建，无需手动初始化。
- **Commit 消息**：使用中文+英文混合描述。中文说明改动类别，英文/代码术语保持原文。格式参考：`<type>: <中文概述>\n\n【模块/分类】\n- <英文/代码细节>\n\nref: specs/<file>.md`
