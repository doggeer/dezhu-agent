---
name: spec_implement
description: 阶段 3 · 小步落地 — 按 spec_plan 的计划逐步执行。TDD 先行（验收→测试→实现），subagent 并行独立任务，每步可验证可回滚。实现中发现问题回写 spec。
---

# spec_implement · 阶段 3：小步落地

你是 SDD 流程的第四站。你的职责是按 `spec_plan` 产出的实现计划，小步、可验证、可回滚地把 spec 变成代码。

**前置条件**：`specs/<name>.md` 状态为 `planned`，包含实现计划（含步骤、依赖、并行标注）。
**产出物**：代码 + 测试，每步通过验证。spec 状态更新为 `implemented`。
**下游**：完成后交给 `spec_review` 对标验收。

---

## 操作流程

### 第一步：读取 spec + 计划

读取 `specs/<name>.md`，确认 `<!-- SPEC_STATUS: planned -->`。提取：

- 行为与验收（第 5 节）：每条验收将成为测试的锚点
- 实现计划：步骤列表、依赖关系、并行标注、每步的验证方法

### 第二步：TDD 先行 — 把验收写成测试

在写任何实现代码之前，先把 spec 第 5 节的验收标准转化为测试：

1. 逐条遍历验收标准
2. 每条验收写一个测试用例——测试名直接引用 spec 原文
3. 正常路径 → 正向测试；异常路径 → 异常测试；边界条件 → 边界测试
4. 运行测试——**全部失败（红灯）**，证明测试在测"还没实现的东西"

示例映射：

```
Spec 验收：WHEN 管理员提交合法 user_id 与 ≤90 天范围，
         THE SYSTEM SHALL 返回 CSV 下载

测试用例：
  it("管理员提交合法 user_id 与 ≤90 天范围，返回 CSV 下载", async () => {
    const res = await request(app)
      .get("/export?user_id=123&days=90")
      .set("Authorization", adminToken);
    expect(res.status).toBe(200);
    expect(res.headers["content-type"]).toBe("text/csv");
  });
```

**关键技巧**：测试名直接引用 spec 验收原文。这样 review 时对照 spec 和测试列表，一眼就知道每条验收有没有覆盖。

### 第三步：按计划逐步执行

按计划的顺序执行。每一步：

1. **回看这一步的 spec 原文**：确认你要实现的行为、约束、边界
2. **写代码**：只写这一步要改的东西，不顺手"优化"别的
3. **跑验证**：用计划里写的验证命令确认这一步对了
4. **提交（可选但推荐）**：用描述性的 commit message，引用 spec 文件名
5. **更新进度**：在计划中标记这一步为 ✅

**并行步骤**：如果计划中标注了可并行的步骤，用 subagent 同时处理。每个 subagent 拿到：
- 对应 spec 片段（只给它需要的那部分）
- 明确的输入/输出约定
- 独立的验证命令

### 第四步：异常回写 spec

实现过程中可能会发现：

- spec 写的验收在技术上做不到 → 回写 spec，标记为"待讨论"
- spec 漏了关键的边界条件 → 补充到 spec 的第 5 节
- 约束之间有冲突 → 回写 spec，标记冲突，请用户裁决

**原则**：spec 是事实基准。如果实现偏离了 spec，优先修代码；如果是 spec 本身错了，优先修 spec。不要让 spec 和代码脱节。

每次回写 spec 后，告诉用户：

> ⚠️ Spec 回写：`specs/<name>.md` 第 X 节已更新（原因：...）。请确认这个变更。

### 第五步：全量验证 + 落盘

所有步骤完成后：

1. 跑全量测试（不只是新增的测试，确保没破坏已有行为）
2. 逐条对照 spec 第 5 节的验收——每条都通过了吗？
3. 更新状态标记：

```markdown
<!-- SPEC_STATUS: implemented — 下一步：运行 spec_review -->
```

告诉用户：

> 实现完成，全量测试通过。下一步：运行 `spec_review` 对标验收做最终审查。

---

## Subagent 并行策略

不是所有步骤都适合并行。判断方法：

| 适合并行 | 不适合并行 |
|---------|-----------|
| 步骤之间无文件/模块依赖 | 步骤 B 依赖步骤 A 的输出 |
| 各自改动不同文件 | 改动同一文件的不同部分 |
| 各自有独立的验收标准 | 验收标准跨步骤（端到端） |
| 可独立编译/运行的模块 | 共享同一个数据模型迁移 |

并行执行时，给每个 subagent 的 prompt 应包含：
- 对应 spec 片段（验收 + 约束）
- 文件路径 + 做什么
- 验证命令
- 明确的"不要碰"清单（来自 spec 的"不做"列表）

---

## 提交信息规范（推荐）

```
<类型>: <简短描述>

ref: specs/<name>.md
step: <步骤编号>
```

例如：
```
feat: add activity export route with permission check

ref: specs/user-activity-export.md
step: 1
```
