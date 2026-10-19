"""任务计划的执行状态管理。

状态管理器只替换自己的计划快照，不修改调用方持有的旧计划对象，方便
Agent 在每个步骤后记录可审查的状态。
"""

import json
from dataclasses import dataclass, replace
from typing import Any

from app.core.planner import PlanStepStatus, TaskPlan


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
        """根据工具能力和目标路径选择匹配的 pending 步骤。"""

        # 建目录只是后续写文件的前置条件，不能单独证明功能或测试已经完成。
        if tool_name == "create_directory":
            return None

        path = str(arguments.get("path", "")).lower()
        objective_scores: list[tuple[int, int]] = []
        for step in self._plan.steps:
            if step.status is not PlanStepStatus.PENDING:
                continue
            text = step.objective.lower()
            score = 0
            if tool_name in {"list_files", "read_file", "search_code"} and any(
                word in text for word in ("阅读", "读取", "项目结构", "搜索")
            ):
                score += 4
            if tool_name in {"create_file", "edit_file", "delete_file", "delete_directory"}:
                in_tests = self._is_in_tests_path(path)
                is_test_source = self._is_test_source_path(path)
                if is_test_source:
                    score += 5 if "编写" in text or "补充" in text else 0
                score += 4 if not in_tests and any(
                    word in text
                    for word in ("实现", "修改", "修复", "创建", "新增", "功能")
                ) else 0
            if tool_name == "run_tests":
                score += 6 if "运行" in text or "验证" in text else 0
            if tool_name not in {"run_tests", "list_files", "read_file", "search_code", "create_file", "edit_file", "create_directory", "delete_file", "delete_directory"}:
                score += 1
            objective_scores.append((score, step.step_id))
        if not objective_scores:
            return None
        # 分数相同时保留更早的步骤，避免后面的无关步骤抢占当前工具结果。
        best_score, best_id = max(objective_scores, key=lambda item: (item[0], -item[1]))
        return best_id if best_score > 0 else None

    def can_complete_step(
        self, step_id: int, tool_name: str, arguments: dict[str, Any], result: Any
    ) -> bool:
        """只有工具类型、目标路径和成功结果共同满足时才允许完成步骤。"""

        status = getattr(result, "status", None)
        if status is None or getattr(status, "value", status) != "success":
            return False
        known_tools = {
            "run_tests", "list_files", "read_file", "search_code",
            "create_file", "edit_file", "create_directory", "delete_file",
            "delete_directory",
        }
        if tool_name not in known_tools:
            return True
        text = self._step(step_id).objective
        path = str(arguments.get("path", "")).lower()
        if "阅读" in text or "读取" in text or "项目结构" in text:
            return tool_name in {"list_files", "read_file", "search_code"}
        if "编写" in text or "补充" in text:
            return tool_name in {"create_file", "edit_file"} and self._is_test_source_path(path)
        if "运行" in text or "验证" in text:
            return tool_name == "run_tests"
        if any(word in text for word in ("实现", "修改", "修复", "创建", "新增", "功能")):
            return tool_name in {"create_file", "edit_file"} and not (
                path.startswith("tests/") or "/tests/" in path
            )
        return tool_name in {"create_file", "edit_file", "run_tests"}

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
