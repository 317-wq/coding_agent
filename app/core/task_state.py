"""任务计划的执行状态管理。

状态管理器只替换自己的计划快照，不修改调用方持有的旧计划对象，方便
Agent 在每个步骤后记录可审查的状态。
"""

import json
from dataclasses import dataclass, replace
from typing import Any

from app.planner import PlanStepStatus, TaskPlan


@dataclass(frozen=True)
class PlanExecutionState:
    """计划执行摘要及其当前计划快照。"""

    plan: TaskPlan
    current_step_id: int | None
    completed_step_ids: tuple[int, ...]
    failed_step_ids: tuple[int, ...]
    stop_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan": self.plan.to_dict(),
            "current_step_id": self.current_step_id,
            "completed_step_ids": list(self.completed_step_ids),
            "failed_step_ids": list(self.failed_step_ids),
            "stop_reason": self.stop_reason,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


class PlanStateManager:
    """执行一个 :class:`TaskPlan` 的显式状态流转。"""

    def __init__(self, plan: TaskPlan) -> None:
        self._plan = plan
        self._current_step_id: int | None = None
        self._completed_step_ids: list[int] = []
        self._failed_step_ids: list[int] = []
        self._stop_reason: str | None = None

    def state(self) -> PlanExecutionState:
        return PlanExecutionState(
            plan=self._plan,
            current_step_id=self._current_step_id,
            completed_step_ids=tuple(self._completed_step_ids),
            failed_step_ids=tuple(self._failed_step_ids),
            stop_reason=self._stop_reason,
        )

    def start_step(self, step_id: int) -> TaskPlan:
        self._ensure_step(step_id)
        if self._stop_reason is not None:
            raise ValueError("计划已停止，不能开始新步骤。")
        if self._current_step_id is not None:
            raise ValueError("已有步骤正在执行。")
        step = self._step(step_id)
        if step.status is not PlanStepStatus.PENDING:
            raise ValueError("只有 pending 步骤可以开始。")
        return self._replace_step(step_id, PlanStepStatus.RUNNING)

    def complete_step(self, step_id: int, result: Any = None) -> TaskPlan:
        del result  # 结果由上下文模块保存；此处只负责状态流转。
        self._ensure_running(step_id)
        plan = self._replace_step(step_id, PlanStepStatus.COMPLETED)
        self._completed_step_ids.append(step_id)
        self._current_step_id = None
        return plan

    def fail_step(self, step_id: int, reason: str) -> TaskPlan:
        self._ensure_running(step_id)
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("失败原因不能为空。")
        plan = self._replace_step(step_id, PlanStepStatus.FAILED)
        self._failed_step_ids.append(step_id)
        self._current_step_id = None
        self._stop_reason = reason.strip()
        return plan

    def cancel_step(self, step_id: int, reason: str) -> TaskPlan:
        self._ensure_running(step_id)
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("取消原因不能为空。")
        plan = self._replace_step(step_id, PlanStepStatus.CANCELLED)
        self._current_step_id = None
        self._stop_reason = reason.strip()
        return plan

    def _ensure_step(self, step_id: int) -> None:
        if not any(step.step_id == step_id for step in self._plan.steps):
            raise ValueError(f"不存在计划步骤：{step_id}。")

    def _step(self, step_id: int):
        return next(step for step in self._plan.steps if step.step_id == step_id)

    def _ensure_running(self, step_id: int) -> None:
        self._ensure_step(step_id)
        if self._current_step_id != step_id:
            raise ValueError("只有当前 running 步骤可以结束。")
        if self._step(step_id).status is not PlanStepStatus.RUNNING:
            raise ValueError("步骤当前不是 running 状态。")

    def _replace_step(self, step_id: int, status: PlanStepStatus) -> TaskPlan:
        steps = tuple(
            replace(step, status=status) if step.step_id == step_id else step
            for step in self._plan.steps
        )
        self._plan = replace(self._plan, steps=steps)
        if status is PlanStepStatus.RUNNING:
            self._current_step_id = step_id
        return self._plan
