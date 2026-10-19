"""从脱敏执行 Trace 中提取有限的关键事件引用。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

from app.runtime.trace import ExecutionTrace


@dataclass(frozen=True)
class TraceReference:
    """面向报告的单条 Trace 事件引用，不包含完整事件载荷。"""

    event: str
    timestamp: str
    summary: str = ""
    trace_id: str = ""
    sequence: int = 0

    def to_dict(self) -> dict[str, str]:
        """返回可序列化的 Trace 引用。"""
        return {
            "event": self.event,
            "timestamp": self.timestamp,
            "summary": self.summary,
            "trace_id": self.trace_id,
            "sequence": self.sequence,
        }


class TraceSummaryBuilder:
    """从 Trace 事件中选择终止、恢复和测试等关键事件。"""

    _KEY_EVENTS: ClassVar[frozenset[str]] = frozenset({
        "task_started",
        "task_completed",
        "task_failed",
        "task_stopped",
        "completion_checked",
        "recovery_decided",
        "rollback_finished",
        "requirements_checked",
        "safety_rejected",
        "final_state_verified",
        "final_state_verification_failed",
    })

    def build(self, trace: ExecutionTrace | None, limit: int = 12) -> tuple[TraceReference, ...]:
        """提取最多 limit 条关键事件，使用 Trace 已完成的脱敏结果。"""
        if limit < 1:
            raise ValueError("Trace 引用数量上限必须大于 0。")
        if trace is None:
            return ()
        references: list[TraceReference] = []
        for item in trace.get_events():
            event = str(item.get("event", ""))
            if event not in self._KEY_EVENTS:
                continue
            details = item.get("details", {})
            references.append(
                TraceReference(
                    event=event,
                    timestamp=str(item.get("timestamp", "")),
                    summary=self._summary(details),
                    trace_id=str(item.get("trace_id", "")),
                    sequence=int(item.get("sequence", 0)),
                )
            )
        selected = references[:limit]
        final_reference = next(
            (
                item
                for item in reversed(references)
                if item.event
                in {"final_state_verified", "final_state_verification_failed"}
            ),
            None,
        )
        if final_reference is not None and final_reference not in selected:
            selected[-1] = final_reference
        return tuple(selected)

    @staticmethod
    def _summary(details: Any) -> str:
        """从脱敏详情中选取短摘要，不复制完整 Trace 载荷。"""
        if not isinstance(details, dict):
            return ""
        for key in ("reason", "status", "message", "error"):
            value = details.get(key)
            if value is not None:
                return str(value)[:240]
        return ""


def summarize_trace(trace: ExecutionTrace | None, limit: int = 12) -> tuple[TraceReference, ...]:
    """使用默认构建器生成关键 Trace 引用。"""
    return TraceSummaryBuilder().build(trace, limit)
