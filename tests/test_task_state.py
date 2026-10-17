import json

import pytest

from app.planner import PlanStepStatus, create_task_plan
from app.task_state import PlanStateManager


@pytest.fixture
def manager() -> PlanStateManager:
    return PlanStateManager(create_task_plan("读取配置，然后修改代码，然后运行测试"))


def test_initial_state_is_pending_and_serializable(manager):
    state = manager.state()
    assert state.current_step_id is None
    assert state.completed_step_ids == ()
    assert state.failed_step_ids == ()
    assert state.stop_reason is None
    assert all(step.status is PlanStepStatus.PENDING for step in state.plan.steps)
    assert json.loads(state.to_json()) == state.to_dict()


def test_step_can_start_and_complete_without_mutating_old_plan(manager):
    original = manager.state().plan
    manager.start_step(1)
    assert manager.state().current_step_id == 1
    assert manager.state().plan.steps[0].status is PlanStepStatus.RUNNING
    manager.complete_step(1, result="已完成")
    completed = manager.state()
    assert completed.current_step_id is None
    assert completed.completed_step_ids == (1,)
    assert completed.plan.steps[0].status is PlanStepStatus.COMPLETED
    assert original.steps[0].status is PlanStepStatus.PENDING


def test_failed_step_records_reason_and_stops_plan(manager):
    manager.start_step(1)
    manager.fail_step(1, "测试失败")
    state = manager.state()
    assert state.failed_step_ids == (1,)
    assert state.stop_reason == "测试失败"
    assert state.plan.steps[0].status is PlanStepStatus.FAILED
    assert all(step.status is PlanStepStatus.PENDING for step in state.plan.steps[1:])
    with pytest.raises(ValueError, match="计划已停止"):
        manager.start_step(2)


def test_cancelled_step_records_reason(manager):
    manager.start_step(1)
    manager.cancel_step(1, "用户取消")
    state = manager.state()
    assert state.stop_reason == "用户取消"
    assert state.plan.steps[0].status is PlanStepStatus.CANCELLED


def test_invalid_ids_and_transitions_are_rejected(manager):
    with pytest.raises(ValueError, match="不存在计划步骤"):
        manager.start_step(99)
    with pytest.raises(ValueError, match="只有当前 running"):
        manager.complete_step(1)
    manager.start_step(1)
    with pytest.raises(ValueError, match="已有步骤正在执行"):
        manager.start_step(2)
    manager.complete_step(1)
    with pytest.raises(ValueError, match="只有 pending"):
        manager.start_step(1)


def test_failure_and_cancel_require_non_empty_reasons(manager):
    manager.start_step(1)
    with pytest.raises(ValueError, match="失败原因不能为空"):
        manager.fail_step(1, " ")
    manager.cancel_step(1, "用户取消")
