"""汇总恢复决策、授权和回滚结果，生成可审查的恢复记录。"""

from __future__ import annotations

from collections.abc import Sequence

from app.core.recovery import RecoveryAction, RecoveryDecision
from app.core.task_result import RecoveryRecord
from app.runtime.trace import ExecutionTrace


class RecoverySummaryBuilder:
    """从结构化恢复信号构建恢复记录与中文摘要。"""

    def build(
        self,
        trace: ExecutionTrace | None = None,
        decision: RecoveryDecision | None = None,
    ) -> tuple[RecoveryRecord, ...]:
        """汇总恢复决策、授权请求和回滚完成事件。"""
        records: list[RecoveryRecord] = []
        if decision is not None and decision.action is not RecoveryAction.CONTINUE:
            records.append(
                RecoveryRecord(
                    action=decision.action.value,
                    reason=decision.reason,
                    restored_paths=decision.recoverable_paths,
                )
            )
        if trace is None:
            return self._deduplicate(records)

        authorization_requested = False
        for event in trace.events:
            event_type = event["event"]
            details = event.get("details", {})
            if event_type == "rollback_authorization_requested":
                authorization_requested = True
            elif event_type == "recovery_decided":
                records.append(
                    RecoveryRecord(
                        action=str(details.get("action", "unknown")),
                        reason=str(details.get("reason", "恢复决策")),
                        restored_paths=tuple(details.get("recoverable_paths", ())),
                    )
                )
            elif event_type == "rollback_finished":
                rollback_status = str(details.get("status", "unknown"))
                authorized = (
                    False if rollback_status == "cancelled"
                    else True if authorization_requested else None
                )
                records.append(
                    RecoveryRecord(
                        action=f"rollback:{rollback_status}",
                        reason=str(details.get("reason", "回滚完成")),
                        authorized=authorized,
                        restored_paths=tuple(details.get("restored_paths", ())),
                    )
                )
        return self._deduplicate(records)

    @staticmethod
    def _deduplicate(records: Sequence[RecoveryRecord]) -> tuple[RecoveryRecord, ...]:
        """移除决策对象与 Trace 对同一恢复动作形成的重复记录。"""
        result: list[RecoveryRecord] = []
        seen: set[tuple[object, ...]] = set()
        for record in records:
            key = (
                record.action,
                record.reason,
                record.authorized,
                record.restored_paths,
            )
            if key not in seen:
                seen.add(key)
                result.append(record)
        return tuple(result)

    @staticmethod
    def render(records: Sequence[RecoveryRecord], repair_attempts: int = 0) -> str:
        """渲染自动修复次数、授权结果和回滚结果的中文摘要。"""
        if repair_attempts < 0:
            raise ValueError("自动修复次数不能小于 0。")
        lines = [f"恢复记录：自动修复 {repair_attempts} 次"]
        if not records:
            lines.append("  - 未触发恢复动作")
            return "\n".join(lines)
        for record in records:
            authorization = (
                "已授权" if record.authorized is True
                else "已拒绝" if record.authorized is False
                else "未涉及授权"
            )
            lines.append(f"  - {record.action}（{authorization}）：{record.reason}")
        return "\n".join(lines)


def summarize_recovery(
    trace: ExecutionTrace | None = None,
    decision: RecoveryDecision | None = None,
) -> tuple[RecoveryRecord, ...]:
    """使用默认构建器生成恢复记录。"""
    return RecoverySummaryBuilder().build(trace, decision)
