"""从任务运行证据汇总稳定的 :class:`TaskResult`。"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from app.core.completion import CompletionDecision, CompletionStatus
from app.core.context import TaskContext
from app.core.recovery import RecoveryDecision
from app.core.task_result import (
    ChangeSummary,
    RecoveryRecord,
    TaskResult,
    TaskStatus,
    TestEvidence,
    TestEvidenceStatus,
)
from app.runtime.trace import ExecutionTrace
from app.tools.change_set import ChangeSetSummary

_TEST_TARGET = re.compile(r"测试目标：([^\n]+)")
_TEST_EXIT = re.compile(r"测试退出码：(-?\d+)")
_TEST_ERROR = re.compile(r"测试错误类型：([^\n]+)")


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
    ) -> TaskResult:
        if not isinstance(context, TaskContext):
            raise TypeError("任务上下文必须是 TaskContext。")
        if not isinstance(changes, ChangeSetSummary):
            raise TypeError("变更摘要必须是 ChangeSetSummary。")
        if repair_attempts < 0:
            raise ValueError("自动修复次数不能小于 0。")

        status = task_status or self._status(completion, trace)
        stop_reason = self._stop_reason(completion, trace, status)
        recovery_records = self._recovery_records(trace, recovery)
        # restored_paths 表示恢复过的路径，不是未恢复路径；只有回滚事件的
        # pending_paths 才进入 unresolved，见 _recovery_records。
        unresolved = self._unresolved_paths(trace)
        return TaskResult(
            task=context.task,
            status=status,
            summary=self._summary(status, completion, changes),
            stop_reason=stop_reason,
            changes=ChangeSummary(
                created=changes.created_paths,
                modified=changes.modified_paths,
                deleted=changes.deleted_paths,
                unresolved=unresolved,
            ),
            tests=tuple(self._tests(context.test_results)),
            recovery=tuple(recovery_records),
            repair_attempts=repair_attempts,
            trace_event_count=len(trace.events) if trace is not None else 0,
        )

    @staticmethod
    def _status(
        completion: CompletionDecision | None,
        trace: ExecutionTrace | None,
    ) -> TaskStatus:
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
    ) -> str:
        if completion is not None and completion.reason:
            return completion.reason
        if status is TaskStatus.COMPLETED:
            return f"任务完成，产生 {len(changes.changes)} 个文件变更。"
        return "任务未完成，需根据停止原因继续处理。"

    @staticmethod
    def _tests(results: Sequence[str]) -> list[TestEvidence]:
        evidence: list[TestEvidence] = []
        for raw in results:
            if not isinstance(raw, str) or not raw.strip():
                continue
            target = _TEST_TARGET.search(raw)
            exit_code = _TEST_EXIT.search(raw)
            error = _TEST_ERROR.search(raw)
            target_name = target.group(1).strip() if target else "未标识测试"
            code = int(exit_code.group(1)) if exit_code else None
            failed = code not in (None, 0) or "测试结果：失败" in raw or raw.startswith("错误：")
            evidence.append(
                TestEvidence(
                    target=target_name,
                    status=TestEvidenceStatus.FAILED if failed else TestEvidenceStatus.PASSED,
                    exit_code=code,
                    error_kind=error.group(1).strip() if error else None,
                    output=raw,
                )
            )
        return evidence

    @staticmethod
    def _recovery_records(
        trace: ExecutionTrace | None,
        decision: RecoveryDecision | None,
    ) -> list[RecoveryRecord]:
        records: list[RecoveryRecord] = []
        if decision is not None:
            records.append(
                RecoveryRecord(
                    action=decision.action.value,
                    reason=decision.reason,
                    restored_paths=decision.recoverable_paths,
                )
            )
        if trace is None:
            return records
        for event in trace.events:
            details = event.get("details", {})
            if event["event"] == "recovery_decided":
                records.append(
                    RecoveryRecord(
                        action=str(details.get("action", "unknown")),
                        reason=str(details.get("reason", "恢复决策")),
                        restored_paths=tuple(details.get("recoverable_paths", ())),
                    )
                )
            elif event["event"] == "rollback_finished":
                records.append(
                    RecoveryRecord(
                        action=f"rollback:{details.get('status', 'unknown')}",
                        reason=str(details.get("reason", "回滚完成")),
                        restored_paths=tuple(details.get("restored_paths", ())),
                    )
                )
        return records

    @staticmethod
    def _unresolved_paths(trace: ExecutionTrace | None) -> tuple[str, ...]:
        if trace is None:
            return ()
        paths: list[str] = []
        for event in trace.events:
            if event["event"] == "rollback_finished":
                paths.extend(str(path) for path in event.get("details", {}).get("pending_paths", ()))
        return tuple(dict.fromkeys(paths))


def aggregate_task_result(*args: Any, **kwargs: Any) -> TaskResult:
    """使用默认汇总器生成任务结果。"""

    return TaskResultAggregator().aggregate(*args, **kwargs)
