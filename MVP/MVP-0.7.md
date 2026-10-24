# MVP-0.7 · 执行内核重构与运行时配置化

> 基线：PROJECT.md v1.39
> 前置版本：MVP-0.6
> 日期：2026-10-24

## 1. 本版范围

本版**不新增用户可见能力**。目标是把执行内核从单个 1114 行的函数重构为可测试、可扩展的状态机，并解除三处会直接阻碍后续功能开发的耦合。

**行为保持承诺**：CLI 参数、任务报告字段、工具语义、恢复策略、Trace 事件名与 MVP-0.6 收官态一致。唯一的用户可见变化是配置文件新增 provider 列表格式（旧 `QWEN_*` 变量继续可用）。

包含 5 个交付项：

1. **R1 配置 provider 泛化与惰性加载** —— 解除“导入即校验”和配置写死 `QWEN_*` 的耦合。
2. **R2 测试结果结构化** —— 把被 5 个模块用正则解析的测试文本协议收敛为结构化对象。
3. **R3 授权入口收敛** —— 消除“删一个文件问两次”和两套并行授权机制。
4. **R4 工作区上下文注入** —— 去掉全局可变工作区，支持同进程多会话。
5. **R5 执行内核拆分** —— `_run_legacy` 拆为 `TaskRunner` + 显式状态机。

**不包含（留到 0.8）**：新增工具、记忆系统、评测模块、多 Agent 协作、报告字段扩展，以及 `.gitignore`、行尾统一等工程卫生项。

## 2. 本版用户路径

主路径不变：
> 用户输入任务 → CLI → Agent → 工具循环 → 完成判定 → 中文或 JSON 报告。

新增运维路径：
> 运维在 `.env` 配置多个 provider → Agent 按 provider 顺序选择模型 → 单个 provider 额度耗尽时先在内部故障转移，再切换到下一个 provider。

重构验收路径：
> 用固定 FakeModel 复现 MVP-0.6 的典型场景 → 重构前后 `TaskResult` 逐字段一致。

## 3. 相对上一版的增量

### 架构 / 模块改动

| 模块 | 改动 |
|---|---|
| `app/core/runner.py` | 新增：`TaskRunner` 与显式执行状态机 |
| `app/runtime/provider.py` | 新增：provider 配置模型与解析 |
| `app/tools/test_result.py` | 新增：`TestRunResult` 结构化测试结果 |
| `app/core/agent.py` | 收缩为对外接口与组装，删除 `_run_legacy` |
| `app/runtime/config.py` | 改为惰性加载 + provider 列表 |
| `app/tools/registry.py` | 持有 workspace 与授权器，声明工具能力类别 |
| `app/tools/tools.py` | 改为绑定 workspace 的实现，删除工具内二次确认 |
| `app/core/task_state.py` | 步骤匹配改用注入的能力解析器 |

### 接口改动

| 接口 | 改动 | 兼容性 |
|---|---|---|
| `ToolResult` | 新增 `data: Any = None`，不进入回传模型的 JSON | 向后兼容 |
| `ToolSpec` | 新增必填 `capability` | 内部接口 |
| `ToolRegistry` | 构造时接收 `workspace`、`authorizer` | 内部接口 |
| `WorkspaceContext` | 不再有模块级全局副本 | 内部接口 |
| `ModelAdapter` | 接收 provider 列表，保留 `models=` 兼容参数 | 向后兼容 |
| `Agent` | `run()`、`last_result`、`last_trace` 等公开属性不变 | 不变 |

### 技术选型

无新增第三方依赖。

### 基线同步约定

每项验收通过后 `PROJECT.md` 升版（从 v1.40 起），并在本节追加“已同步至 PROJECT.md vX.Y”。

## 4. 本版要实现的模块功能

> 按依赖顺序排，一项验收通过后才进入下一项。

### 执行顺序与理由

`R1 → R2 → R3 → R4 → R5`：前四项都是“把耦合切开”，风险低、单点可回退；R5 依赖前面切好的基础，放在最后做，避免在大函数里同时改耦合和改控制流。

---

- [ ] **R1 · 配置 provider 泛化与惰性加载**

  - 现象：仓库里没有 `.env` 时 `import app` 直接抛 `ValidationError`（`qwen_api_key`、`qwen_chat_model_1` 必填），新克隆无法收集测试；README 声称支持 `GPT_API_KEY`/`GPT_CHAT_MODEL`，但 `Settings` 没有这些字段且 `extra="ignore"` 会静默丢弃。
  - 根因：`app/runtime/config.py` 在模块级执行 `settings = Settings()`；字段名与单一 provider 绑定。
  - 改动：
    1. 新增 `ProviderConfig(name, base_url, api_key, models)` 与 `Settings.model_providers()`，支持 `MODEL_PROVIDER_n_*` 形式的配置。
    2. `QWEN_*` 作为兼容映射生成一个默认 provider，老配置继续可用。
    3. `get_settings()` 惰性加载并缓存，`Settings()` 不再在导入时构造。
    4. `ModelAdapter` 接收 provider 列表；跨 provider 顺序故障转移语义与现在一致（仅在额度、限流、模型缺失时切换）。
    5. `tests/conftest.py` 注入最小测试配置，保证无 `.env` 也能收集测试。
    6. `.env.example` 与 README 同步新格式。
  - 完成条件：无 `.env` 环境下 `import app` 与 `uv run pytest` 可用；现有模型故障转移测试全绿；`ModelAdapter(models=[...])` 仍然可用。

- [ ] **R2 · 测试结果结构化**

  - 现象：`run_tests` 输出“测试目标 / 测试退出码 / 测试结果 / 测试错误类型”四行文本，`app/cli/main.py`、`app/core/completion.py`、`app/core/test_evidence.py`、`app/tools/registry.py`、`app/tools/tools.py` 共 5 处依赖该文本协议。
  - 根因：工具只返回字符串，结构化信息只能编码进文本再被正则解析。
  - 改动：
    1. 新增 `TestRunResult(target, exit_code, status, error_kind, output)`。
    2. `ToolResult` 增加 `data` 字段；`to_dict()` / `to_json()` 不回传 `data`，模型仍只看到文本。
    3. `run_tests` 返回带 `data` 的 `ToolResult`。
    4. `completion`、`final_verification`、`test_evidence` 优先读 `data`；文本解析只保留在 `test_evidence.summarize_test_evidence()` 一处，用于从 Trace 事件恢复历史测试结果。
    5. `registry._normalize_output` 不再嗅探中文前缀判断状态。
  - 完成条件：测试状态判定不再对原始输出做正则；文本协议解析点从 5 处降到 1 处；全量测试通过并新增结构化用例。

- [ ] **R3 · 授权入口收敛**

  - 现象：删除一个文件要按两次 `y`（`registry` 的 HIGH 授权加 `tools._confirm_delete`）；非交互模式下两个授权器都默认拒绝，删除工具永远不可能成功；`RiskLevel.MEDIUM` 没有任何行为分支。
  - 根因：授权在两处实现，工具内部自建确认；风险等级只对 HIGH 定义了行为。
  - 改动：
    1. 授权只在 `ToolRegistry.execute()` 一处执行；删除工具内部的 `_confirm_delete`。
    2. `delete_directory` 改为“一次授权 + 在授权信息中列出将删除的完整路径清单”，不再逐项交互。
    3. 明确 MEDIUM 语义：同一任务写操作超过阈值（默认 20 个路径）时触发一次确认；若判定成本高于收益，则删除 MEDIUM 并简化 `TOOL_RISK_LEVELS`，不留装饰性字段。
    4. 非交互策略显式化：无 TTY 时 HIGH 操作默认拒绝，并在工具结果中说明原因。
  - 完成条件：删除单个文件只触发一次确认；非交互场景有明确结果与说明；授权决定记录进 Trace；相关测试更新并全绿。

- [ ] **R4 · 工作区上下文注入**

  - 现象：`app/tools/tools.py` 的 `WORKSPACE` / `WORKSPACE_CONTEXT` 是模块级可变全局，靠 `set_workspace()` 修改；`app/tools/__init__.py` 用 `globals()[...]` 手工同步；`_resolve_path()` 读取全局。
  - 根因：工具函数是无状态模块函数，工作区只能保存在模块全局。
  - 改动：
    1. `ToolRegistry` 构造时接收 `WorkspaceContext`；工具实现改为绑定实例方法或接收 context 的纯函数。
    2. 删除 `set_workspace()`、`WORKSPACE`、`WORKSPACE_CONTEXT`。
    3. 删除 `execute_tool()` 旧分发入口与 `app/__init__.py` 的 `__getattr__` 懒导出。
    4. `git_tools` 同步改造。
  - 完成条件：`grep -rn 'WORKSPACE_CONTEXT\|set_workspace' app/` 无结果；同进程两个不同工作区的 registry 并行执行互不影响（新增测试）；全量测试通过。

- [ ] **R5 · 执行内核拆分 TaskRunner + 状态机**

  - 现象：`Agent._run_legacy` 共 1114 行（当前 192–1305 行），同时负责 Trace 初始化、快照、复杂度判断、计划、上下文、安全会话、消息组装、工具批次执行、完成判定、三种恢复分支和收尾核验；其中授权回滚处理在完成判定分支与工具分支各写一遍，约 110 行重复。
  - 根因：控制流与状态更新写在同一函数，恢复路径靠复制代码实现。
  - 改动：
    1. 新增 `app/core/runner.py`：`TaskRunner` 持有 trace、plan、context、safety、state_manager，对外暴露 `run(task)`。
    2. 显式状态机：`Observe`（模型响应与工具结果）→ `Decide`（完成判定与恢复决策）→ `Act`（工具批次执行）→ `Recover`（重规划与回滚）→ `Finish`（最终核验与报告）。每个状态是独立方法，状态之间只通过数据结构传递。
    3. 回滚处理只保留一份实现，完成判定分支与工具分支共用。
    4. `Agent` 保留 `run()`、`last_result`、`last_trace`、`last_plan` 等对外属性，内部委托 `TaskRunner`。
  - 完成条件：`_run_legacy` 不再存在；`app/core/agent.py` 行数显著下降（目标 400 行以内）；全量测试通过（允许调整内部实现测试，不允许放宽断言）；行为等价性用例逐字段一致。

## 5. 本版验收标准

- [ ] **通用门槛**：每项完成后 `uv run pytest -q` 全绿、`uv run ruff check app tests` 输出 `All checks passed!`。
- [ ] **行为等价性**（R5 前置）：先新增 golden 用例，以固定 FakeModel 复现 6 个场景（正常完成、测试失败后修复、授权回滚、拒绝回滚、变更集异常、重规划上限），断言 `TaskResult.to_dict()` 全字段一致；该项必须在 R5 动手前完成。
- [ ] **R1**：删除 `.env` 后 `python -c "import app"` 与 `uv run pytest` 均可用；`MODEL_PROVIDER_1_*` 配置生效且旧 `QWEN_*` 仍可用。
- [ ] **R2**：`grep -rn '测试退出码：' app/` 只剩 `test_evidence` 与 `tools`（生成端），判定端不再解析文本。
- [ ] **R3**：交互式删除单个文件只出现一次 `y` 确认；非交互删除返回明确说明；`RiskLevel.MEDIUM` 要么有行为、要么被删除。
- [ ] **R4**：`grep -rn 'WORKSPACE_CONTEXT\|set_workspace\|execute_tool' app/` 无结果；新增并行工作区用例通过。
- [ ] **R5**：`grep -n '_run_legacy' app/` 无结果；`wc -l app/core/agent.py` 明显下降；golden 用例全绿。
- [ ] **交付纪律**：每项单独提交，每个改动文件单独 `git commit`，消息格式 `<类型>: <简短描述>`；复选框与基线版本只在验收通过后更新。

## 6. 本版风险与待办

- **风险 · R5 最大**：重构直接动 285 个测试保护的执行路径。缓解方式是先把行为快照（golden）测试立起来，再逐段搬迁；搬迁期间不允许改断言来迁就实现。
- **风险 · R2 的 `data` 字段**：绝不能进入回传模型的消息，否则上下文会塞入重复的结构化副本。`to_dict()` 必须显式排除。
- **风险 · R3 的 MEDIUM 语义**：如果无法给出低成本的明确行为，宁可删掉这个等级，也不要留下装饰性枚举值。
- **风险 · R1 的兼容期**：`QWEN_*` 映射需要明确弃用时间，否则两套配置会长期并存。
- **待办 · 工程卫生（留到 0.8）**：`.gitignore` 忽略 `tests/`、`MVP/`、`diagram/` 导致新增文件需 `git add -f`，与“每个文件单独提交”冲突；`PROJECT.md` 是仓库唯一 CRLF 文件；`tests/test_workspace_arg.py.orig` 为未跟踪残留。
- **待办 · 后续能力**：记忆系统、评测模块、多 Agent 协作在新内核上再做。
