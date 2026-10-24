"""将用户任务拆解为可跟踪执行的计划步骤。"""

import json
import re
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any

from app.core.complexity import (
    ComplexityDecision,
    TaskComplexity,
    judge_task_complexity,
)
from app.core.intent import (
    EvidenceKind,
    TaskIntent,
    extract_intent,
)
from app.core.plan_lint import FILE_KINDS, lint_objectives, phase_of
from app.runtime.explorer import ProjectSnapshot


class PlanStepStatus(StrEnum):
    """计划步骤的生命周期状态。"""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class PlanStep:
    """一个可检查完成条件的计划步骤。"""

    step_id: int
    objective: str
    completion_criteria: str
    status: PlanStepStatus = PlanStepStatus.PENDING
    acceptance_ids: tuple[int, ...] = ()
    conditional: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "objective": self.objective,
            "completion_criteria": self.completion_criteria,
            "status": self.status.value,
            "acceptance_ids": list(self.acceptance_ids),
            "conditional": self.conditional,
        }


@dataclass(frozen=True)
class TaskPlan:
    """一次任务的稳定、可序列化计划。"""

    task: str
    complexity: TaskComplexity
    steps: tuple[PlanStep, ...]
    reasons: tuple[str, ...]
    intent: TaskIntent | None = None
    lint_issues: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "complexity": self.complexity.value,
            "reasons": list(self.reasons),
            "intent": self.intent.to_dict() if self.intent is not None else None,
            "lint_issues": list(self.lint_issues),
            "steps": [step.to_dict() for step in self.steps],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


class TaskPlanner:
    """根据任务文本生成不绑定具体工具名称的轻量步骤计划。"""

    def plan(
        self,
        task: str,
        snapshot: ProjectSnapshot | None = None,
        decision: ComplexityDecision | None = None,
    ) -> TaskPlan:
        if not isinstance(task, str) or not task.strip():
            raise ValueError("任务文本不能为空。")

        normalized = task.strip()
        decision = decision or judge_task_complexity(normalized, snapshot)
        intent = extract_intent(normalized)
        objectives = self._extract_objectives(normalized, decision.complexity)
        lint_issues: tuple[str, ...] = ()

        if decision.complexity is TaskComplexity.COMPLEX:
            raw = [self._strip_prefix(objective) for objective in objectives]
            result = lint_objectives(raw, intent)
            objectives = [f"完成：{objective}" for objective in result.objectives]
            lint_issues = tuple(
                f"{issue.rule}: {issue.detail}" for issue in result.issues
            )

        repair_conditional = bool(_CONDITIONAL_PATTERN.search(normalized))
        steps = tuple(
            PlanStep(
                step_id=index,
                objective=objective,
                completion_criteria=self._criteria_for(objective),
                acceptance_ids=self._bind_acceptance(objective, intent),
                conditional=repair_conditional
                and phase_of(objective) == "implement",
            )
            for index, objective in enumerate(objectives, start=1)
        )

        return TaskPlan(
            task=normalized,
            complexity=decision.complexity,
            steps=self._attach_unbound_acceptance(steps, intent),
            reasons=decision.reasons,
            intent=intent,
            lint_issues=lint_issues,
        )

    @staticmethod
    def _extract_objectives(task: str, complexity: TaskComplexity) -> list[str]:
        """从用户任务归并阅读、实现、测试编写和测试执行目标。"""
        # 计划按开发意图归并，而不是按句号、编号或连接词机械切片。
        normalized = re.sub(
            r"(?m)^\s*(?:#{1,6}\s*|(?:\d+|[一二三四五六七八九十]+)[、.)）:]\s*)",
            "",
            task,
        )
        normalized = re.sub(
            r"。(?=\s*(?:\d+|[一二三四五六七八九十]+)[、.)）:])",
            "\n",
            normalized,
        )
        action = r"(?:实现|新增|添加|创建|修改|更新|修复|删除|移除|重构|迁移|配置|编写|补充|测试|验证|运行|检查|读取|搜索|排查|支持|优化)"
        clauses = [
            part.strip(" ，,\t\n")
            for part in re.split(r"\n+|；|;|(?:然后|之后|接着|再|并且|以及|同时)", normalized)
        ]
        parts: list[str] = []
        for clause in clauses:
            if not clause:
                continue
            # “修改代码并运行测试”是两个真实开发目标；普通句子中的“并”不拆。
            pieces = re.split(r"\s+并(?=" + action + r")|并(?=" + action + r")", clause)
            for piece in pieces:
                piece = re.sub(r"^\s*(?:\d+|[一二三四五六七八九十]+)[、.)）:]\s*", "", piece)
                piece = piece.strip(" ，,\t\n。.")
                if piece and not re.fullmatch(r"[\d\W_]+", piece):
                    parts.append(piece)

        if complexity is TaskComplexity.SIMPLE:
            cleaned = "；".join(parts) if parts else normalized.strip()
            return [f"完成用户任务：{cleaned}"]

        # 长任务通常是需求清单。把多个细节归并成少量可验证的开发目标，
        # 避免每个函数、编号或断言都变成独立计划步骤。
        if len(parts) > 5 or len(normalized) > 180 or len(parts) <= 1:
            objectives: list[str] = []
            actionable_test_text = re.sub(
                r"(?:不要|不得|禁止).{0,12}(?:测试|用例|断言)",
                "",
                normalized,
            )
            if re.search(r"阅读|查看|读取|现有代码|项目结构|检查", normalized):
                objectives.append("阅读现有代码和项目结构")
            if re.search(r"实现|新增|添加|创建|修改|更新|修复|重构|优化", normalized):
                objectives.append("实现或修改任务要求的功能")
            if re.search(
                r"(?:编写|补充|新增|添加|创建|修改)\s*"
                r"(?:对应|相关|新的|现有)?\s*(?:测试|用例|断言)",
                actionable_test_text,
            ):
                objectives.append("编写或补充测试用例")
            if re.search(
                r"运行.{0,8}测试|执行.{0,8}测试|重跑.{0,8}测试|回归测试|完整测试|测试通过|验证",
                normalized,
            ):
                objectives.append("运行指定测试并记录结果")
            if objectives:
                return [f"完成：{objective}" for objective in objectives]
            return [f"完成用户任务：{normalized.strip()}"]

        return [f"完成：{part}" for part in parts]

    @staticmethod
    def _strip_prefix(objective: str) -> str:
        """去掉步骤目标的前缀，便于校验与绑定。"""
        for prefix in ("完成用户任务：", "完成："):
            if objective.startswith(prefix):
                return objective.removeprefix(prefix)
        return objective

    @staticmethod
    def _bind_acceptance(objective: str, intent: TaskIntent) -> tuple[int, ...]:
        """把验收项按阶段绑定到步骤，便于追踪。"""
        phase = phase_of(objective)
        ids: list[int] = []
        for item in intent.acceptance:
            matches = (
                (phase, item.kind)
                in {
                    ("read", EvidenceKind.WORKSPACE_READ),
                    ("test_run", EvidenceKind.TEST_PASSED),
                }
                or (
                    phase == "test_author"
                    and item.kind is EvidenceKind.FILE_EXISTS
                    and _is_test_path(item.target)
                )
                or (
                    phase == "implement"
                    and item.kind in FILE_KINDS
                    and not _is_test_path(item.target)
                )
            )
            if matches:
                ids.append(item.item_id)
        return tuple(ids)

    @staticmethod
    def _attach_unbound_acceptance(
        steps: tuple[PlanStep, ...], intent: TaskIntent
    ) -> tuple[PlanStep, ...]:
        """把未被任何步骤绑定的必需验收项挂到实现或测试编写步骤上。"""
        bound = {
            item_id for step in steps for item_id in step.acceptance_ids
        }
        unbound = [
            item.item_id
            for item in intent.acceptance
            if item.required and item.item_id not in bound
        ]
        if not unbound or not steps:
            return steps
        target = next(
            (
                index
                for index, step in enumerate(steps)
                if phase_of(step.objective) in {"implement", "test_author"}
            ),
            0,
        )
        entries = list(steps)
        entries[target] = replace(
            entries[target],
            acceptance_ids=entries[target].acceptance_ids + tuple(unbound),
        )
        return tuple(entries)

    @staticmethod
    def _criteria_for(objective: str) -> str:
        if "总结" in objective:
            return "已汇总修改文件、实现内容和测试验证结果。"
        if "阅读" in objective or "读取" in objective or "项目结构" in objective:
            return "相关源文件或项目结构已读取，并形成可用于实现的上下文。"
        if "测试" in objective and ("编写" in objective or "补充" in objective):
            return "指定测试文件已创建或更新，并覆盖任务要求的关键场景。"
        if "运行" in objective or "验证" in objective:
            return "指定测试命令已执行，并记录了实际通过或失败结果。"
        if "实现" in objective or "修改" in objective:
            return "相关代码已创建或修改，并满足用户明确要求。"
        return f"已获得与“{objective}”对应的工具或测试证据，且没有未处理错误。"


_CONDITIONAL_PATTERN = re.compile(
    r"如果失败|若失败|如有失败|失败时|必要时|如有问题|"
    r"直到通过|直到测试通过|直到全部通过|直到所有测试通过"
)


def _is_test_path(target: str | None) -> bool:
    """判断验收目标是否指向测试文件。"""
    if not target:
        return False
    lowered = target.lower()
    return "test" in lowered or lowered.startswith("tests/")


def create_task_plan(
    task: str,
    snapshot: ProjectSnapshot | None = None,
    decision: ComplexityDecision | None = None,
) -> TaskPlan:
    """使用默认 Planner 创建任务计划。"""

    return TaskPlanner().plan(task, snapshot, decision)


def carry_completed_steps(previous: TaskPlan, replacement: TaskPlan) -> TaskPlan:
    """在同一任务重规划时继承目标和完成条件一致的已完成步骤。"""
    completed = {
        (step.objective, step.completion_criteria)
        for step in previous.steps
        if step.status is PlanStepStatus.COMPLETED
    }
    steps = tuple(
        replace(step, status=PlanStepStatus.COMPLETED)
        if (step.objective, step.completion_criteria) in completed
        else step
        for step in replacement.steps
    )
    return replace(replacement, steps=steps)
