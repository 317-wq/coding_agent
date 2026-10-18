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
    trace_event_count: int = 0
    next_steps: tuple[str, ...] = ()
    trace_references: tuple[TraceReference, ...] = ()

    def __post_init__(self) -> None:
        """校验任务描述、修复次数和 Trace 计数合法。"""
        if not isinstance(self.task, str) or not self.task.strip():
            raise ValueError("任务描述不能为空。")
        if self.repair_attempts < 0:
            raise ValueError("自动修复次数不能小于 0。")
        if self.trace_event_count < 0:
            raise ValueError("Trace 事件数量不能小于 0。")
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
            "trace_event_count": self.trace_event_count,
            "next_steps": list(self.next_steps),
            "trace_references": [item.to_dict() for item in self.trace_references],
        }

    def to_json(self) -> str:
        """输出稳定的 JSON 结果。"""

        return json.dumps(self.to_dict(), ensure_ascii=False)
