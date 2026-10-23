# MVP-0.6 · 正确性修复与证据可信化

> 基线：PROJECT.md v1.35
> 前置版本：MVP-0.5
> 日期：2026-10-23

## 1. 本版范围

本版**不新增能力、不改变 CLI 用法**，只修正在“执行证据 → 任务结论”这条链路上会产出**错误结论**的缺陷，为后续版本（MVP-0.7 架构重构、模型 provider 泛化）建立可信基线。

包含 5 个交付项：

1. 修复 Git 只读工具被误判为计划步骤完成证据的问题，堵住完成判定被绕过的路径。
2. 修复测试成功输出中含失败关键词时被误判为阻塞的问题，让测试状态只由结构化字段决定。
3. 修复变更集异常穿透 Agent 主循环、导致任务崩溃且无结构化结果的问题。
4. 修复重规划没有次数上限、失败后反复重建计划和上下文却无法收敛的问题。
5. 清理本次触碰范围内的死代码（`rollback_denied`、`test_runs`）。

**不包含（明确留到后续版本）：**

- `_run_legacy` 拆分 TaskRunner + 状态机（已定为 MVP-0.7 主线）。
- 测试结果结构化 `TestRunResult`、授权机制收敛、去掉全局工作区（MVP-0.7）。
- 配置从 `QWEN_*` 泛化为 provider 列表、配置惰性加载（MVP-0.7）。
- Planner 归并阈值、`ContextManager` 未接入模型、4 份忽略清单、`.gitignore` 与跟踪冲突等 P2/P3 项（MVP-0.7/0.8）。

## 2. 本版用户路径

场景一（主路径）：
> 用户在 Git 仓库工作区提交“实现 X 并跑测试” → Agent 先查 Git 状态 → 实现代码 → 运行测试 → CLI 输出报告。
> 本版保证：查 Git 这一步**不会**把“实现”步骤标记为完成；报告里的完成状态必须同时有文件变更和真实测试证据支撑。

场景二（外部干扰）：
> 任务执行期间，某个被 Agent 触碰的文件被外部进程改成符号链接、超过 1MB 上限，或读取时正在被写入。
> 本版保证：Agent 不会抛异常崩掉，而是以 `blocked` 结束并给出可读原因，同时保留已完成的变更证据。

场景三（失败收敛）：
> 任务反复失败，控制层先重规划再修复，多次仍不收敛。
> 本版保证：重规划次数达到上限后停止，而不是烧完 `max_steps` 预算。

## 3. 相对上一版的增量

### 架构 / 模块改动

不新增模块。改动集中在 5 个文件的判定逻辑：

| 文件 | 改动性质 |
|---|---|
| `app/tools/capability.py` | 新增：工具能力类别枚举与映射（工具元数据归属工具层） |
| `app/core/task_state.py` | 改：步骤匹配与完成核验改为按能力类别判定 |
| `app/core/completion.py` | 改：测试状态改由结构化证据决定，删除原文关键词扫描 |
| `app/core/recovery.py` | 改：新增重规划次数上限信号与策略分支 |
| `app/core/task_safety.py` | 改：记录重规划次数；删除只写不读的 `test_runs` |
| `app/core/agent.py` | 改：主循环兜住变更集异常；应用重规划计数；删除死变量 |

### 接口改动

| 接口 | 改动 | 兼容性 |
|---|---|---|
| `PlanStateManager.match_step()` | 未知工具不再返回兜底步骤，一律返回 `None` | 行为收紧，签名不变 |
| `PlanStateManager.can_complete_step()` | 未知工具一律返回 `False` | 行为收紧，签名不变 |
| `CompletionVerifier.verify()/_basic()` | 测试状态只读 `测试退出码` 与 `测试结果` 字段 | 输入输出不变 |
| `RecoverySignals` | 新增 `replan_attempts: int = 0` | 向后兼容 |
| `RecoveryPolicy.__init__` | 新增 `max_replan_attempts: int = 3` | 向后兼容 |
| `TaskSafetySession` | 新增 `note_replan()`；移除 `test_runs` 属性 | 内部接口 |
| `Agent.run()` | 变更集异常时返回停止说明而不是抛出 | 行为修正 |

### 基线同步约定

- 本版属于**接口/约定变更**，每个交付项**验收通过后**同步更新 `PROJECT.md`，从 v1.36 起按验收顺序递增，并在本节追加一行“已同步至 PROJECT.md vX.Y”。
- `PROJECT.md` 第 4 节需同步的条目：`Planner`（步骤状态由工具能力、目标路径和完成条件维护）、`完成判定`（测试状态取值口径）、`失败恢复`（重规划上限）、`Agent 任务结果`（异常降级为 blocked）。
- 已同步至 PROJECT.md v1.36：工具能力类别成为步骤匹配与完成核验的唯一依据，只读 Git 查询和未登记工具不参与任何步骤状态流转（对应 D1）。

### 技术选型改动

无。

## 4. 本版要实现的模块功能

> 按依赖顺序排，勾选代表已完成。**一次只交付一项，用户验收通过后才进入下一项。**

### 交付节奏

1. 每项开工前先跑 `uv run pytest -q` 与 `uv run ruff check app tests`，确认基线是绿的。
2. 每项完成后补回归测试，再跑全量测试与 Ruff。
3. 每项单独提交，遵循 `每个文件单独提交` 与 `<类型>: <简短描述>` 格式。
4. 提交后由用户验收（看改动 + 跑验收命令），通过后勾选复选框、同步 `PROJECT.md`、进入下一项。

---

- [x] **D1 · 工具能力类别与步骤匹配收口**

  - 现象：`git_status` / `git_diff` 能把“重构支付模块”这类实现步骤标记为 `completed`。模型只要开工前查一次 Git，实现步骤就变完成，配合“无失败工具结果”就可能整体判 `completed`，而工作区零文件变更。
  - 根因：`app/core/task_state.py:96` 对未识别工具兜底 `score += 1`，`:118` 对未识别工具直接 `return True`；而 `git_status`/`git_diff` 不在 `known_tools` 集合内。
  - 改动：
    1. 新增 `app/tools/capability.py`，定义 `ToolCapability`（`READ` / `MUTATE` / `TEST` / `VCS` / `UNKNOWN`）与 `capability_of(tool_name)`。
    2. `match_step()` 改为按能力类别打分；`UNKNOWN` 返回 `None`，不匹配任何步骤。
    3. `can_complete_step()` 改为按能力类别判定；`UNKNOWN` 返回 `False`。保持现有语义：`create_directory` 不单独完成实现/测试步骤，`tests/` 路径只匹配测试编写步骤，实现步骤需等待后续测试阶段。
    4. `VCS` 类工具只产生执行证据，不参与任何步骤状态流转。
  - 完成条件：`git_status`/`git_diff` 对任意计划步骤的 `match_step` 均为 `None`、`can_complete_step` 均为 `False`；原有步骤匹配测试全部仍通过。

- [ ] **D2 · 测试状态判定只读结构化字段**

  - 现象：一条 `测试退出码：0` + `测试结果：通过` 的通过结果，只要输出里出现 `error` / `失败` / `错误` 字样就被判 `blocked`。用例名、`print` 输出、pytest warning 都可能触发。
  - 根因：`app/core/completion.py:54` 的 `_FAILURE_PATTERN` 在**整段原始输出**上扫关键词，且 `:136`、`:198` 先判失败、后判成功，成功判定只是兜底。
  - 改动：
    1. 删除 `_FAILURE_PATTERN` / `_SUCCESS_PATTERN` 对原始输出的关键词扫描。
    2. 复用 `app/core/test_evidence.py` 已有的结构化解析（`测试退出码` / `测试结果` / `测试错误类型`）作为唯一事实来源。
    3. 测试历史折叠统一走 `latest_test_evidence()`，保持“父目标成功覆盖子目标历史失败”的既有语义。
    4. 明确冲突优先级：退出码非 0 **或** `测试结果：失败` → `FAILED`；两者都没有 → 沿用现有“未标识测试”行为。
  - 完成条件：含 `error` 字样的通过结果判 `complete`；真实失败判 `blocked`；原有 `test_completion.py`、`test_test_evidence.py`、`test_test_failure_status.py` 全部通过。

- [ ] **D3 · 变更集异常的主循环降级**

  - 现象：`change_set.summary()` 抛出 `ChangeSetError` 时异常直接穿透 `Agent.run()`，CLI 只打印“程序发生异常”，`agent.last_result` 为空，用户拿不到任何结构化结论。
  - 根因：`app/core/agent.py:890`、`:1065`、`:535`、`:1167` 在 `try` 之外裸调 `summary()`；触发条件包括触碰文件变成符号链接、超过 1MB、读取期间被改动。
  - 改动：
    1. 在 `Agent.run()` 主循环出口捕获 `ChangeSetError` / `SnapshotError`，记录 `change_set_failed` 与 `task_stopped` 两个 Trace 事件。
    2. 返回可读的中文停止说明，不再向上抛异常。
    3. 依赖 `_build_task_result()` 已有的异常分支把结果降级为 `blocked`，并与正常分支共用同一份 `TaskResult` 构造逻辑。
  - 完成条件：构造出 `ChangeSetError` 的场景下，`Agent.run()` 正常返回字符串、`last_result.status == blocked`、`stop_reason` 含可读原因、Trace 中能找到对应事件。

- [ ] **D4 · 重规划次数上限生效**

  - 现象：任务失败后反复“重规划 → 再失败 → 再重规划”，每次都重扫项目、重建计划和上下文，直到 `max_steps` 才停。`RecoveryPolicy` 的 `max_recovery_attempts` 看起来是收敛保护，实际只管回滚轮次。
  - 根因：`app/core/task_safety.py:396` 只在回滚成功后递增 `recovery_attempts`；`REPLAN` 分支从不递增，`recovery.py:90` 的上限判断对重规划无效。
  - 改动：
    1. 保留 `recovery_attempts` 的“回滚轮次”语义不变，避免影响既有“两次修复后建议回滚”流程。
    2. 新增 `replan_attempts` 信号与 `max_replan_attempts`（默认 3）；`TaskSafetySession.note_replan()` 在 Agent 实际应用重规划时递增。
    3. `RecoveryPolicy.decide()` 在返回 `REPLAN` 前检查上限，超限返回 `STOP`，原因明确为“重规划次数已达上限”。
  - 完成条件：连续触发重规划达到上限后决策变为 `STOP`；既有“两次测试修复 → 建议回滚”流程行为不变；`test_recovery.py`、`test_agent_recovery.py`、`test_mvp04_integration.py` 全部通过。

- [ ] **D5 · 死代码清理**

  - 现象：`rollback_denied` 置 `True` 后下一行立即 `return`，两处 `if rollback_denied:` 永远不成立；`test_runs` 只写不读。
  - 根因：`app/core/agent.py:240/485/518/1114/1147`；`app/core/task_safety.py:48/298/399`。
  - 改动：删除上述死变量与相关分支；确认无测试依赖后清理。
  - 完成条件：`grep -rn 'rollback_denied\|test_runs' app/` 无结果；Ruff 通过；全量测试通过。

## 5. 本版验收标准

> 下列命令在项目根目录执行；`PATH` 需包含 `~/.local/bin`（uv 安装位置）。

- [ ] **通用门槛**：每个交付项完成后 `uv run pytest -q` 全绿（当前基线 269 passed），`uv run ruff check app tests` 输出 `All checks passed!`。
- [x] **D1**：以下命令输出 `ok`，且新增回归测试断言 `git_status`/`git_diff` 无法开始或完成任何类型的计划步骤（实现 / 测试编写 / 测试执行 / 阅读）。

  ```bash
  uv run python -c "
  from app.core.planner import create_task_plan
  from app.core.task_state import PlanStateManager
  task='重构支付模块并迁移配置，先阅读现有代码，然后实现新接口，同时编写测试，最后运行全部测试验证。'
  plan=create_task_plan(task)
  for name in ('git_status','git_diff'):
      assert PlanStateManager(plan).match_step(name, {'path':'.'}) is None, name
  print('ok')"
  ```

- [ ] **D2**：以下命令输出 `ok`，且新增回归测试覆盖“通过但含 error 字样”“退出码 0 但结果字段为失败”“真实断言失败”三种输入。

  ```bash
  uv run python -c "
  from app.core.completion import CompletionVerifier
  from app.tools.result import ToolResult
  v=CompletionVerifier().verify_basic
  ok='测试目标：tests/test_x.py\n测试退出码：0\n测试结果：通过\n1 passed\n注意：error handling 已覆盖\n'
  assert v([ToolResult.success('ok')],[ok]).status.value=='complete'
  bad='测试目标：tests/test_y.py\n测试退出码：1\n测试结果：失败\nassert 1 == 2\n'
  assert v([ToolResult.success('ok')],[bad]).status.value=='blocked'
  print('ok')"
  ```

- [ ] **D3**：新增集成测试构造 `ChangeSetError` 场景，断言不抛异常、`last_result.status == blocked`、`stop_reason` 非空、Trace 含 `change_set_failed`。
- [ ] **D4**：新增单元测试断言连续重规划达上限后动作为 `STOP`；集成测试断言 Agent 不会超出重规划上限继续重建计划。
- [ ] **D5**：`grep -rn 'rollback_denied\|test_runs' app/` 无输出。
- [ ] **文档同步**：每项验收后 `PROJECT.md` 升版并在第 6 节变更记录追加一行；本文件第 3 节追加“已同步至 PROJECT.md vX.Y”。
- [ ] **交付纪律**：每项单独提交，每个改动文件单独 `git commit`，消息格式 `<类型>: <简短描述>`；复选框只在验收通过后勾选。

## 6. 本版风险与待办

- **风险 · D1 语义收紧**：`match_step` 不再给未知工具兜底分后，未来新增工具若未登记能力类别会“静默不匹配”，表现为步骤永远推进不了。已在 `capability_of()` 中对未登记工具返回 `UNKNOWN` 并由回归测试固定该行为；MVP-0.7 应改为注册工具时强制声明能力类别，缺失即启动失败。
- **风险 · D2 口径变化**：删除关键词扫描后，任何“退出码为 0 但输出里确实有失败信息”的异常情况将不再被判为失败。当前 `run_tests` 的退出码由 pytest 真实返回，可信；若后续接入非 pytest runner，必须保证其退出码语义一致。
- **风险 · D3 降级粒度**：变更集异常时报告只能给出 `blocked` 与原因，无法区分“哪个路径出错”之外的信息；不追求自动修复，避免在证据不可信时继续动作。
- **风险 · D4 计数位置**：`replan_attempts` 必须在“实际重建计划”时递增，而不是在决策产生时递增，否则会被未应用的决策污染；回归测试需覆盖这一点。
- **待办 · 后续版本**：`_run_legacy` 拆分（MVP-0.7 主线）、测试结果结构化、授权机制收敛、去除全局工作区、配置 provider 泛化与惰性加载、`.gitignore` 与文档跟踪冲突、`Planner` 归并阈值、`ContextManager` 接入模型上下文。
- **已知摩擦**：`.gitignore` 忽略了 `MVP/`、`diagram/`、`tests/`，本文件为新增文件，提交时需 `git add -f`；该问题本身列入 MVP-0.7 处理。
