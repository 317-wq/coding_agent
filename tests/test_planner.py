import json

import pytest

from app.core.complexity import TaskComplexity, judge_task_complexity
from app.core.intent import EvidenceKind
from app.core.plan_lint import phase_of
from app.core.planner import (
    PlanStepStatus,
    TaskPlan,
    TaskPlanner,
    carry_completed_steps,
    create_task_plan,
)
from app.core.task_state import PlanStateManager


def test_complex_task_generates_ordered_pending_steps():
    decision = judge_task_complexity("读取配置，然后修改代码并运行测试")

    plan = create_task_plan("读取配置，然后修改代码并运行测试", decision=decision)

    assert isinstance(plan, TaskPlan)
    assert plan.complexity is TaskComplexity.COMPLEX
    assert [step.step_id for step in plan.steps] == [1, 2, 3]
    assert all(step.status is PlanStepStatus.PENDING for step in plan.steps)
    assert plan.steps[0].objective == "完成：读取配置"
    assert "读取" in plan.steps[0].completion_criteria


def test_simple_task_has_single_stable_objective():
    plan = create_task_plan("读取 app.runtime.config.py")

    assert plan.complexity is TaskComplexity.SIMPLE
    assert len(plan.steps) == 1
    assert plan.steps[0].objective == "完成用户任务：读取 app.runtime.config.py"


def test_plan_serialization_is_stable_and_json_compatible():
    plan = create_task_plan("先搜索代码，然后运行测试")
    serialized = plan.to_json()
    restored = json.loads(serialized)

    assert restored == plan.to_dict()
    assert restored["steps"][0]["status"] == "pending"
    assert restored["steps"][0]["step_id"] == 1


def test_planner_preserves_explicit_complexity_decision():
    decision = judge_task_complexity("读取配置")
    plan = TaskPlanner().plan(
        "读取配置，然后修改代码", decision=decision
    )

    assert plan.complexity is TaskComplexity.SIMPLE
    assert len(plan.steps) == 1


def test_empty_task_is_rejected():
    with pytest.raises(ValueError, match="任务文本不能为空"):
        TaskPlanner().plan(" ")


def test_planner_does_not_create_number_or_heading_steps():
    task = "1、创建 calculator.py。2、补充 pytest 测试。"
    plan = create_task_plan(task, decision=judge_task_complexity(task))

    assert all(step.objective not in {"完成：1", "完成：2"} for step in plan.steps)
    assert all("calculator.py" in step.objective or "pytest" in step.objective for step in plan.steps)


def test_regression_task_plans_test_execution_without_test_authoring():
    """验证只要求回归测试的修复任务不会被误规划成编写测试。"""
    task = (
        "检查 calculator.py 和 calculator_history.py，修复循环导入问题。"
        "不要修改测试断言。修复后运行完整回归测试，只有测试通过才能完成。"
    )

    plan = create_task_plan(task, decision=judge_task_complexity(task))
    objectives = tuple(step.objective for step in plan.steps)

    assert any("实现或修改" in objective for objective in objectives)
    assert any("运行指定测试" in objective for objective in objectives)
    assert not any("编写或补充测试" in objective for objective in objectives)


def test_repair_then_rerun_does_not_imply_test_authoring():
    """验证“修改后重跑测试”不会被误解为修改测试用例。"""
    task = (
        "检查现有实现，删除无用临时文件。"
        "测试失败时进行代码修复，每次修改后必须立即重跑原测试目标。"
    )

    plan = create_task_plan(task, decision=judge_task_complexity(task))
    objectives = tuple(step.objective for step in plan.steps)

    assert any("实现或修改" in objective for objective in objectives)
    assert any("运行指定测试" in objective for objective in objectives)
    assert not any("编写或补充测试" in objective for objective in objectives)


def test_replan_carries_completed_steps_and_retries_failed_step():
    """验证重规划继承已完成目标，但将失败目标恢复为待执行。"""
    task = "读取配置，然后修改代码，然后运行测试"
    previous = create_task_plan(task, decision=judge_task_complexity(task))
    manager = PlanStateManager(previous)
    manager.start_step(1)
    manager.complete_step(1)
    manager.start_step(2)
    manager.complete_step(2)
    manager.start_step(3)
    manager.fail_step(3, "测试失败")
    replacement = create_task_plan(task, decision=judge_task_complexity(task))

    carried = carry_completed_steps(manager.state().plan, replacement)

    assert [step.status for step in carried.steps] == [
        PlanStepStatus.COMPLETED,
        PlanStepStatus.COMPLETED,
        PlanStepStatus.PENDING,
    ]


def test_non_actionable_requirement_fragment_is_merged():
    """真实验证：需求细节不会被当成独立步骤，且补齐测试执行阶段。"""
    task = (
        "实现 calculator.py，提供 add(a, b) 和 divide(a, b) 两个函数；"
        "divide 在 b 为 0 时抛出 ZeroDivisionError。"
        "然后编写 tests/test_calculator.py 覆盖这两个函数，最后运行测试确认通过。"
    )

    plan = create_task_plan(task)

    assert all(phase_of(step.objective) is not None for step in plan.steps)
    assert any("运行指定测试" in step.objective for step in plan.steps)
    assert plan.intent is not None
    assert any(
        item.kind is EvidenceKind.TEST_PASSED for item in plan.intent.acceptance
    )
    assert any(issue.startswith("L1:") for issue in plan.lint_issues)
