"""单次 Agent 任务的结构化结果模型。

该模块只定义结果数据和序列化规则，不负责从运行上下文汇总结果；
后续的结果汇总器会使用这些不可变模型生成用户报告和 JSON 输出。
"""

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.core.trace_summary import TraceReference


class TaskStatus(StrEnum):
    """任务执行的最终状态。"""

    COMPLETED = "completed"
    STOPPED = "stopped"
    FAILED = "failed"
    BLOCKED = "blocked"


class TestEvidenceStatus(StrEnum):
    """测试证据的结果状态。"""

    __test__ = False  # 名称以 Test 开头，告知 pytest 不要当作测试类收集

    PASSED = "passed"
    FAILED = "failed"
    NOT_RUN = "not_run"


@dataclass(frozen=True)
class ChangeSummary:
    """任务实际产生的文件变化，路径统一使用工作区相对路径。"""

    created: tuple[str, ...] = ()
    modified: tuple[str, ...] = ()
    deleted: tuple[str, ...] = ()
    unresolved: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """校验变更路径均为非空字符串。"""
        for paths in (self.created, self.modified, self.deleted, self.unresolved):
            if any(not isinstance(path, str) or not path.strip() for path in paths):
                raise ValueError("变更路径必须是非空字符串。")

    def to_dict(self) -> dict[str, list[str]]:
        """返回稳定的字典结构，供 JSON 结果和报告使用。"""
        return {
            "created": list(self.created),
            "modified": list(self.modified),
            "deleted": list(self.deleted),
            "unresolved": list(self.unresolved),
        }


@dataclass(frozen=True)
class TestEvidence:
    """一次指定测试执行的可审查证据。"""

    __test__ = False  # 名称以 Test 开头，告知 pytest 不要当作测试类收集

    target: str
    status: TestEvidenceStatus
    exit_code: int | None = None
    error_kind: str | None = None
    output: str | None = None

    def __post_init__(self) -> None:
        """校验测试目标和退出码字段格式。"""
        if not isinstance(self.target, str) or not self.target.strip():
            raise ValueError("测试目标不能为空。")
        if self.exit_code is not None and not isinstance(self.exit_code, int):
            raise TypeError("测试退出码必须是整数或 None。")

    def to_dict(self) -> dict[str, Any]:
        """返回可序列化的测试证据字典。"""
        return {
            "target": self.target,
            "status": self.status.value,
            "exit_code": self.exit_code,
            "error_kind": self.error_kind,
            "output": self.output,
        }


@dataclass(frozen=True)
class RecoveryRecord:
    """一次恢复决策及其授权/回滚结果。"""

    action: str
    reason: str
    authorized: bool | None = None
    restored_paths: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """校验恢复动作和原因均已提供。"""
        if not self.action.strip() or not self.reason.strip():
            raise ValueError("恢复动作和原因不能为空。")

    def to_dict(self) -> dict[str, Any]:
        """返回可序列化的恢复记录字典。"""
        return {
            "action": self.action,
            "reason": self.reason,
            "authorized": self.authorized,
            "restored_paths": list(self.restored_paths),
        }


@dataclass(frozen=True)
class ToolCallSummary:
    """直接来自 Trace 的工具调用计数。"""

    calls_started: int = 0
    calls_finished: int = 0
    succeeded: int = 0
    failed: int = 0
    cancelled: int = 0
    unfinished: int = 0

    def __post_init__(self) -> None:
        """校验全部调用计数都是非负整数。"""
        values = self.to_dict().values()
        if any(not isinstance(value, int) or value < 0 for value in values):
            raise ValueError("工具调用计数必须是非负整数。")

    def to_dict(self) -> dict[str, int]:
        """返回与 Trace 统计字段一致的字典。"""
        return {
            "calls_started": self.calls_started,
            "calls_finished": self.calls_finished,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "cancelled": self.cancelled,
            "unfinished": self.unfinished,
        }


@dataclass(frozen=True)
class FinalVerificationSummary:
    """任务结束前从磁盘、Git、测试和 Trace 重新取得的证据摘要。"""

    verified: bool = False
    comparison_basis: str = "task_initial_state"
    verified_path_count: int = 0
    net_changed_count: int = 0
    git_status: str = "not_checked"
    git_dirty: bool | None = None
    git_changed_count: int | None = None
    test_evidence_count: int = 0
    evidence_sources: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """校验核验计数、比较口径和证据来源。"""
        if self.comparison_basis != "task_initial_state":
            raise ValueError("最终文件核验必须相对任务初始状态。")
        counts = (
            self.verified_path_count,
            self.net_changed_count,
            self.test_evidence_count,
        )
        if any(not isinstance(value, int) or value < 0 for value in counts):
            raise ValueError("最终核验计数必须是非负整数。")
        if self.git_changed_count is not None and self.git_changed_count < 0:
            raise ValueError("Git 变更计数不能小于 0。")

    def to_dict(self) -> dict[str, Any]:
        """返回最终核验证据的稳定字典。"""
        return {
            "verified": self.verified,
            "comparison_basis": self.comparison_basis,
            "verified_path_count": self.verified_path_count,
            "net_changed_count": self.net_changed_count,
            "git_status": self.git_status,
            "git_dirty": self.git_dirty,
            "git_changed_count": self.git_changed_count,
            "test_evidence_count": self.test_evidence_count,
            "evidence_sources": list(self.evidence_sources),
        }


@dataclass(frozen=True)
class TaskResult:
    """单次任务的稳定、可序列化结果。"""

    task: str
    status: TaskStatus
    summary: str = ""
    stop_reason: str | None = None
    changes: ChangeSummary = ChangeSummary()
    tests: tuple[TestEvidence, ...] = ()
    recovery: tuple[RecoveryRecord, ...] = ()
    repair_attempts: int = 0
    model_request_count: int = 0
    tool_calls: ToolCallSummary = ToolCallSummary()
    trace_event_count: int = 0
    final_verification: FinalVerificationSummary = FinalVerificationSummary()
    next_steps: tuple[str, ...] = ()
    trace_references: tuple[TraceReference, ...] = ()

    def __post_init__(self) -> None:
        """校验任务描述、修复次数和 Trace 计数合法。"""
        if not isinstance(self.task, str) or not self.task.strip():
            raise ValueError("任务描述不能为空。")
        if self.repair_attempts < 0:
            raise ValueError("自动修复次数不能小于 0。")
        if self.model_request_count < 0 or self.trace_event_count < 0:
            raise ValueError("模型请求和 Trace 事件数量不能小于 0。")
        if any(not isinstance(item, str) or not item.strip() for item in self.next_steps):
            raise ValueError("下一步建议必须是非空字符串。")
        if any(not isinstance(item, TraceReference) for item in self.trace_references):
            raise TypeError("Trace 引用必须是 TraceReference。")

    @property
    def completed(self) -> bool:
        """是否为成功完成状态。"""

        return self.status is TaskStatus.COMPLETED

    def to_dict(self) -> dict[str, Any]:
        """返回完整任务结果的稳定字典表示。"""
        return {
            "task": self.task,
            "status": self.status.value,
            "completed": self.completed,
            "summary": self.summary,
            "stop_reason": self.stop_reason,
            "changes": self.changes.to_dict(),
            "tests": [test.to_dict() for test in self.tests],
            "recovery": [item.to_dict() for item in self.recovery],
            "repair_attempts": self.repair_attempts,
            "model_request_count": self.model_request_count,
            "tool_calls": self.tool_calls.to_dict(),
            "trace_event_count": self.trace_event_count,
            "final_verification": self.final_verification.to_dict(),
            "next_steps": list(self.next_steps),
            "trace_references": [item.to_dict() for item in self.trace_references],
        }

    def to_json(self) -> str:
        """输出稳定的 JSON 结果。"""

        return json.dumps(self.to_dict(), ensure_ascii=False)
