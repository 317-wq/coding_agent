"""基于结构化证据的任务结果验证与完成判定。

判定只接受两类事实：工具执行状态和结构化测试证据。测试结论完全来自
``run_tests`` 输出的结构化字段（测试目标、测试退出码、测试结果），不对原始
输出做失败或成功关键词扫描，避免用例名、日志或断言信息中的“error”影响结论。
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.core.acceptance import AcceptanceResult
from app.core.planner import PlanStepStatus, TaskPlan
from app.core.task_result import TestEvidence, TestEvidenceStatus
from app.core.test_evidence import structured_test_evidence
from app.tools.result import ToolResult, ToolStatus
from app.tools.test_result import TestRunResult


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

    @classmethod
    def _structured_test_evidence(
        cls, test_results: Sequence[TestRunResult | str]
    ) -> tuple[TestEvidence, ...]:
        """只保留结构化测试结果，并按目标折叠为最新证据。"""
        return structured_test_evidence(test_results)

    @staticmethod
    def _evidence_text(evidence: TestEvidence) -> str:
        """把结构化测试证据还原为可审查的描述。"""
        if evidence.output:
            return evidence.output
        return f"{evidence.target}：{evidence.status.value}"

    def verify(
        self,
        plan: TaskPlan,
        tool_results: Sequence[ToolResult] = (),
        test_results: Sequence[TestRunResult | str] = (),
        acceptance: Sequence[AcceptanceResult] = (),
    ) -> CompletionDecision:
        """根据计划状态、工具结果、测试证据与验收项严格判定复杂任务。"""
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

        tests = self._structured_test_evidence(test_results)
        failed_tests = tuple(
            self._evidence_text(item)
            for item in tests
            if item.status is TestEvidenceStatus.FAILED
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
            self._evidence_text(item)
            for item in tests
            if item.status is TestEvidenceStatus.PASSED
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
        unmet = tuple(item for item in acceptance if not item.passed)
        if unmet:
            return CompletionDecision(
                CompletionStatus.INCOMPLETE,
                "未满足验收条件：" + "；".join(item.item.text for item in unmet),
                pending,
                evidence=tuple(
                    f"{item.item.text}：{item.detail}" for item in unmet
                ),
            )
        return CompletionDecision(
            CompletionStatus.COMPLETE,
            "所有计划步骤已完成，且存在成功执行证据。",
            evidence=evidence,
        )

    def verify_basic(
        self,
        tool_results: Sequence[ToolResult] = (),
        test_results: Sequence[TestRunResult | str] = (),
        acceptance: Sequence[AcceptanceResult] = (),
    ) -> CompletionDecision:
        """验证简单任务也具备结构化执行证据与验收项。"""

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
        tests = self._structured_test_evidence(test_results)
        failed_tests = tuple(
            self._evidence_text(item)
            for item in tests
            if item.status is TestEvidenceStatus.FAILED
        )
        if failed_tests:
            return CompletionDecision(
                CompletionStatus.BLOCKED,
                "测试结果包含失败或错误。",
                evidence=failed_tests,
            )
        unmet = tuple(item for item in acceptance if not item.passed)
        if unmet:
            return CompletionDecision(
                CompletionStatus.INCOMPLETE,
                "未满足验收条件：" + "；".join(item.item.text for item in unmet),
                evidence=tuple(
                    f"{item.item.text}：{item.detail}" for item in unmet
                ),
            )
        evidence = tuple(
            (item.result or "工具执行成功")
            for item in tool_results
            if item.status is ToolStatus.SUCCESS
        ) + tuple(
            self._evidence_text(item)
            for item in tests
            if item.status is TestEvidenceStatus.PASSED
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
    test_results: Sequence[TestRunResult | str] = (),
    acceptance: Sequence[AcceptanceResult] = (),
) -> CompletionDecision:
    """使用默认验证器执行完成判定。"""

    return CompletionVerifier().verify(
        plan, tool_results, test_results, acceptance
    )
