# MVP-0.8 · 工程卫生收尾

> 基线：PROJECT.md v1.44
> 前置版本：MVP-0.7
> 日期：2026-10-24

## 1. 本版范围

本版**不新增用户可见能力**，只清理 MVP-0.5 至 MVP-0.7 期间累积的技术债，让后续功能开发不再被旧约定牵制。

包含 3 个交付项：

1. **H1 仓库卫生** —— 统一行尾、清理遗留文件、收敛忽略规则，消除“新文件静默不入库”的隐患。
2. **H2 工具输出统一为 `ToolResult`** —— 删除注册表对中文前缀的嗅探，让工具状态成为强类型契约。
3. **H3 工具批次执行再拆分** —— 把 `_execute_tool_batch`（当前 585 行）拆成“预处理 / 执行 / 记账 / 恢复”四个方法。

**不包含**：新工具、记忆系统、评测模块、多 Agent 编排、报告字段扩展、CLI 新参数。
超时参数化、`pyproject` 版本对齐留到后续版本（见第 6 节待办）。

## 2. 本版用户路径

任务执行路径与报告内容不变：
> 用户输入任务 → CLI → Agent → TaskRunner → 工具循环 → 完成判定 → 报告。

本版承诺：CLI 参数、报告字段、工具语义、Trace 事件名、恢复策略与 MVP-0.7 收官态一致；`tests/golden/` 的 6 条行为快照必须原样通过。

唯一的工程侧变化：把 `MVP/`、`diagram/` 移出忽略规则后，新增文档不再需要 `git add -f`。

## 3. 相对上一版的增量

### 架构 / 模块改动

| 模块 | 改动 |
|---|---|
| `app/tools/registry.py` | 删除 `_normalize_output` 的中文前缀嗅探；执行器必须返回 `ToolResult` |
| `app/tools/tools.py` | 所有工具方法返回 `ToolResult` 而非 `str` |
| `app/tools/git_tools.py` | `GitReadTools.status/diff` 返回 `ToolResult` |
| `app/core/runner.py` | `_execute_tool_batch` 拆为四个方法，行为不变 |
| `.gitattributes`（新增） | 统一 `eol=lf`，防止行尾漂移 |
| `.gitignore` | 移除 `MVP/`、`diagram/`（与 `tests/` 同一类问题） |

### 接口改动

| 接口 | 改动 | 兼容性 |
|---|---|---|
| 工具执行器 | 统一返回 `ToolResult` | 内部接口收紧 |
| `ToolRegistry._normalize_output` | 删除，不再嗅探 `错误：` / `已取消` | 内部接口 |
| `TaskRunner` 私有方法 | `_execute_tool_batch` 内部拆分 | 不影响对外 |
| `Agent` / `CLI` | 不变 | 不变 |

### 技术选型

无新增依赖。

### 基线同步约定

每项验收通过后 `PROJECT.md` 升版（从 v1.45 起），并在本节追加“已同步至 PROJECT.md vX.Y”。
- H1 仅调整仓库约定（行尾、忽略规则、遗留文件），不改变架构、选型或接口，因此不升 `PROJECT.md` 版本。
- H2 把实现收紧到 `PROJECT.md` 早已要求的“工具返回结构化结果”，没有新增或改变基线契约，因此同样不升版本。
- H3 只在 `TaskRunner` 内部拆分方法，不改变任何对外接口或基线，因此不升版本。

## 4. 本版要实现的模块功能

> 按依赖顺序排，一项验收通过后才进入下一项。

### 执行顺序与理由

`H1 → H2 → H3`：先把仓库本身弄干净（H1 让后续提交不再依赖 `-f`），再收紧工具契约（H2 会改大量测试），最后动执行内核的拆分（H3 依赖 H2 之后更清晰的状态判断）。

---

- [x] **H1 · 仓库卫生**

  - 现象：`PROJECT.md` 与 `AGENTS.md` 是仓库里的 CRLF 文件，其余为 LF；`tests/test_workspace_arg.py.orig` 这类备份文件仍留在工作区；`MVP/`、`diagram/` 被忽略导致新文档必须 `git add -f`，漏加就会静默丢失（`tests/` 此前因此丢了 13 个文件）。
  - 根因：缺少 `.gitattributes` 约束；忽略规则与“文档/测试需要入库”的实际用法冲突。
  - 改动：
    1. 新增 `.gitattributes`：`* text=auto eol=lf`，并把 `PROJECT.md` 一次性规范为 LF。
    2. 从 `.gitignore` 移除 `MVP/`、`diagram/`，保留 `*.orig` 与缓存类规则。
    3. 删除 `tests/test_workspace_arg.py.orig`。
    4. 校验 `git ls-files` 与磁盘一致，无未跟踪的文档或测试。
  - 完成条件：`git status` 干净；`file MVP/PROJECT.md` 显示 LF；新增文件无需 `-f`；全量测试与 golden 通过。

- [x] **H2 · 工具输出统一为 `ToolResult`**

  - 现象：`registry._normalize_output` 靠 `错误：`、`已取消` 这两个中文前缀推断工具状态；工具返回字符串，状态语义不可靠，任何文案调整都可能改变判定。
  - 根因：工具层早期以字符串作为唯一返回类型，状态只能编码进文本。
  - 改动：
    1. `WorkspaceTools` 的 9 个方法与 `GitReadTools` 的 2 个方法统一返回 `ToolResult`。
    2. `ToolRegistry` 删除 `_normalize_output`；非 `ToolResult` 返回值直接判为失败（内部契约违约）。
    3. 更新依赖字符串返回的测试，统一通过 `.result` / `.error` 断言。
  - 完成条件：`grep -rn '_normalize_output' app/` 无结果；工具状态不再依赖文案；全量测试与 golden 通过。

- [x] **H3 · 工具批次执行再拆分**

  - 现象：`TaskRunner._execute_tool_batch` 585 行，单个方法内同时处理参数解析、重复写去重、预算检查、快照、执行、状态更新、恢复决策与 Trace 记录。
  - 根因：MVP-0.7 只按“阶段”拆分，没有继续拆“单次工具调用”的处理链。
  - 改动：
    1. 抽出 `_prepare_tool_call`：参数解析、重复写命中、重复创建重定向、调用预算、强制重跑拦截。
    2. 抽出 `_execute_tool_call`：约束校验、写前快照、注册表执行、变更集与 mutation 事件记录。
    3. 抽出 `_record_tool_call`：上下文、计划状态、恢复决策、`tool_call_finished` 与消息回填。
    4. `_execute_tool_batch` 只负责遍历调用、按结果决定继续或中断。
  - 完成条件：`_execute_tool_batch` 行数降到 150 行以内；`_run_legacy` 仍不存在；golden 6 条与全量测试通过。

## 5. 本版验收标准

- [x] **通用门槛**：每项完成后 `uv run pytest -q` 全绿、`uv run ruff check app tests` 输出 `All checks passed!`。
- [x] **行为等价**：`uv run pytest tests/test_runner_golden.py -q` 始终 6 passed，快照文件不得修改。
- [x] **H1**：`git status --short --untracked-files=all` 无输出；`file MVP/PROJECT.md` 不含 CRLF；`git check-ignore MVP/MVP-0.8.md` 返回非零。
- [x] **H2**：`grep -rn '_normalize_output' app/` 无结果；新增用例断言“返回字符串的执行器被判为失败”。
- [x] **H3**：`awk` 统计 `_execute_tool_batch` 行数 ≤ 150；四个辅助方法各有独立 docstring。
- [x] **交付纪律**：每项单独提交，每个改动文件单独 `git commit`，消息格式 `<类型>: <简短描述>`；复选框与基线版本只在验收通过后更新。H2 例外：工具契约迁移涉及 14 个文件，拆分后中间状态无法通过测试，因此合并为一次提交。

## 6. 本版风险与待办

- **风险 · H1 的忽略规则**：移除 `MVP/`、`diagram/` 忽略后，这两类文件会出现在 `git status` 里，需要显式提交。如果你希望继续忽略，请在验收时说明，我会改成“在文档中写明 `git add -f` 约定”而不是改规则。
- **风险 · H1 的行尾统一**：`PROJECT.md` 从 CRLF 改成 LF 会产生一次整文件 diff，这是唯一一次“看起来改了很多”的提交，之后不会再出现。
- **风险 · H2 的测试改动面**：大量断言从 `== "字符串"` 改为 `== ToolResult`，属于契约迁移而非放宽断言，每处都需保持原意。
- **已知行为修正**：目录删除中任一子项失败时，现在整体判为失败；此前会被 `_normalize_output` 当作成功（错误信息混在成功文本里）。这是契约收紧带来的修正，不是回归。
- **遗留 · `_apply_tool_result` 仍偏大**：拆分后它仍有 316 行，集中了变更记账、上下文、计划状态、恢复决策与 Trace 记录。它已是单一职责，但可再按“记账 / 恢复”拆成两个方法，留作后续小项。
- **风险 · H3 的搬迁风险**：`_execute_tool_batch` 是执行链路最密集的一段，必须靠 golden 与全量测试守住；拆分期间不允许修改 golden 快照。
- **待办 · 后续版本**：`run_tests` 的 30 秒超时与 `search_code` 的 10 秒超时参数化；`pyproject.toml` 版本号与 `PROJECT.md` 基线对齐并暴露 `--version`；`Agent` 的 `_run_step`（146 行）可在 H3 完成后同样按阶段细化。
