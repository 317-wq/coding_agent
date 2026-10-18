"""基于结构化证据的任务结果验证与完成判定。"""

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.core.planner import PlanStepStatus, TaskPlan
from app.tools.result import ToolResult, ToolStatus


class CompletionStatus(StrEnum):
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class CompletionDecision:
    """一次完成判定及其可审查证据。"""

    status: CompletionStatus
    reason: str
    pending_step_ids: tuple[int, ...] = ()
    failed_step_ids: tuple[int, ...] = ()
    evidence: tuple[str, ...] = ()

    @property
    def completed(self) -> bool:
        return self.status is CompletionStatus.COMPLETE

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "completed": self.completed,
            "reason": self.reason,
            "pending_step_ids": list(self.pending_step_ids),
            "failed_step_ids": list(self.failed_step_ids),
            "evidence": list(self.evidence),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


class CompletionVerifier:
    """不依赖模型自由文本的确定性完成判定器。"""

    _FAILURE_PATTERN = re.compile(
        r"失败|错误|failed|failure|error|exception", re.IGNORECASE
    )
    _SUCCESS_PATTERN = re.compile(
        r"通过|成功|passed|pass|success|ok", re.IGNORECASE
    )

    def verify(
        self,
        plan: TaskPlan,
        tool_results: Sequence[ToolResult] = (),
        test_results: Sequence[str] = (),
    ) -> CompletionDecision:
        if not isinstance(plan, TaskPlan):
            raise TypeError("计划必须是 TaskPlan。")
        if any(not isinstance(item, ToolResult) for item in tool_results):
            raise TypeError("工具结果必须是 ToolResult。")

        pending = tuple(
            step.step_id
            for step in plan.steps
            if step.status in {PlanStepStatus.PENDING, PlanStepStatus.RUNNING}
        )
        failed = tuple(
            step.step_id
            for step in plan.steps
            if step.status in {PlanStepStatus.FAILED, PlanStepStatus.CANCELLED}
        )
        if failed:
            return CompletionDecision(
                CompletionStatus.BLOCKED,
                "计划步骤失败或取消，不能判定任务完成。",
                pending,
                failed,
            )

        failed_tools = tuple(
            result.error or result.result or "工具执行失败"
            for result in tool_results
            if result.status in {ToolStatus.FAILURE, ToolStatus.CANCELLED}
        )
        if failed_tools:
            return CompletionDecision(
                CompletionStatus.BLOCKED,
                "存在失败或取消的工具结果。",
                evidence=failed_tools,
            )

        normalized_tests = tuple(result.strip() for result in test_results if result.strip())
        failed_tests = tuple(
            result for result in normalized_tests if self._FAILURE_PATTERN.search(result)
        )
        if failed_tests:
            return CompletionDecision(
                CompletionStatus.BLOCKED,
                "测试结果包含失败或错误。",
                evidence=failed_tests,
            )

        successful_tools = tuple(
            result.result or "工具执行成功"
            for result in tool_results
            if result.status is ToolStatus.SUCCESS
        )
        successful_tests = tuple(
            result for result in normalized_tests if self._SUCCESS_PATTERN.search(result)
        )
        evidence = successful_tools + successful_tests
        if not evidence:
            return CompletionDecision(
                CompletionStatus.INCOMPLETE,
                "缺少工具或测试的结构化完成证据。",
            )
        # 计划状态可能因模型未逐步回写而滞后；只有目标测试也通过时，
        # 才允许代码证据覆盖 pending 状态，避免单个任意工具调用误报完成。
        if pending and not successful_tests:
            return CompletionDecision(
                CompletionStatus.INCOMPLETE,
                "仍有计划步骤未完成，且缺少通过的目标测试证据。",
                pending,
                evidence=evidence,
            )
        if pending:
            return CompletionDecision(
                CompletionStatus.COMPLETE,
                "已有代码修改和目标测试等结构化证据，计划状态未完全同步。",
                pending,
                evidence=evidence,
            )
        return CompletionDecision(
            CompletionStatus.COMPLETE,
            "所有计划步骤已完成，且存在成功执行证据。",
            evidence=evidence,
        )

    def verify_basic(
        self,
        tool_results: Sequence[ToolResult] = (),
        test_results: Sequence[str] = (),
    ) -> CompletionDecision:
        """验证简单任务也具备结构化执行证据。"""

        if any(not isinstance(item, ToolResult) for item in tool_results):
            raise TypeError("工具结果必须是 ToolResult。")
        failures = tuple(
            item.error or item.result or "工具执行失败"
            for item in tool_results
            if item.status is ToolStatus.FAILURE
        )
        if failures:
            return CompletionDecision(
                CompletionStatus.BLOCKED,
                "存在失败的工具结果。",
                evidence=failures,
            )
        normalized_tests = tuple(item.strip() for item in test_results if item.strip())
        failed_tests = tuple(
            item for item in normalized_tests if self._FAILURE_PATTERN.search(item)
        )
        if failed_tests:
            return CompletionDecision(
                CompletionStatus.BLOCKED,
                "测试结果包含失败或错误。",
                evidence=failed_tests,
            )
        evidence = tuple(
            (item.result or "工具执行成功")
            for item in tool_results
            if item.status in {ToolStatus.SUCCESS, ToolStatus.CANCELLED}
        ) + tuple(
            item for item in normalized_tests if self._SUCCESS_PATTERN.search(item)
        )
        if not evidence:
            return CompletionDecision(
                CompletionStatus.INCOMPLETE,
                "缺少工具或测试的结构化完成证据。",
            )
        return CompletionDecision(
            CompletionStatus.COMPLETE,
            "存在结构化工具或测试完成证据。",
            evidence=evidence,
        )


def verify_completion(
    plan: TaskPlan,
    tool_results: Sequence[ToolResult] = (),
    test_results: Sequence[str] = (),
) -> CompletionDecision:
    """使用默认验证器执行完成判定。"""

    return CompletionVerifier().verify(plan, tool_results, test_results)
