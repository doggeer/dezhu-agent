# dezhu-agent

基于 Python 3.12+ 的命令行 AI Agent，通过 DeepSeek API 驱动，可在终端中执行 shell 命令来回答用户问题。

## 快速开始

```bash
# 安装依赖
uv sync

# 配置环境变量（复制模板并填入 API Key）
cp .env.example .env

# 运行
uv run python -m dezhu_agent
```

## 功能

- 交互式对话界面，支持多轮对话
- 通过 `terminal` 工具在沙箱中执行 shell 命令
- 自动检测并拦截危险命令（`rm -rf /`、`mkfs`、`dd if=`、`shutdown`、`reboot` 等）
- 命令执行 30 秒超时保护，输出限制 10000 字符
- 每次对话最多 10 轮工具调用

## 配置

在 `.env` 文件中设置以下环境变量：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `APP_NAME` | `dezhu-agent` | 应用名称 |
| `ENV` | `development` | 运行环境 |
| `LOG_LEVEL` | `INFO` | 日志级别 |
| `BASE_URL` | `https://api.deepseek.com` | API 基础地址 |
| `API_KEY` | `sk-your-api-key` | API 密钥 |
| `MODEL` | `deepseek-v4-pro` | 模型名称 |
| `MAX_ITERATIONS` | `10` | 单次对话最大工具调用轮数 |

## 项目结构

```
dezhu-agent/
├── src/dezhu_agent/
│   ├── __init__.py          # 包入口，定义 __version__
│   ├── __main__.py          # python -m dezhu_agent 入口
│   ├── config.py            # Settings 类 (pydantic-settings)
│   ├── core/                # 核心逻辑模块
│   │   └── agent.py         # Agent 循环与工具执行
│   ├── models/              # 数据模型 (pydantic)
│   ├── services/            # 业务服务层
│   └── utils/               # 工具函数
├── tests/                   # 测试文件
├── pyproject.toml           # 项目元数据与工具配置
├── .env.example             # 环境变量模板
├── .pre-commit-config.yaml  # Pre-commit 钩子
└── uv.lock                  # 依赖锁定
```

## 开发

```bash
# 测试（含覆盖率）
uv run pytest

# Lint 检查
uv run ruff check src/

# 格式化
uv run ruff format src/

# 类型检查
uv run mypy src/

# 完整检查
uv run ruff check src/ && uv run ruff format --check src/ && uv run mypy src/

# 安装 pre-commit hooks
uv run pre-commit install
```

## 技术栈

| 依赖 | 用途 |
|------|------|
| pydantic / pydantic-settings | 数据校验与配置管理 |
| structlog | 结构化日志 |
| openai | DeepSeek API 客户端 |
| httpx | HTTP 客户端（含 SOCKS 代理支持） |
| pytest / pytest-cov | 测试框架 |
| ruff | Lint & 格式化 |
| mypy | 类型检查 |
| pre-commit | Git 钩子 |

## License

MIT
