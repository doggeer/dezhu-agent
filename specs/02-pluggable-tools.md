# Spec：可插拔工具系统（Pluggable Tools）

## 1. 背景与目标（Why）

当前工具系统存在三个紧密耦合的问题：

1. **工具定义与主循环硬编码**：`tools.py` 中的 `TOOL_REGISTRY` 和 `execute_tool()` 与 `loop.py` 的调用逻辑紧耦合。新增或修改工具需要同时改动 `tools.py`（注册）和可能影响 `loop.py`（调用路径），破坏主流程稳定性。
2. **工具需手动告知 agent**：创建新工具后，需要手动在系统 prompt 或 API tools 参数中注册，agent 才能使用。缺少自动发现机制，容易遗漏。
3. **不支持运行时启用/禁用**：所有工具一旦注册就永远可用。日常使用中无法按需开关工具，且没有考虑 DeepSeek KV Cache 前缀缓存原则——工具定义内容/顺序变化会导致缓存失效。

**成功衡量指标**：
- 新增一个工具只需创建单个 Python 文件 + 使用装饰器注册，零改动 `loop.py`
- 进程启动时自动发现并注册所有工具
- 配置文件控制单个工具的启用/禁用，重启后生效
- 现有行为的验收测试全部通过（允许重构测试代码）

## 2. 范围

### 做
- 引入 `ToolRegistry` 类作为工具注册中心的统一接口
- 提供 `@tool` 装饰器，用于在工具实现文件上声明注册
- 进程启动时按配置文件指定的路径扫描目录发现工具（默认 `src/dezhu_agent/tools/`）
- 通过配置文件（YAML/JSON）控制单个工具的启用/禁用，重启后生效
- 原有 `read_file`、`write_file` 工具迁移到新架构
- 内置工具集：`read_file`、`write_file`、`edit_file`（精确文本替换）、`shell`（shell 命令执行）
- `loop.py` 通过 `ToolRegistry` 获取工具列表和执行工具，不直接引用具体工具实现
- 新增或移除工具时，`loop.py`、`prompt.py` 零改动

### 不做（Out of scope）
- 不实现文件系统 watch 热加载
- 不实现按会话/对话的临时启用/禁用
- 不实现通过自然语言让 agent 自己控制开关
- 不做 Prompt Cache 分段优化（本轮重构不涉及 cache 策略）
- 不引入 MCP 协议或外部进程通信
- 不支持 Registry 接口本身可插拔（只做单一 ToolRegistry 实现）
- 不实现配置变更热重载（修改配置文件需重启进程）
- 不实现工具执行结果缓存
- 不实现工具执行耗时统计（当前仅打印调用信息，不测量耗时）

## 3. 约束

- **Python 版本**：≥3.10，与项目要求一致
- **外部依赖**：新增 `PyYAML` 作为运行时依赖（用于解析 YAML 配置文件）
- **向后兼容**：`get_tool_list()` 和 `execute_tool()` 的公开签名可变更，但 `loop.py` 通过 Registry 获取的最终工具定义格式必须保持与 OpenAI API `tools` 参数兼容（name, description, parameters）
- **装饰器行为**：`@tool` 装饰器不得改变被装饰函数的调用签名，不得引入被装饰函数不预期的参数
- **sys.path 稳定性**：自动扫描不得修改 `sys.path`，仅通过 `importlib.import_module` 相对导入
- **配置格式**：YAML，文件名 `tools_config.yaml`，支持自定义路径
- **扫描路径可配置**：配置文件中的 `scan_paths` 字段控制扫描路径（可配多个），默认值为 `["src/dezhu_agent/tools/"]`

## 4. 既有决策

### 已选定
| 决策 | 理由 |
|---|---|
| `ToolRegistry` 统一抽象（方案A） | loop.py 只依赖 Registry 接口，不接触具体工具实现 |
| 进程启动时扫描一次 | 最稳定，不引入热加载复杂性，KV Cache 在进程生命周期内有效 |
| Python 文件 + `@tool` 装饰器 | 轻量、类型安全、零配置，新建文件即自动注册 |
| 单个工具级别启用/禁用 | 粒度最灵活，适配日常使用场景 |
| 配置文件持久化（重启生效） | 简单可靠，无需运行时状态同步 |
| 允许重写测试 | 新架构下部分内部细节测试不再适用 |

### 已否决
| 方案 | 否决理由 | 下次评估条件 |
|---|---|---|
| MCP 协议 / 外部进程通信 | 引入分布式复杂性，当前单进程场景不需要 | 当 agent 需要访问远程工具（如远端 API）时评估 |
| 热加载（watch 模式） | 引入文件系统监听复杂度和竞态条件 | 当工具有频繁热更新需求时评估 |
| Registry 接口可插拔 | 过度抽象，当前没有多 Registry 实现需求 | 当需要本地 + 远程 Registry 共存时评估 |
| 会话级启用/禁用 | 状态管理复杂，需要运行时持久化 | 当需要"本次对话禁用某个工具"的明确场景出现时评估 |
| 自然语言开关 | Prompt 注入风险，行为不可预测 | 未来可考虑作为辅助交互方式，不作为主控制路径 |
| Prompt Cache 分段优化 | 当前 cost/benefit 不显著 | 当工具数量 > 20 或 cache miss 成本可测量时评估 |

## 5. 行为与验收

### 正常路径

**N1: 装饰器注册工具**
- WHEN 用户创建一个 Python 文件 `tools/my_tool.py`，使用 `@tool(name="my_tool", description="...")` 装饰一个函数，THE SYSTEM SHALL 令该工具可通过 `ToolRegistry` 获取到，且 name/description/parameters 与装饰器声明一致

**N2: 目录扫描自动发现**
- WHEN 进程启动，THE SYSTEM SHALL 按配置文件中 `scan_paths` 指定的路径扫描目录下的所有 `*.py` 文件（除了 `__init__.py`），自动发现并注册其中使用 `@tool` 装饰的函数

**N3: 通过 Registry 获取工具列表**
- WHEN `loop.py` 调用 `ToolRegistry.get_tools()`，THE SYSTEM SHALL 返回一个列表，列表元素格式与 OpenAI API `tools` 参数兼容（`{name, description, parameters}`），且内容与当前启用的工具集合一致

**N4: 通过 Registry 执行工具**
- WHEN `loop.py` 调用 `ToolRegistry.execute("my_tool", {"arg1": "val1"})`，THE SYSTEM SHALL 调用对应工具函数并返回执行结果字符串

**N5: 配置文件控制启用/禁用**
- WHEN 配置文件中将某个工具设置为 `enabled: false`，THE SYSTEM SHALL 在 `ToolRegistry.get_tools()` 的返回列表中排除该工具，且 `ToolRegistry.execute()` 对该工具返回 "Tool 'xxx' is disabled"
- WHEN 配置文件将该工具重新设置为 `enabled: true` 并重启进程，THE SYSTEM SHALL 恢复该工具的可用状态

**N6: 零改动主流程**
- WHEN 新增一个工具（创建文件 + 装饰器）或移除一个工具（删除文件），THE SYSTEM SHALL 无需修改 `loop.py` 或 `prompt.py` 中的任何代码

**N7: edit_file 精确替换**
- WHEN 调用 `edit_file` 工具且 `old_string` 在文件中唯一出现，THE SYSTEM SHALL 将其替换为 `new_string` 并返回成功消息
- WHEN `old_string` 在文件中出现 0 次，THE SYSTEM SHALL 返回错误消息 `"Error: old_string not found in {path}"`
- WHEN `old_string` 在文件中出现多次，THE SYSTEM SHALL 返回错误消息指明出现次数，不执行替换

**N8: shell 命令执行**
- WHEN 调用 `shell` 工具，THE SYSTEM SHALL 在项目 shell 中执行命令并返回 stdout + stderr
- WHEN 命令执行超时（>60s），THE SYSTEM SHALL 返回超时错误消息
- WHEN 命令输出超过 10000 字符，THE SYSTEM SHALL 截断输出并附加 `(truncated)` 标记

**N9: 工具执行信息显示**
- WHEN 任意工具被调用，THE SYSTEM SHALL 在 stderr 打印格式化日志，包含工具名、参数和结果摘要
- WHEN 结果超过 6 行，THE SYSTEM SHALL 截断显示为前 3 行 + 后 3 行

### 异常路径

**E1: 工具函数执行异常**
- WHEN 工具函数执行时抛出任意异常，`ToolRegistry.execute()` SHALL 返回 `"Error executing tool '{name}': {exception_message}"`，不向上抛出异常

**E2: 调用不存在的工具**
- WHEN 调用 `ToolRegistry.execute()` 传入一个未注册/未启用的工具名，THE SYSTEM SHALL 返回 `"Tool '{name}' not found"`

**E3: 装饰器参数不合法**
- WHEN `@tool()` 装饰器缺少 `name` 参数，THE SYSTEM SHALL 在进程启动扫描时抛出 `ValueError`，fail-fast

**E4: 配置格式错误**
- WHEN 配置文件存在但格式不符合规范（如非法的 YAML/JSON），THE SYSTEM SHALL 在进程启动时抛出异常，fail-fast

### 边界条件

**B1: 空工具目录**
- WHEN `src/dezhu_agent/tools/` 目录下没有任何带 `@tool` 装饰器的文件，THE SYSTEM SHALL 令 `ToolRegistry.get_tools()` 返回空列表，`execute()` 对所有调用返回 `"Tool '{name}' not found"`

**B2: 配置文件不存在**
- WHEN 配置文件（`tools_config.yaml`）不存在，THE SYSTEM SHALL 视为所有已注册工具均为启用状态，不报错

**B3: 所有工具被禁用**
- WHEN 配置文件中所有工具均为 `enabled: false`，THE SYSTEM SHALL 令 `ToolRegistry.get_tools()` 返回空列表

**B4: 工具名与配置不匹配**
- WHEN 配置文件中列举了某个工具名，但实际没有对应注册的工具，THE SYSTEM SHALL 忽略该配置项（视为空操作，不报错）

**B5: 注册的函数签名为空**
- WHEN `@tool` 装饰的函数没有参数，THE SYSTEM SHALL 生成的 `parameters` 中 `properties` 为空对象、`required` 为空数组

## 6. 实现计划

> 按顺序执行，每步可独立验证。

### 步骤 1：添加 PyYAML 依赖

- **改动**：`pyproject.toml`
- **做什么**：在 `[project] dependencies` 中添加 `"pyyaml>=6.0"`
- **验证**：`uv sync` 成功，无报错
- **依赖**：无
- **风险**：低 — 标准依赖添加

### 步骤 2：实现 ToolRegistry 核心类 + @tool 装饰器

- **改动**：**创建** `src/dezhu_agent/tools/__init__.py`
  - ⚠️ `tools.py` 和 `tools/` package 不能共存，此步骤前需**删除** `src/dezhu_agent/tools.py`
- **做什么**：
  - 定义 `ToolDef` dataclass：`name`, `description`, `parameters`, `fn`, `enabled`
  - 定义 `ToolRegistry` 类：`register(tool: ToolDef)`, `execute(name, args) → str`, `get_tools() → list[dict]`, `get_tool_names() → list[str]`
  - 实现 `tool(name, description)` 装饰器：
    - 用 `inspect.signature` 解析被装饰函数参数，自动生成 `parameters`（`str→string`, `int→integer`, `bool→boolean`，有默认值的参数不加入 `required`）
    - 缺少 `name` 时抛出 `ValueError`
  - 创建模块级单例 `registry = ToolRegistry()`
- **验证**：`uv run python -c "from dezhu_agent.tools import registry; print(type(registry).__name__)"` 输出 `ToolRegistry`
- **依赖**：步骤 1（有 PyYAML 后 config 才能用 YAML，但 ToolRegistry 本身不依赖 YAML，可并行）
- **并行标注**：可与步骤 3 并行
- **风险**：低 — 纯新增，不碰现有逻辑

### 步骤 3：实现配置文件加载

- **改动**：**创建** `src/dezhu_agent/tools_config.py`
- **做什么**：
  - 定义默认路径：`PROJECT_ROOT / "tools_config.yaml"`（可被 `TOOLS_CONFIG_PATH` 环境变量覆盖）
  - 实现 `load_config(path: str | None = None) → dict`：
    - 文件不存在返回 `{}`（视为全启用）
    - YAML 格式错误时抛出异常（fail-fast）
  - 实现 `apply_config(registry: ToolRegistry, config: dict)`：
    - 读取 `config["tools"]` 中的 `{tool_name: {enabled: bool}}`
    - 对 `enabled: false` 的工具调用 `registry.disable(name)`
    - 配置中列了实际未注册的工具名 → 静默忽略
  - 实现 `load_scan_paths(config: dict) → list[str]`：
    - 读取 `config["scan_paths"]`，默认返回 `["src/dezhu_agent/tools"]`（代码内部转换为点号路径用于 importlib）
- **验证**：`uv run python -c "from dezhu_agent.tools_config import load_config, apply_config; print('OK')"`
- **依赖**：步骤 1（PyYAML）
- **并行标注**：可与步骤 2 并行
- **风险**：低

### 步骤 4：将 config 集成到 tools/__init__.py 模块初始化中

- **改动**：**编辑** `src/dezhu_agent/tools/__init__.py`（追加模块级初始化逻辑）
- **做什么**：
  - 在模块加载时：`load_config()` → 读取 `scan_paths` → 对每个路径执行 `scan_tools(pkg_path)`
  - 实现 `scan_tools(pkg_path: str)`：
    - 使用 `importlib.import_module(pkg_path)` 加载 package
    - 将文件系统路径（如 `src/dezhu_agent/tools`）转换为点号路径（`dezhu_agent.tools`）用于 importlib
    - 遍历 package `__path__` 下的所有 `*.py` 文件（排除 `__init__.py`）
    - 为每个文件调用 `importlib.import_module(f"{pkg_path_dotted}.{stem}")`
    - 检查该模块的 `__tool_registrations__` 列表（由 `@tool` 装饰器在函数上设置标记），注册到 registry
  - 扫描完成后：`apply_config(registry, config)`
- **验证**：现有 `loop.py` 能正常导入且 `registry.get_tool_names()` 返回所有已注册工具（验证整体集成）
- **依赖**：步骤 2 + 步骤 3
- **风险**：中 — 模块级初始化在 import 时自动执行，测试隔离需要专门处理

### 步骤 5：迁移现有工具 read_file / write_file

- **改动**：**创建** `src/dezhu_agent/tools/read_file.py` 和 `src/dezhu_agent/tools/write_file.py`
- **做什么**：
  - 从 `tools.py` 复制 `_read_file` / `_write_file` 函数体
  - 分别用 `@tool(name="read_file", description="...")` 装饰
  - 确保生成的 `parameters` 与原来一致（`path: str` / `path: str, content: str`）
- **验证**：`uv run python -c "from dezhu_agent.tools import registry; t=registry.execute('read_file', {'path':'pyproject.toml'}); print(len(t)>0)"` 输出 `True`
- **依赖**：步骤 4（registry 初始化完毕 + 扫描机制就绪）
- **风险**：低 — 纯搬运，函数体不变

### 步骤 6：重构 loop.py

- **改动**：**编辑** `src/dezhu_agent/loop.py`
- **做什么**：
  - 第 12 行：`from dezhu_agent.tools import execute_tool, get_tool_list` → `from dezhu_agent.tools import registry`
  - 第 44 行：`tools = get_tool_list()` → `tools = registry.get_tools()`
  - 第 91 行：`result = execute_tool(tool_name, tool_args)` → `result = registry.execute(tool_name, tool_args)`
- **验证**：`uv run python -c "from dezhu_agent.loop import run_conversation; print('OK')"`（验证导入成功）
- **依赖**：步骤 5（工具已迁移）
- **风险**：中 — 主流程代码，改动虽小但影响整个对话循环。回滚简单（改一行 import）

### 步骤 7：删除旧 tools.py 文件

- **改动**：**删除** `src/dezhu_agent/tools.py`
- **做什么**：所有功能已迁移到 `tools/` package，旧文件不再需要
- **验证**：`uv run python -c "from dezhu_agent.tools import registry; print(registry.get_tool_names())"` 正常工作
- **依赖**：步骤 6（loop.py 不再引用 tools.py）
- **风险**：低 — 纯清理

### 步骤 8：编写 registry 单元测试

- **改动**：**创建** `tests/test_tool_registry.py`
- **做什么**：每个测试创建独立的 `ToolRegistry()` 实例 + 直接在测试文件中用 `@tool` 装饰器注册模拟工具，不依赖模块级扫描，覆盖以下验收标准（每项一条测试）：
  - **N1**: 装饰器注册 — 用 `@tool` 装饰一个简单函数 → 验证 registry 可获取
  - **N2**: 扫描发现 — 已由集成测试覆盖，此处测 `scan_tools()` 函数
  - **N3**: `get_tools()` 返回正确格式且只含启用工具
  - **N4**: `execute()` 正确调用并返回结果
  - **N5**: disable/enable 控制
  - **E1**: 工具执行异常包装
  - **E2**: 调用不存在工具
  - **E3**: 装饰器缺 name 参数 → ValueError
  - **E4**: 配置格式错误 → fail-fast
  - **B1**: 空目录/空 registry
  - **B2**: 配置文件不存在 → 全部启用
  - **B3**: 全禁用 → 空列表
  - **B4**: 配置中提到未注册工具 → 静默忽略
  - **B5**: 空参数函数 → `properties` 空对象
- **验证**：`uv run pytest tests/test_tool_registry.py -v` 14 条全部通过
- **依赖**：步骤 5（工具迁移完成，可构造真实注册场景）
- **并行标注**：可与步骤 9 并行
- **风险**：低

### 步骤 9：适配 loop 层测试

- **改动**：**检查并调整** `tests/test_loop.py`
- **做什么**：
  - conftest 中使用 `monkeypatch` 将 `dezhu_agent.tools.registry` 替换为独立的 `ToolRegistry()` 实例，避免模块级初始化（扫描 + config 加载）影响测试
  - 每个测试函数前重置 mock registry 状态
  - test_b4 断言 `len(kwargs["tools"]) == 2` — 迁移后仍为 2（read_file + write_file），无需修改
  - test_e2/e3 依赖 `execute_tool` 的内部行为 — 迁移后 `registry.execute()` 返回相同格式，无需修改
  - 运行全部测试，修复可能的 import 路径问题
- **验证**：`uv run pytest tests/test_loop.py -v` 全部通过
- **依赖**：步骤 6（loop.py 已重构）
- **并行标注**：可与步骤 8 并行
- **风险**：低 — 主要是调整 fixture

---

### 执行顺序总览

```
步骤 1 (pyyaml)
   ├─ 步骤 2 (ToolRegistry) ──┐
   ├─ 步骤 3 (tools_config) ──┤  ← 可并行
   └───────────────────────────┘
              ↓
       步骤 4 (集成: 扫描 + config)
              ↓
       步骤 5 (迁移工具)
              ↓
       步骤 6 (重构 loop.py)
              ↓
       步骤 7 (删除旧 tools.py)
              ↓
  步骤 8 (registry 测试) ──┐
  步骤 9 (loop 测试适配) ──┤  ← 可并行
                            ↓
                    全部测试通过 ✓
```

<!-- SPEC_STATUS: implemented — 下一步：运行 spec_review -->