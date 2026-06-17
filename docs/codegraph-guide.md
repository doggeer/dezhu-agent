<!--
  CodeGraph 工具使用文档
  项目: dezhu-agent
  生成日期: 2026-06-17
-->

# CodeGraph 使用手册

## 概述

CodeGraph 是一个语义代码智能工具，为代码库构建知识图谱 —— 符号关系、调用图、代码结构。AI Agent 查询图谱即可获得精确答案，无需反复 grep / read 文件。**完全本地运行，不上传任何代码。**

- **版本**: 1.0.1
- **安装方式**: npm 全局安装 (`@colbymchenry/codegraph`)
- **仓库**: [github.com/colbymchenry/codegraph](https://github.com/colbymchenry/codegraph)

## 安装与初始化

### 1. 安装 CLI

```bash
# macOS / Linux
curl -fsSL https://raw.githubusercontent.com/colbymchenry/codegraph/main/install.sh | sh

# Windows (PowerShell)
irm https://raw.githubusercontent.com/colbymchenry/codegraph/main/install.ps1 | iex

# 或通过 npm
npm i -g @colbymchenry/codegraph
```

### 2. 接入 Agent

```bash
# 交互式选择要配置的 Agent
codegraph install

# 或跳过交互
codegraph install --yes
```

自动检测并配置：Claude Code、Cursor、**Codex CLI**、opencode、Hermes Agent、Gemini CLI、Antigravity IDE、Kiro。

Codex CLI 配置变更：
- `~/.codex/config.toml` — 新增 `[mcp_servers.codegraph]` 节
- `~/.codex/AGENTS.md` — 新增 CodeGraph 使用指令

### 3. 初始化项目

```bash
cd your-project
codegraph init       # 交互式
codegraph init -i    # 非交互式 (初始化并立即索引)
```

初始化后生成 `.codegraph/` 目录，包含 SQLite 索引文件。

## 核心命令

| 命令 | 用途 | 示例 |
|------|------|------|
| `codegraph status` | 查看索引统计和健康状态 | `codegraph status` |
| `codegraph sync` | 同步增量变更 | `codegraph sync` |
| `codegraph query <关键词>` | 搜索符号 | `codegraph query "Settings"` |
| `codegraph explore <查询>` | 语义探索 (源码 + 调用路径 + 影响面) | `codegraph explore "config settings"` |
| `codegraph node <名称>` | 单个符号/文件的源码 + 调用链 | `codegraph node "config.py"` |
| `codegraph callers <符号>` | 查找谁调用了某个符号 | `codegraph callers "get_config"` |
| `codegraph callees <符号>` | 查找某个符号调用了谁 | `codegraph callees "build_system_prompt"` |
| `codegraph impact <符号>` | 分析修改影响面 | `codegraph impact "Settings" --depth 3` |
| `codegraph affected <文件...>` | 找出受影响的测试文件 | `codegraph affected src/dezhu_agent/config.py` |
| `codegraph files` | 展示项目文件结构 | `codegraph files` |

## MCP 工具 (需 Codex 重启后生效)

| 工具 | 说明 |
|------|------|
| `codegraph_explore` | 一次性回答大部分代码问题：相关符号的逐字源码 + 调用路径 + 影响面 |
| `codegraph_node` | 返回单个符号的源码 + 调用者/被调用者链路，或按行号读文件 |

**注意**: `codegraph install` 后需重启 Codex，MCP 服务器才会自动连接。重启前 CLI 命令 (`codegraph explore` / `codegraph node`) 功能完全等价。

## 常用选项

| 选项 | 适用命令 | 说明 |
|------|----------|------|
| `-p, --path <path>` | explore / node / callers / callees / impact / affected | 指定项目路径 |
| `--max-files <n>` | explore | 限制返回的文件数 |
| `-f, --file <file>` | node | 按文件模式读取 (带行号) |
| `--offset <n>` | node | 起始行号 |
| `--limit <n>` | node | 最大行数 |
| `-d, --depth <n>` | impact / affected | 遍历深度 |
| `-j, --json` | impact / affected / node | JSON 格式输出 |
| `-q, --quiet` | affected | 仅输出文件路径 |

## 本项目索引概况

```
文件:  30
节点:  339 (method 119 / import 97 / class 39 / function 31 / file 29 / variable 24)
边:    720
数据库: 0.79 MB (node:sqlite WAL 模式)
语言:  Python 29 / YAML 1
```

## 自动同步

CodeGraph 启动 watchdog 后台进程 (daemon)，监测项目文件变更并自动更新索引。索引始终与磁盘保持同步。

```bash
# 管理后台进程
codegraph daemons        # 列出并停止运行中的后台守护进程
```

## 排除规则

以下目录和文件默认不索引：
- 依赖/构建/缓存目录：`node_modules`、`vendor`、`dist`、`build`、`target`、`.venv`、`Pods`、`.next` 等
- `.gitignore` 中列出的所有内容
- 大于 1 MB 的文件

如需额外排除，添加到 `.gitignore`。如需索引某个默认排除的目录，在 `.gitignore` 中添加否定规则：`!vendor/`。

## 卸载

```bash
codegraph uninstall    # 从所有 Agent 中移除配置
codegraph uninit       # 删除项目的 .codegraph/ 目录
```

## 遥测

CodeGraph 收集匿名使用统计（工具使用、语言分布等），**不上传** 代码、路径、文件名、符号名、查询内容或 IP 地址。

```bash
codegraph telemetry off    # 关闭
# 或设置环境变量: CODEGRAPH_TELEMETRY=0 / DO_NOT_TRACK=1
```

## 故障排查

| 问题 | 解决 |
|------|------|
| "CodeGraph not initialized" | 在项目根目录执行 `codegraph init` |
| 索引缺失符号 | 执行 `codegraph sync`，确认语言支持且不在排除目录中 |
| MCP 未连接 | 重启 Codex，确认 `codegraph status` 状态正常 |
| `database is locked` | 检查 WAL 模式：`codegraph status` 中 Journal 应为 `wal` |

## 支持的语言

TypeScript/JavaScript、Python、Go、Rust、Java、C#、PHP、Ruby、C/C++、Objective-C、Swift、Kotlin、Scala、Dart、Svelte、Vue、Astro、Liquid、Pascal/Delphi、Lua、R、Luau
