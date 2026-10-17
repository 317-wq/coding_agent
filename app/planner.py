import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.complexity import ComplexityDecision, TaskComplexity, judge_task_complexity
from app.explorer import ProjectSnapshot


class PlanStepStatus(StrEnum):
    """计划步骤的初始状态；状态流转由后续模块负责。"""

    PENDING = "pending"


@dataclass(frozen=True)
class PlanStep:
    """一个可检查完成条件的计划步骤。"""

    step_id: int
    objective: str
    completion_criteria: str
    status: PlanStepStatus = PlanStepStatus.PENDING

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "objective": self.objective,
            "completion_criteria": self.completion_criteria,
            "status": self.status.value,
        }


@dataclass(frozen=True)
class TaskPlan:
    """一次任务的稳定、可序列化计划。"""

    task: str
    complexity: TaskComplexity
    steps: tuple[PlanStep, ...]
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "complexity": self.complexity.value,
            "reasons": list(self.reasons),
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
        objectives = self._extract_objectives(normalized, decision.complexity)
        steps = tuple(
            PlanStep(
                step_id=index,
                objective=objective,
                completion_criteria=self._criteria_for(objective),
            )
            for index, objective in enumerate(objectives, start=1)
        )

        return TaskPlan(
            task=normalized,
            complexity=decision.complexity,
            steps=steps,
            reasons=decision.reasons,
        )

    @staticmethod
    def _extract_objectives(task: str, complexity: TaskComplexity) -> list[str]:
        separators = r"\s*(?:→|->|然后|并且|以及|同时|之后|再|并|;|；|\.|。)\s*"
        parts = [part.strip(" ，,\t\n") for part in re.split(separators, task)]
        parts = [part for part in parts if part]

        if complexity is TaskComplexity.SIMPLE or len(parts) <= 1:
            return [f"完成用户任务：{task}"]

        return [f"完成：{part}" for part in parts]

    @staticmethod
    def _criteria_for(objective: str) -> str:
        return f"工具结果表明已完成“{objective}”，且没有未处理错误。"


def create_task_plan(
    task: str,
    snapshot: ProjectSnapshot | None = None,
    decision: ComplexityDecision | None = None,
) -> TaskPlan:
    """使用默认 Planner 创建任务计划。"""

    return TaskPlanner().plan(task, snapshot, decision)
