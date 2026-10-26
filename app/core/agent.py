"""Agent 对外入口：组装执行内核并生成结构化任务结果。"""

from collections.abc import Callable
from typing import Any

from app.core.completion import CompletionDecision, CompletionStatus
from app.core.context import ContextManager
from app.core.final_verification import FinalStateVerifier
from app.core.planner import TaskPlan
from app.core.recovery import RecoveryDecision
from app.core.result_aggregator import TaskResultAggregator
from app.core.runner import TaskRunner
from app.core.task_result import TaskResult, TaskStatus
from app.core.task_safety import TaskSafetySession
from app.runtime.model import ModelAdapter
from app.runtime.trace import ExecutionTrace
from app.runtime.workspace import WorkspaceContext
from app.tools.authorization import ToolAuthorizer
from app.tools.change_set import ChangeSetError, ChangeSetSummary
from app.tools.change_snapshot import SnapshotError
from app.tools.registry import ToolRegistry, build_default_registry


class Agent:
    """通过大模型和工具循环完成编程任务。"""

    def __init__(
        self,
        workspace: WorkspaceContext,
        max_steps: int = 20,
        max_tool_calls: int = 40,
        registry: ToolRegistry | None = None,
        debug: bool = False,
        event_listener: Callable[[dict[str, Any]], None] | None = None,
        authorizer: ToolAuthorizer | None = None,
    ) -> None:
        """绑定工作区、工具注册表与模型适配器。"""
        self.workspace = workspace
        self.registry = (
            registry
            if registry is not None
            else build_default_registry(workspace, authorizer=authorizer)
        )
        self.model = ModelAdapter()
        self.max_steps = max_steps
        self.max_tool_calls = max_tool_calls
        self.debug = debug
        self.event_listener = event_listener
        self.last_trace: ExecutionTrace | None = None
        self.last_plan: TaskPlan | None = None
        self.last_context: ContextManager | None = None
        self.last_change_set: Any = None
        self.last_recovery_decision: RecoveryDecision | None = None
        self.last_result: TaskResult | None = None
        self.last_safety: TaskSafetySession | None = None

    def run(self, task: str) -> str:
        """执行任务并生成结构化结果，同时兼容返回原始文本结果。

        变更集在被外部干扰破坏后无法安全分析（触碰路径变成符号链接、文件超过
        摘要上限、读取期间被改写）。这类异常不允许穿透 CLI，统一降级为可读的
        停止说明，并由任务结果层落成 ``blocked``。
        """
        self.last_result = None
        self.last_safety = None
        runner = TaskRunner(
            workspace=self.workspace,
            registry=self.registry,
            model=self.model,
            max_steps=self.max_steps,
            max_tool_calls=self.max_tool_calls,
            debug=self.debug,
            event_listener=self.event_listener,
        )
        try:
            answer = runner.run(task)
        except (ChangeSetError, SnapshotError) as exc:
            answer = f"变更集状态无法安全分析，任务已停止：{exc}"
            if runner.trace is not None:
                runner.trace.record("change_set_failed", error=str(exc))
                runner.trace.record(
                    "task_stopped",
                    reason="change_set_unavailable",
                    error=answer,
                )
        finally:
            self._sync_from_runner(runner)
            self._build_task_result(task)
            if self.last_trace is not None:
                self._print_trace_summary(self.last_trace)
        return answer

    def _sync_from_runner(self, runner: TaskRunner) -> None:
        """把执行内核的终态同步到对外属性，保持既有调用方兼容。"""
        self.last_trace = runner.trace
        self.last_plan = runner.plan
        self.last_context = runner.context
        self.last_change_set = runner.change_set
        self.last_recovery_decision = runner.recovery_decision
        self.last_safety = runner.safety

    def _debug_print(self, message: str) -> None:
        """仅在调试模式输出底层 Agent 和工具日志。"""
        if self.debug:
            print(message)

    def _print_trace_summary(self, trace: ExecutionTrace) -> None:
        """统一输出任务的 Trace 统计摘要。"""
        if self.debug:
            print("\n[Trace Summary]")
            print(trace.summary())

    def _build_task_result(self, task: str) -> None:
        """从本次 Agent 运行证据生成并保存 TaskResult。

        正常核验与核验失败共用同一份构造逻辑，只在证据和任务状态上不同，
        避免同一批报告字段在两处分别拼装后出现口径差异。
        """
        if (
            self.last_trace is None
            or self.last_context is None
            or self.last_change_set is None
        ):
            return

        final_evidence = None
        completion: CompletionDecision | None = None
        task_status: TaskStatus | None = None
        changes: ChangeSetSummary = ChangeSetSummary(
            touched_count=0, unchanged_count=0, changes=()
        )
        try:
            final_evidence = FinalStateVerifier().verify(
                self.workspace,
                self.last_change_set,
                self.last_trace,
            )
        except Exception as exc:  # noqa: BLE001 - reporting boundary
            reason = f"最终状态核验失败：{exc}"
            self.last_trace.record("final_state_verification_failed", error=str(exc))
            completion = CompletionDecision(CompletionStatus.BLOCKED, reason)
            task_status = TaskStatus.BLOCKED
        else:
            changes = final_evidence.changes
            if self._last_completion_status() == "blocked":
                task_status = TaskStatus.BLOCKED

        self.last_result = TaskResultAggregator().aggregate(
            self.last_context.context,
            changes,
            completion=completion,
            trace=self.last_trace,
            recovery=self.last_recovery_decision,
            repair_attempts=(
                self.last_safety.test_repair_attempts
                if self.last_safety is not None
                else 0
            ),
            task_status=task_status,
            final_evidence=final_evidence,
        )

    def _last_completion_status(self) -> str | None:
        """读取最近一次完成判定状态，供任务结果定级使用。"""
        if self.last_trace is None:
            return None
        for event in reversed(self.last_trace.events):
            if event["event"] == "completion_checked":
                return event.get("details", {}).get("status")
        return None
