"""任务计划的执行状态管理。

状态管理器只替换自己的计划快照，不修改调用方持有的旧计划对象，方便
Agent 在每个步骤后记录可审查的状态。
"""

import json
from dataclasses import dataclass, replace
from typing import Any

from app.core.planner import PlanStepStatus, TaskPlan
from app.tools.capability import ToolCapability, capability_of


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

    def match_step(self, tool_name: str, arguments: dict[str, Any]) -> int | None:
        """按工具能力类别和目标路径选择匹配的 pending 步骤。"""

        # 建目录只是后续写文件的前置条件，不能单独证明功能或测试已经完成。
        if tool_name == "create_directory":
            return None

        capability = capability_of(tool_name)

        # 只读版本控制查询和未登记工具不参与任何步骤状态流转。
        if capability in {ToolCapability.VCS, ToolCapability.UNKNOWN}:
            return None

        path = str(arguments.get("path", "")).lower()
        objective_scores: list[tuple[int, int]] = []
        for step in self._plan.steps:
            if step.status is not PlanStepStatus.PENDING:
                continue
            text = step.objective.lower()
            score = 0
            if capability is ToolCapability.READ and any(
                word in text for word in ("阅读", "读取", "项目结构", "搜索")
            ):
                score += 4
            if capability is ToolCapability.MUTATE:
                in_tests = self._is_in_tests_path(path)
                is_test_source = self._is_test_source_path(path)
                if is_test_source:
                    score += 5 if "编写" in text or "补充" in text else 0
                score += 4 if not in_tests and any(
                    word in text
                    for word in ("实现", "修改", "修复", "创建", "新增", "删除", "功能")
                ) else 0
            if capability is ToolCapability.TEST:
                score += 6 if "运行" in text or "验证" in text else 0
            objective_scores.append((score, step.step_id))
        if not objective_scores:
            return None
        # 分数相同时保留更早的步骤，避免后面的无关步骤抢占当前工具结果。
        best_score, best_id = max(objective_scores, key=lambda item: (item[0], -item[1]))
        return best_id if best_score > 0 else None

    def can_complete_step(
        self, step_id: int, tool_name: str, arguments: dict[str, Any], result: Any
    ) -> bool:
        """只有工具能力类别、目标路径和成功结果共同满足时才允许完成步骤。"""

        status = getattr(result, "status", None)
        if status is None or getattr(status, "value", status) != "success":
            return False

        capability = capability_of(tool_name)

        # 只读版本控制查询和未登记工具不是任何步骤的完成证据。
        if capability in {ToolCapability.VCS, ToolCapability.UNKNOWN}:
            return False

        step = self._step(step_id)
        phase = getattr(step, "phase", None)
        text = step.objective

        if phase == "read" and capability is not ToolCapability.READ:
            return False
        # 合并步骤（“实现…；运行测试”）允许由测试执行完成；
        # 纯实现步骤必须由写操作完成，避免读取类工具误判完成。
        if (
            phase == "implement"
            and capability is not ToolCapability.MUTATE
            and not ("运行" in text or "验证" in text)
        ):
            return False
        if phase == "test_run" and capability is not ToolCapability.TEST:
            return False
        path = str(arguments.get("path", "")).lower()
        if "阅读" in text or "读取" in text or "项目结构" in text:
            return capability is ToolCapability.READ
        if "编写" in text or "补充" in text:
            return (
                tool_name in {"create_file", "edit_file"}
                and self._is_test_source_path(path)
                and not self._has_later_test_execution_step(step_id)
            )
        if "运行" in text or "验证" in text:
            return capability is ToolCapability.TEST
        if any(
            word in text for word in ("实现", "修改", "修复", "创建", "新增", "删除", "功能")
        ):
            mutation_matches = (
                capability is ToolCapability.MUTATE
                and tool_name != "create_directory"
                and not self._is_in_tests_path(path)
            )
            if not mutation_matches:
                return False
            if tool_name in {"delete_file", "delete_directory"} and "删除" in text:
                return True
            return not self._has_later_development_step(step_id)
        return tool_name in {"create_file", "edit_file", "run_tests"}

    def should_advance_for_tool(
        self, step_id: int, tool_name: str, arguments: dict[str, Any]
    ) -> bool:
        """判断新工具是否证明当前阶段已结束并进入后续阶段。"""
        current = self._step(step_id)
        if current.status is not PlanStepStatus.RUNNING:
            return False
        matched = self.match_step(tool_name, arguments)
        return matched is not None and matched > step_id

    def should_defer_tests_without_sources(self) -> bool:
        """判断开发计划是否仍需先实现功能或编写测试源文件。"""
        development_markers = ("实现", "修改", "修复", "创建", "新增", "编写", "补充", "功能")
        return any(
            step.status in {PlanStepStatus.PENDING, PlanStepStatus.RUNNING}
            and any(marker in step.objective for marker in development_markers)
            for step in self._plan.steps
        )

    def _has_later_development_step(self, step_id: int) -> bool:
        """判断实现步骤之后是否还有测试编写或执行验收阶段。"""
        return any(
            step.step_id > step_id
            and any(word in step.objective for word in ("编写", "补充", "运行", "验证"))
            for step in self._plan.steps
        )

    def _has_later_test_execution_step(self, step_id: int) -> bool:
        """判断测试编写步骤之后是否还有真实测试执行阶段。"""
        return any(
            step.step_id > step_id
            and any(word in step.objective for word in ("运行", "验证"))
            for step in self._plan.steps
        )

    @staticmethod
    def _is_test_source_path(path: str) -> bool:
        """判断路径是否为能证明测试已编写的 Python 测试源文件。"""
        normalized = path.replace("\\", "/")
        name = normalized.rsplit("/", 1)[-1]
        in_tests = PlanStateManager._is_in_tests_path(normalized)
        return in_tests and (name.startswith("test_") or name == "conftest.py")

    @staticmethod
    def _is_in_tests_path(path: str) -> bool:
        """判断路径是否位于 tests 目录中。"""
        normalized = path.replace("\\", "/")
        return normalized.startswith("tests/") or "/tests/" in normalized

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
