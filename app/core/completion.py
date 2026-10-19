"""基于结构化证据的任务结果验证与完成判定。"""

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.core.planner import PlanStepStatus, TaskPlan
from app.core.test_evidence import test_target_covers
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
        """返回可序列化的完成判定及其证据。"""
        return {
            "status": self.status.value,
            "completed": self.completed,
            "reason": self.reason,
            "pending_step_ids": list(self.pending_step_ids),
            "failed_step_ids": list(self.failed_step_ids),
            "evidence": list(self.evidence),
        }

    def to_json(self) -> str:
        """将完成判定序列化为稳定 JSON。"""
        return json.dumps(self.to_dict(), ensure_ascii=False)


class CompletionVerifier:
    """不依赖模型自由文本的确定性完成判定器。"""

    _FAILURE_PATTERN = re.compile(
        r"失败|错误|failed|failure|error|exception", re.IGNORECASE
    )
    _SUCCESS_PATTERN = re.compile(
        r"通过|成功|passed|pass|success|ok", re.IGNORECASE
    )
    _TEST_TARGET_PATTERN = re.compile(r"^测试目标：([^\n]+)", re.MULTILINE)

    @classmethod
    def _latest_test_results(cls, test_results: Sequence[str]) -> tuple[str, ...]:
        """保留最新结果，并允许成功的父目标覆盖历史子目标失败。"""
        latest: dict[str, str] = {}
        unkeyed: list[str] = []
        for raw in test_results:
            normalized = raw.strip()
            if not normalized:
                continue
            match = cls._TEST_TARGET_PATTERN.search(normalized)
            if match is None:
                unkeyed.append(normalized)
                continue
            target = match.group(1).strip()
            if cls._is_successful_test_result(normalized):
                latest = {
                    old_target: old_result
                    for old_target, old_result in latest.items()
                    if not test_target_covers(target, old_target)
                }
            latest[target] = normalized
        return (*unkeyed, *latest.values())

    @classmethod
    def _is_successful_test_result(cls, raw: str) -> bool:
        """判断测试文本是否明确表示成功，而非依赖模糊关键词。"""
        return "测试退出码：0" in raw and "测试结果：通过" in raw

    def verify(
        self,
        plan: TaskPlan,
        tool_results: Sequence[ToolResult] = (),
        test_results: Sequence[str] = (),
    ) -> CompletionDecision:
        """根据计划状态、工具结果和测试结果严格判定复杂任务。"""
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
                pending_step_ids=pending,
                failed_step_ids=failed,
                evidence=failed_tools,
            )

        normalized_tests = self._latest_test_results(test_results)
        failed_tests = tuple(
            result for result in normalized_tests if self._FAILURE_PATTERN.search(result)
        )
        if failed_tests:
            return CompletionDecision(
                CompletionStatus.BLOCKED,
                "测试结果包含失败或错误。",
                pending_step_ids=pending,
                failed_step_ids=failed,
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
        # 计划是任务目标的结构化表达。测试通过只能证明测试步骤，不能替代
        # 尚未完成的阅读、实现或其他必要步骤，因此 pending 永远不能判为完成。
        if pending:
            return CompletionDecision(
                CompletionStatus.INCOMPLETE,
                "仍有必要计划步骤未完成，现有工具或测试证据不能替代这些步骤。",
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
            if item.status in {ToolStatus.FAILURE, ToolStatus.CANCELLED}
        )
        if failures:
            return CompletionDecision(
                CompletionStatus.BLOCKED,
                "存在失败或取消的工具结果。",
                evidence=failures,
            )
        normalized_tests = self._latest_test_results(test_results)
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
            if item.status is ToolStatus.SUCCESS
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
