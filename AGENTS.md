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
│   ├── models/              # 数据模型 (pydantic)
│   ├── services/            # 业务服务层
│   └── utils/               # 工具函数
├── tests/                   # 测试文件
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

### 配置模式
- Settings 类继承 `pydantic_settings.BaseSettings`，定义在 `src/dezhu_agent/config.py`
- 通过 `get_config()` (带 `lru_cache`) 单例获取
- 新配置项直接在 `Settings` 类中定义，环境变量 `.env` 自动映射

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
