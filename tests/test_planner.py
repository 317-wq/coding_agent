import json

import pytest

from app.core.complexity import TaskComplexity, judge_task_complexity
from app.core.planner import (
    PlanStepStatus,
    TaskPlan,
    TaskPlanner,
    create_task_plan,
)


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
