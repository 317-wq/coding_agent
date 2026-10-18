"""将用户任务拆解为可跟踪执行的计划步骤。"""

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.core.complexity import (
    ComplexityDecision,
    TaskComplexity,
    judge_task_complexity,
)
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
        # 计划按开发意图拆分，而不是按句号、编号或标题机械切片。
        # 先移除常见列表标记，再只在相邻片段都包含行动意图时拆分。
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

        if complexity is TaskComplexity.SIMPLE or len(parts) <= 1:
            cleaned = "；".join(parts) if parts else normalized.strip()
            return [f"完成用户任务：{cleaned}"]

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
