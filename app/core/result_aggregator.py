"""从任务运行证据汇总稳定的 :class:`TaskResult`。"""

from __future__ import annotations

from typing import Any

from app.core.change_summary import build_change_summary
from app.core.completion import CompletionDecision, CompletionStatus
from app.core.context import TaskContext
from app.core.final_verification import FinalStateEvidence
from app.core.recovery import RecoveryDecision
from app.core.recovery_summary import summarize_recovery
from app.core.task_result import (
    FinalVerificationSummary,
    TaskResult,
    TaskStatus,
    TestEvidence,
    TestEvidenceStatus,
    ToolCallSummary,
)
from app.core.task_suggestions import build_task_suggestions
from app.core.test_evidence import summarize_test_evidence
from app.core.trace_summary import summarize_trace
from app.runtime.trace import ExecutionTrace
from app.tools.change_set import ChangeSetSummary


class TaskResultAggregator:
    """将运行时证据转换为一个不依赖模型总结的任务结果。"""

    def aggregate(
        self,
        context: TaskContext,
        changes: ChangeSetSummary,
        completion: CompletionDecision | None = None,
        trace: ExecutionTrace | None = None,
        recovery: RecoveryDecision | None = None,
        *,
        repair_attempts: int = 0,
        task_status: TaskStatus | None = None,
        final_evidence: FinalStateEvidence | None = None,
    ) -> TaskResult:
        """从任务运行时证据创建唯一、可序列化的任务结果。"""
        if not isinstance(context, TaskContext):
            raise TypeError("任务上下文必须是 TaskContext。")
        if not isinstance(changes, ChangeSetSummary):
            raise TypeError("变更摘要必须是 ChangeSetSummary。")
        if repair_attempts < 0:
            raise ValueError("自动修复次数不能小于 0。")

        if final_evidence is not None:
            changes = final_evidence.changes
        status = task_status or self._status(completion, trace)
        tests = (
            final_evidence.tests
            if final_evidence is not None
            else summarize_test_evidence(context.test_results)
        )
        verification_reason = self._verification_block_reason(status, final_evidence, tests)
        if verification_reason is not None:
            status = TaskStatus.BLOCKED
        stop_reason = verification_reason or self._stop_reason(completion, trace, status)
        recovery_records = summarize_recovery(trace, recovery)
        # restored_paths 表示恢复过的路径，不是未恢复路径；只有回滚事件的
        # pending_paths 才进入 unresolved，见 _recovery_records。
        unresolved = self._unresolved_paths(trace)
        changes_summary = build_change_summary(changes, unresolved)
        statistics = trace.get_statistics() if trace is not None else {}
        tool_calls = self._tool_calls(statistics.get("tool_calls", {}))
        return TaskResult(
            task=context.task,
            status=status,
            summary=self._summary(status, completion, changes, stop_reason),
            stop_reason=stop_reason,
            changes=changes_summary,
            tests=tests,
            recovery=recovery_records,
            repair_attempts=repair_attempts,
            model_request_count=(
                final_evidence.model_request_count
                if final_evidence is not None
                else int(statistics.get("model_requests", 0))
            ),
            tool_calls=(
                final_evidence.tool_calls
                if final_evidence is not None
                else tool_calls
            ),
            trace_event_count=(
                final_evidence.trace_event_count
                if final_evidence is not None
                else len(trace.events) if trace is not None else 0
            ),
            final_verification=(
                final_evidence.summary
                if final_evidence is not None
                else FinalVerificationSummary()
            ),
            next_steps=build_task_suggestions(status, stop_reason, changes_summary, tests),
            trace_references=summarize_trace(trace),
        )

    @staticmethod
    def _verification_block_reason(
        status: TaskStatus,
        final_evidence: FinalStateEvidence | None,
        tests: tuple[TestEvidence, ...],
    ) -> str | None:
        """完成状态缺少最终证据或最新测试未通过时降级为阻塞。"""
        if status is not TaskStatus.COMPLETED or final_evidence is None:
            return None
        if not final_evidence.summary.verified:
            return "最终状态核验未通过，存在未完成的工具调用。"
        latest_by_target = {item.target: item for item in tests}
        if any(
            item.status is not TestEvidenceStatus.PASSED
            for item in latest_by_target.values()
        ):
            return "最终状态核验发现最新测试证据未通过。"
        return None

    @staticmethod
    def _tool_calls(raw: Any) -> ToolCallSummary:
        """从 Trace 统计构造报告使用的工具调用计数。"""
        values = raw if isinstance(raw, dict) else {}
        return ToolCallSummary(
            calls_started=int(values.get("calls_started", 0)),
            calls_finished=int(values.get("calls_finished", 0)),
            succeeded=int(values.get("succeeded", 0)),
            failed=int(values.get("failed", 0)),
            cancelled=int(values.get("cancelled", 0)),
            unfinished=int(values.get("unfinished", 0)),
        )

    @staticmethod
    def _status(
        completion: CompletionDecision | None,
        trace: ExecutionTrace | None,
    ) -> TaskStatus:
        """根据完成判定和 Trace 终止事件确定任务状态。"""
        if completion is not None:
            if completion.status is CompletionStatus.COMPLETE:
                return TaskStatus.COMPLETED
            if completion.status is CompletionStatus.BLOCKED:
                return TaskStatus.BLOCKED
            return TaskStatus.STOPPED
        if trace is not None:
            for event in reversed(trace.events):
                if event["event"] == "task_failed":
                    return TaskStatus.FAILED
                if event["event"] == "task_stopped":
                    return TaskStatus.STOPPED
                if event["event"] == "task_completed":
                    return TaskStatus.COMPLETED
        return TaskStatus.STOPPED

    @staticmethod
    def _stop_reason(
        completion: CompletionDecision | None,
        trace: ExecutionTrace | None,
        status: TaskStatus,
    ) -> str | None:
        """提取任务未完成时可供用户处理的原因。"""
        if status is TaskStatus.COMPLETED:
            return None
        if completion is not None and completion.reason:
            return completion.reason
        if trace is not None:
            for event in reversed(trace.events):
                if event["event"] in {"task_failed", "task_stopped"}:
                    details = event.get("details", {})
                    return str(details.get("reason") or details.get("error") or "任务未完成")
        return "任务未完成。"

    @staticmethod
    def _summary(
        status: TaskStatus,
        completion: CompletionDecision | None,
        changes: ChangeSetSummary,
        stop_reason: str | None,
    ) -> str:
        """生成不依赖模型自由文本的简短结果摘要。"""
        if status is TaskStatus.COMPLETED:
            if completion is not None and completion.reason:
                return completion.reason
            return f"任务完成，产生 {len(changes.changes)} 个文件变更。"
        return stop_reason or "任务未完成，需根据停止原因继续处理。"

    @staticmethod
    def _unresolved_paths(trace: ExecutionTrace | None) -> tuple[str, ...]:
        """提取回滚后仍未恢复的工作区相对路径。"""
        if trace is None:
            return ()
        paths: list[str] = []
        for event in trace.events:
            if (
                event["event"] == "rollback_finished"
                and event.get("details", {}).get("status") in {"conflict", "failed"}
            ):
                paths.extend(str(path) for path in event.get("details", {}).get("pending_paths", ()))
        return tuple(dict.fromkeys(paths))


def aggregate_task_result(*args: Any, **kwargs: Any) -> TaskResult:
    """使用默认汇总器生成任务结果。"""

    return TaskResultAggregator().aggregate(*args, **kwargs)
