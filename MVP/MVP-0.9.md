# MVP-0.9 · 计划契约与完成判定可信性

> 基线：PROJECT.md v1.44
> 前置版本：MVP-0.8
> 日期：2026-10-24

## 1. 本版范围

本版修复一次真实运行暴露的 P0 缺陷，并顺手把“计划”从一个字符串数组升级为**有类型、可校验的契约**。

1. **C1 计划契约与可执行性校验** —— 计划拆成「意图 / 验收 / 步骤」三层；步骤必须可执行，验收项必须可核验。
2. **C2 验收条件必须被证据满足** —— 用户要求“测试通过”时，必须真的有通过的测试证据，而不是“创建了测试文件”。
3. **C3 终止事件与停止原因完整** —— 任务结束必须有终止事件；`stop_reason` 不允许退化成“任务未完成。”。

**原则**：本版**不引入模型规划**。计划仍由代码生成，模型只负责执行。模型参与规划（意图抽取 / 审查 Agent）留到 0.10 再评估。

**不包含**：模型 Planner、审查 Agent、非交互删除授权、空转收敛、Git 报告口径、报告新增验收段落。

## 2. 复现场景（本版的验收基准）

```bash
uv run coding-agent --workspace ./workspace/trace_probe --debug --max-steps 25 --max-tool-calls 60
```

任务文本：

> 实现 calculator.py，提供 add(a, b) 和 divide(a, b) 两个函数；divide 在 b 为 0 时抛出 ZeroDivisionError。然后编写 tests/test_calculator.py 覆盖这两个函数，最后运行测试确认通过。

修复前的事实：第 5 轮（19 秒）测试已通过、工作区正确，但计划第 2 步「完成：divide 在 b 为 0 时抛出 ZeroDivisionError」对全部工具 `match_step` 返回 `None`，永远 `pending`；完成判定永远 `incomplete`；空转 19 轮后以 `blocked` 结束，`stop_reason` 为“任务未完成。”。

本版目标：同一任务 ≤8 轮、`completed`、`stop_reason` 为空。

## 3. 相对上一版的增量

### 数据模型（新增）

```python
# app/core/intent.py
class EvidenceKind(StrEnum):
    FILE_EXISTS = "file_exists"          # 路径存在
    FILE_CHANGED = "file_changed"        # 相对任务初始状态有净变化
    TEST_PASSED = "test_passed"          # 指定测试目标有通过证据
    CONTENT_CONTAINS = "content_contains"  # 源码包含指定符号
    WORKSPACE_READ = "workspace_read"    # 至少一次成功的读取/搜索

@dataclass(frozen=True)
class AcceptanceItem:
    item_id: int
    text: str                 # 人类可读描述
    kind: EvidenceKind
    target: str | None = None # 路径 / 测试目标 / 符号
    required: bool = True

@dataclass(frozen=True)
class TaskIntent:
    objective: str
    scope: tuple[str, ...]          # 候选路径：仅供参考，不参与完成判定
    out_of_scope: tuple[str, ...]   # 明确不做
    acceptance: tuple[AcceptanceItem, ...]
    open_questions: tuple[str, ...] # 只装“必须二选一”的问题
```

`TaskPlan` 增加 `intent` 字段；`PlanStep` 保持不变（`objective` / `completion_criteria` / `status`），但新增 `acceptance_ids`，用于把步骤绑定到验收项。

### 模块改动

| 模块 | 改动 |
|---|---|
| `app/core/intent.py`（新增） | `TaskIntent` / `AcceptanceItem` / 从任务文本抽取意图与验收项 |
| `app/core/plan_lint.py`（新增） | 计划可执行性校验：动作词、工具覆盖、去重、验收覆盖、步数上限 |
| `app/core/acceptance.py`（新增） | 按 `EvidenceKind` 核验验收项 |
| `app/core/planner.py` | 生成 `intent`，步骤从验收项派生，调用 linter |
| `app/core/completion.py` | 完成判定除步骤外，还要求 required 验收项全部满足 |
| `app/core/runner.py` | 记录 `plan_linted` / `intent_extracted` 事件；C3 终止事件 |
| `app/core/result_aggregator.py` | 无终止事件时用最近完成判定原因兜底；`stop_reason` 附待处理步骤号 |

### 接口改动

| 接口 | 改动 | 兼容性 |
|---|---|---|
| `TaskPlan` | 新增 `intent` | 内部接口 |
| `PlanStep` | 新增 `acceptance_ids` | 内部接口 |
| `CompletionVerifier.verify` | 新增 `acceptance` 结果参数 | 内部接口 |
| `create_task_plan` | 返回带 lint 结果的计划；不合法步骤被合并并记录 | 行为修正 |

### 基线同步约定

每项验收通过后 `PROJECT.md` 升版（从 v1.45 起），并在本节追加“已同步至 PROJECT.md vX.Y”。
- 已同步至 PROJECT.md v1.45：计划升级为意图 / 验收项 / 步骤三层，并加入可执行性校验（L1–L6）（对应 C1）。
- 已同步至 PROJECT.md v1.46：新增验收项证据核验，完成判定要求必需验收项全部满足（对应 C2）。
- 已同步至 PROJECT.md v1.47：回滚被拒记录终止事件，停止原因分级兜底并附待处理步骤（对应 C3）。
- 已同步至 PROJECT.md v1.48：简单任务同样执行验收核验，只读路径不生成文件存在验收项（对应 C4）。

## 4. 本版要实现的模块功能

> 按依赖顺序排，一项验收通过后才进入下一项。

- [x] **C1 · 计划契约与可执行性校验**

  - 现象：计划第 2 步是一个需求细节，不含动作词，任何工具都无法匹配；它永远 `pending`，把任务锁死在 `incomplete`。
  - 根因：`TaskPlanner._extract_objectives` 按标点切分后直接逐句成步；计划里没有“这条能不能执行”的概念，也没有“什么证据算完成”的类型。
  - 改动：
    1. **意图抽取**（代码，确定性）：
       - `objective`：任务首句或去编号后的完整任务；
       - `scope`：任务中显式出现的 `.py` 路径（候选，不参与判定）；
       - `out_of_scope`：任务中“不要/不得/禁止 + 动作”的片段；
       - `acceptance`：按规则生成 `AcceptanceItem`——提到 pytest/运行测试/测试通过 → `TEST_PASSED`；显式 `.py` 路径 → `FILE_EXISTS`；提到 argparse/unittest/异常类名 → `CONTENT_CONTAINS`；提到阅读/查看 → `WORKSPACE_READ`；
       - `open_questions`：只装“必须由用户二选一”的问题（互斥要求、缺失关键输入）；可推断的缺失不算。
    2. **步骤派生**：按验收项归并成阅读 / 实现 / 编写测试 / 运行测试阶段，每步带 `acceptance_ids`。
    3. **计划校验**（`plan_lint.py`）：
       - L1 含动作词（实现/修改/创建/新增/删除/修复/重构/编写/补充/运行/验证/阅读/查看/搜索）；
       - L2 能映射到工具能力（MUTATE / TEST / READ）；
       - L3 与其它步骤不重复；
       - L4 每个 required 验收项至少被一个步骤覆盖（未覆盖则追加阶段步骤）；
       - L5 步骤数 ≤ 5；
       - L6 不触碰 `out_of_scope` 路径。
       不合格步骤并入前一步（无前一步则并入后一步）；全部不合格时回退到四阶段模板。被合并/丢弃的原文写入 Trace 的 `plan_pruned`。
    4. **待确认策略**：`open_questions` 非空且运行环境非交互时，在执行任何工具前停止，`stop_reason` 直接给出问题；交互模式通过 CLI 询问。
  - 完成条件：真实任务生成的计划不再包含无动作词步骤；`intent.acceptance` 至少包含 `TEST_PASSED` 与两个 `FILE_EXISTS`；Trace 出现 `intent_extracted`；`plan_lint` 每条规则都有单元测试。

- [x] **C2 · 验收条件必须被证据满足**

  - 现象：步骤目标写着“最后运行测试确认通过”，但只创建测试文件就被标记完成，机制上允许“没跑过测试也算完成”。
  - 根因：完成判定只看步骤状态、工具结果和测试结果，没有一个“用户要求的验收条件”清单去逐条核对。
  - 改动：
    1. 新增 `AcceptanceVerifier`，按 `EvidenceKind` 取证：
       - `FILE_EXISTS`：工作区中该路径存在；
       - `FILE_CHANGED`：变更集摘要中该路径有净变化；
       - `TEST_PASSED`：该测试目标的最新证据为通过（复用现有 `latest_test_evidence`）；
       - `CONTENT_CONTAINS`：源码包含指定符号（复用 `TaskRequirementVerifier` 的读取逻辑）；
       - `WORKSPACE_READ`：上下文中存在成功的读取/搜索工具结果。
    2. `CompletionVerifier` 在原有规则之外，要求所有 `required` 验收项均已满足；未满足返回 `INCOMPLETE` 并列出缺失项。
    3. 计划里的“运行测试”阶段步骤仍保留，但不再是测试通过的唯一依据。
  - 完成条件：构造“有测试文件但没跑测试”的场景，完成判定为 `INCOMPLETE` 且缺失项为 `TEST_PASSED`；真实任务中测试通过后判定为 `COMPLETE`。

- [x] **C4 · 简单任务也走验收**

  - 现象：验收核验只对有计划的复杂任务生效；被判为简单、又没有计划的短任务仍走 `verify_basic`，只要求“有成功证据”，不要求测试真的跑过。
  - 改动：意图抽取提到计划之外，`TaskRunner` 始终持有意图；`verify_basic` 增加验收参数，未满足必需验收项时返回 `incomplete`。
  - 完成条件：简单任务“运行测试确认通过”但只做了读取时判为 `incomplete`；简单只读任务（无测试要求）不受影响。

- [x] **C3 · 终止事件与停止原因完整**

  - 现象：回滚被拒后 Trace 最后一条是 `user_confirmation_required`，`task_completed/task_failed/task_stopped` 计数全为 0，报告写“未完成原因：任务未完成。”。
  - 根因：回滚取消分支直接返回，不记终止事件；聚合器找不到终止事件时退化为固定文案。
  - 改动：
    1. `_rollback_and_replan` 的取消与失败分支记录 `task_stopped`，reason 使用面向用户的中文原因。
    2. `TaskResultAggregator` 无终止事件时，退回最近一次 `completion_checked` 的原因；仍为空才用兜底文案。
    3. `stop_reason` 在完成判定为 `incomplete` 时追加待处理步骤编号，例如“仍有必要计划步骤未完成（step 2）”。
  - 完成条件：回滚取消场景 Trace 含 `task_stopped`；报告不再出现“任务未完成。”作为唯一原因；新增单元测试覆盖兜底顺序。

## 5. 本版验收标准

- [x] **通用门槛**：每项完成后 `uv run pytest -q` 全绿、`uv run ruff check app tests` 输出 `All checks passed!`。
- [x] **行为等价**：`uv run pytest tests/test_runner_golden.py -q` 保持 6 passed；C1–C2 不得修改快照，C3 有意更新 `rollback_denied` 的停止原因，需在验收时逐项审阅差异。
- [x] **真实复跑**：用第 2 节命令与任务重跑，要求状态 `completed`、`stop_reason` 为空、模型请求 ≤8 次、工作区 `pytest` 通过。
- [x] **C1**：`create_task_plan(真实任务)` 无无动作词步骤；`intent.acceptance` 含 `TEST_PASSED` 与两个 `FILE_EXISTS`；lint 六条规则各有用例。
- [x] **C2**：“只建测试文件不跑测试”判为 `INCOMPLETE`；“跑通测试”判为 `COMPLETE`。
- [x] **C4**：简单任务在缺少测试证据时判为 `incomplete`；只读任务不受影响。
- [x] **C3**：回滚取消路径产生 `task_stopped`；`stop_reason` 兜底与步骤号有单元测试。
- [x] **交付纪律**：每项单独提交，每个改动文件单独 `git commit`，消息格式 `<类型>: <简短描述>`；复选框与基线版本只在验收通过后更新。

## 6. 本版风险与待办

- **风险 · 验收项抽取误判**：任务说“优化一下”但没提测试时，不应强行要求 `TEST_PASSED`。规则是**只从任务文本明确出现的要求**生成 required 验收项，推断出的内容不设 required。
- **已知行为变更 · C3**：回滚被拒的停止原因从“任务未完成。”变为真实原因，`tests/golden/rollback_denied.json` 已同步更新；其余快照保持不变。
- **风险 · 待确认导致频繁停止**：`open_questions` 只装“必须二选一”的问题，正常任务应为空；若实际频繁触发，需要收紧判定条件而不是取消策略。
- **风险 · 步骤合并隐藏真实工作**：被合并的原文必须写入 Trace，报告摘要保持步骤数不变。
- **待办 · 0.10 模型参与规划**：在 C1 的 linter 之上接入“模型只产意图/验收、代码产步骤”的方案，并 A/B 对比轮次与完成率；审查 Agent 只在模型规划开启时启用，最多 1 轮、只做减法。
- **待办 · 非交互删除**：清理本任务创建的文件被授权拒绝，`cancelled` 结果永久阻塞完成判定。
- **待办 · 空转收敛**：连续 N 轮无步骤推进的保护。
- **待办 · Git 报告口径**：工作区被 gitignore 时“Git 变更 0 个”与“创建 3 个文件”并列容易误读。
