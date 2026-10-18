"""根据结构化失败信号生成任务恢复决策。"""

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.core.completion import CompletionStatus


class RecoveryAction(StrEnum):
    CONTINUE = "continue"
    STOP = "stop"
    REPLAN = "replan"
    SUGGEST_ROLLBACK = "suggest_rollback"


@dataclass(frozen=True)
class RecoverySignals:
    """Agent Loop 提供给恢复策略的最小结构化信号。"""

    consecutive_tool_failures: int = 0
    test_failed: bool = False
    plan_failed: bool = False
    completion_status: CompletionStatus | None = None
    recoverable_paths: tuple[str, ...] = ()
    recovery_attempts: int = 0
    test_repair_attempts: int = 0
    test_error_kind: str | None = None

    def __post_init__(self) -> None:
        if self.consecutive_tool_failures < 0:
            raise ValueError("连续工具失败次数不能小于 0。")
        if self.recovery_attempts < 0:
            raise ValueError("恢复尝试次数不能小于 0。")
        if self.test_repair_attempts < 0:
            raise ValueError("测试自动修复次数不能小于 0。")
        if any(not path.strip() for path in self.recoverable_paths):
            raise ValueError("可恢复路径不能为空。")


@dataclass(frozen=True)
class RecoveryDecision:
    """恢复策略输出的动作、原因和相关路径。"""

    action: RecoveryAction
    reason: str
    triggers: tuple[str, ...] = ()
    recoverable_paths: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.value,
            "reason": self.reason,
            "triggers": list(self.triggers),
            "recoverable_paths": list(self.recoverable_paths),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


class RecoveryPolicy:
    """限制失败重试并在存在任务变更时优先建议回滚。"""

    def __init__(
        self,
        *,
        consecutive_failure_threshold: int = 3,
        max_recovery_attempts: int = 2,
    ) -> None:
        if consecutive_failure_threshold < 1:
            raise ValueError("连续失败阈值必须大于 0。")
        if max_recovery_attempts < 1:
            raise ValueError("恢复尝试上限必须大于 0。")
        self.consecutive_failure_threshold = consecutive_failure_threshold
        self.max_recovery_attempts = max_recovery_attempts

    def decide(self, signals: RecoverySignals) -> RecoveryDecision:
        triggers = self._triggers(signals)
        paths = tuple(dict.fromkeys(signals.recoverable_paths))

        if not triggers:
            return RecoveryDecision(
                RecoveryAction.CONTINUE,
                "没有达到恢复触发条件，继续当前执行循环。",
            )

        if signals.recovery_attempts >= self.max_recovery_attempts:
            return RecoveryDecision(
                RecoveryAction.STOP,
                "已达到恢复尝试上限，停止自动恢复。",
                triggers,
                paths,
            )

        if signals.test_error_kind in {"collection", "environment"}:
            return RecoveryDecision(
                RecoveryAction.STOP,
                "测试执行环境或收集阶段出错，未自动回滚代码；请先处理测试环境问题。",
                triggers,
                paths,
            )

        if signals.test_failed and signals.test_repair_attempts >= 2:
            return RecoveryDecision(
                RecoveryAction.STOP,
                "代码修改后的测试自动修复次数已达到上限 2 次，请人工处理。",
                triggers,
                paths,
            )

        hard_failure = (
            signals.test_failed
            or signals.plan_failed
            or signals.completion_status is CompletionStatus.BLOCKED
            or signals.consecutive_tool_failures
            >= self.consecutive_failure_threshold
        )
        if hard_failure and paths:
            return RecoveryDecision(
                RecoveryAction.SUGGEST_ROLLBACK,
                "执行已阻塞且存在本次任务变更，建议授权后回滚。",
                triggers,
                paths,
            )

        if signals.completion_status is CompletionStatus.BLOCKED:
            return RecoveryDecision(
                RecoveryAction.STOP,
                "完成判定已阻塞且没有可恢复变更，停止当前执行。",
                triggers,
            )

        if hard_failure:
            return RecoveryDecision(
                RecoveryAction.REPLAN,
                "执行失败已达到恢复条件，建议放弃当前计划并重新规划。",
                triggers,
            )

        return RecoveryDecision(
            RecoveryAction.CONTINUE,
            "失败次数尚未达到阈值，允许当前循环继续处理。",
            triggers,
        )

    def _triggers(self, signals: RecoverySignals) -> tuple[str, ...]:
        triggers: list[str] = []
        if signals.consecutive_tool_failures:
            triggers.append(
                f"连续工具失败 {signals.consecutive_tool_failures} 次"
            )
        if signals.test_failed:
            triggers.append("测试失败")
        if signals.test_error_kind:
            triggers.append(f"测试{signals.test_error_kind}错误")
        if signals.plan_failed:
            triggers.append("计划失败")
        if signals.completion_status is CompletionStatus.BLOCKED:
            triggers.append("完成判定阻塞")
        return tuple(triggers)


def decide_recovery(signals: RecoverySignals) -> RecoveryDecision:
    """使用默认策略生成恢复决策。"""

    return RecoveryPolicy().decide(signals)
