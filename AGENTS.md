# dezhu-agent

AI agent 项目（Python），实现 Hermes Agent 对话循环。

## 项目
- **技术栈：** Python 3.10+，使用 [uv](https://docs.astral.sh/uv/) 管理环境和依赖。
- **依赖：** `openai`（LLM API 客户端）、`python-dotenv`（.env 加载）、`socksio`（SOCKS 代理支持）

## 命令
- **同步依赖：** `uv sync`
- **运行（交互式，从 stdin 读取）：** `echo "你好" | uv run python -m dezhu_agent`
- **测试（mock）：** `uv run pytest`
- **测试（真实 API）：** `echo "读一下 pyproject.toml" | uv run python -m dezhu_agent`
- **Lint：** `uv run ruff check .`
- **格式化：** `uv run ruff format .`

## 架构
- `src/dezhu_agent/` — 核心源码包
  - `__init__.py` — 包入口
  - `__main__.py` — CLI 入口（交互式，逐行 `input()` 读取，空行跳过，Ctrl+D 退出）
  - `config.py` — 配置管理（`.env` + 环境变量，API Key 延迟 fail-fast）
  - `messages.py` — 消息模型（`Message` dataclass + `to_api_dict()` 清洗 `reasoning`、`_internal` 等内部字段）
  - `prompt.py` — System prompt 组装 + OpenAI tools 格式转换
  - `llm.py` — LLM API 客户端（openai SDK 封装，单例 client，首次调用时才校验 API Key）
  - `tools.py` — 工具执行器（`read_file` / `write_file`，白名单硬编码，E2/E3 错误处理）
  - `loop.py` — 核心对话循环（`run_conversation`，stop/tool_calls/length 三路分支 + budget 管理）
- `tests/` — 测试
  - `conftest.py` — 共享 fixture（autouse 设置 `OPENAI_API_KEY=sk-test`）
  - `test_loop.py` — 14 条验收测试（N1-N4, E1-E4, B1-B4）
- `.env.example` — 环境变量模板
- `specs/agent-loop.md` — 六要素 spec（已完成 brainstorm → write → implement → review 全流程）

## 编码约定
- 行宽 100，使用 ruff 管理 lint 和格式
- 消息模型使用 `Message` dataclass，内部字段以下划线前缀标记（`reasoning`、`_internal`）
- API Key 通过 `.env` 文件或环境变量传入，不写入代码
- API Key 采用延迟 fail-fast：`config.py` import 时不检查，`llm._get_client()` 首次调用时才校验
- 测试共享 fixture 统一放在 `tests/conftest.py` 中，各测试文件自动继承
