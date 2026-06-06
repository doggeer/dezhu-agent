---
name: git-commit
description: |
  分析 git 变更、运行 pre-commit 检查、生成中文 Conventional Commit 信息并执行提交。
  触发条件：用户说"提交代码"、"commit"、"提交更改"、"帮我提交"、"生成commit信息"、
  "git提交"、"amend提交"等与 git 提交相关的指令。
license: MIT
compatibility: opencode
metadata:
  category: version-control
  language: zh-cn
---

# Git 提交助手

分析 git 工作区变更，自动运行代码检查，生成规范的中文提交信息并执行提交。

## 前置检查

1. 确认当前目录是 git 仓库（检查 `.git` 目录是否存在）
2. 如果不存在 `.git` 目录，提示用户初始化仓库或检查路径

## 工作流程

### 步骤 1：分析 Git 状态

运行以下命令获取完整的工作区状态：

```bash
git status --porcelain          # 紧凑格式，便于解析
git diff --stat                 # 未暂存的变更统计
git diff --staged --stat        # 已暂存的变更统计
git diff --staged               # 已暂存的完整 diff (用于生成提交信息)
git diff                        # 未暂存的完整 diff (用于生成提交信息)
```

**状态分类逻辑**：
- 没有变更 → 提示"工作区干净，没有需要提交的内容"并结束
- 有未暂存变更 → 进入步骤 2，并提示用户当前文件尚未 `git add`
- 只有已暂存变更 → 进入步骤 2
- 有未跟踪文件 → 列出未跟踪文件，询问用户是否需要 `git add`

### 步骤 2：展示变更摘要

以分组形式展示变更，格式如下：

```
📋 变更摘要
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📁 已暂存 (3 files)
  + 新增: src/dezhu_agent/config.py (+12 lines)
  ~ 修改: src/dezhu_agent/core/agent.py (+8 -3 lines)
  - 删除: src/dezhu_agent/old_module.py (-25 lines)

📁 未暂存 (1 file)
  ~ 修改: README.md (+5 lines)

📁 未跟踪 (2 files)
  ? src/dezhu_agent/new_feature.py
  ? tests/test_new_feature.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
变更总计: 6 files, +25 -28
```

**处理决策**：
- 如果存在未暂存文件（且不是仅删除）：询问用户 "是否将所有未暂存文件一起 git add？"
- 如果存在未跟踪文件：逐文件询问是否添加，用户可回复 `y`/`n` 或 `all`
- 执行用户选择的 `git add` 操作

### 步骤 3：Pre-commit 检查

在执行提交前，按照以下优先级检查代码质量：

**3.1 检测项目类型与检查工具**：

根据项目文件自动检测：

| 检测条件 | 检查命令 |
|---------|---------|
| 存在 `pyproject.toml` 含 `[tool.ruff]` | `uv run ruff check src/` |
| 存在 `pyproject.toml` 含 `[tool.ruff.format]` | `uv run ruff format --check src/` |
| 存在 `package.json` 含 `lint` script | `npm run lint` |
| 存在 `.pre-commit-config.yaml` | `uv run pre-commit run` |
| 存在 `Cargo.toml` | `cargo fmt --check && cargo clippy` |
| 存在 `Makefile` 含 `lint` target | `make lint` |

**3.2 检查执行**：
- 优先运行项目级别的 pre-commit（如果配置了 `.pre-commit-config.yaml`）
- 否则运行检测到的 linter/formatter
- 只检查变更涉及的文件路径

**3.3 结果处理**：
- ✅ **全部通过** → 继续步骤 4
- ❌ **有错误** → 列出错误摘要，询问用户：
  - "修复后继续"：自动修复（如 `ruff --fix`），然后重新检查
  - "跳过检查"：忽略错误直接提交（不推荐）
  - "取消提交"：中断流程

### 步骤 4：生成中文 Commit 信息

**4.1 类型判断**：

分析 diff 内容，匹配以下类型：

| 类型 | 触发条件 |
|------|---------|
| `feat` | 新增功能、新增接口、新增模块、新增文件 |
| `fix` | 修复 bug、修复错误、修复异常、修复问题 |
| `refactor` | 重构代码、调整结构、优化逻辑（功能不变） |
| `chore` | 依赖更新、配置修改、构建工具、环境变更 |
| `docs` | 文档更新、README、注释补充 |
| `test` | 测试用例、测试配置、测试工具 |
| `style` | 代码格式、空格、换行（不影响逻辑） |
| `perf` | 性能优化、速度提升、内存优化 |

**4.2 Scope 推断**：

从变更最多的文件路径中提取模块名作为 scope：

```
src/dezhu_agent/config.py → config
src/dezhu_agent/core/agent.py → core
tests/test_agent.py → test
pyproject.toml → deps
.github/workflows/ci.yml → ci
```

如果变更涉及多个模块，使用涉及最多的模块名；如果无法确定，省略 scope。

**4.3 中文描述生成**：

根据 diff 内容，用简洁的中文总结变更，格式遵循：

```
<type>(<scope>): <中文描述>

- 变更点1
- 变更点2
```

**描述规则**：
- 一句话概括核心变更，不超过 50 字
- 使用动词开头的短句（如"修复"、"新增"、"重构"、"更新"）
- 如果变更较多，在 body 中用 `-` 列表补充细节
- 中文描述使用简体中文

**示例**：
```
feat(config): 支持从 .env 文件加载 Redis 配置

- 新增 RedisSettings 配置类
- 支持 REDIS_HOST/REDIS_PORT 环境变量
- 添加连接池默认值
```

```
fix(core): 修复 Agent 消息循环中的超时异常

- 在消息队列读取时添加 30 秒超时限制
- 超时后自动重试一次再抛出异常
```

### 步骤 5：用户确认

展示生成的 commit 信息并等待用户确认：

```
📝 生成的提交信息
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
feat(config): 支持从 .env 文件加载 Redis 配置

- 新增 RedisSettings 配置类
- 支持 REDIS_HOST/REDIS_PORT 环境变量
- 添加连接池默认值
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

提供三个选项：
- **确认提交** → 进入步骤 6
- **修改信息** → 让用户输入修改后的 message
- **取消提交** → 中断流程，保留暂存区不变

### 步骤 6：执行提交

```bash
git commit -m "generated message"
```

展示提交结果，包括 commit hash 摘要。

提交成功后，总结：
```
✅ 提交成功
   commit: a1b2c3d - feat(config): 支持从 .env 文件加载 Redis 配置
   变更: 3 files, +45 -12
```

## 特殊场景

### Amend 模式

如果用户明确说 "amend"、"追加提交"、"修改上一次提交"：
- 展示上一次提交信息
- 询问 "直接 amend 还是修改 commit message？"
- 执行 `git commit --amend` 或 `git commit --amend -m "新信息"`

### 大变更建议

当变更文件超过 10 个或总行数超过 200 行时：
- 分析 diff 按模块分组
- 建议拆分为多次提交，展示分组方案
- 由用户决定是否拆分还是单次提交

### 仅生成信息模式

如果用户说 "只生成 commit 信息"、"帮我写个 commit message"：
- 执行步骤 1-4，生成 commit 信息
- 不执行实际提交，将信息提供给用户复制使用

## 注意事项

- 所有 `git` 命令在工作目录根路径执行
- 提交前确保用户身份（`git config user.name` / `user.email`）已配置，如未配置则提示
- 敏感文件（`.env`、`*.pem`、`credentials.*`）如果出现在变更中，发出警告并建议从提交中移除
- 不要自动 `git push`
