import json

import pytest

from app.core.planner import PlanStepStatus, create_task_plan
from app.core.task_state import PlanStateManager


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


def test_match_step_uses_test_path_instead_of_step_order():
    manager = PlanStateManager(
        create_task_plan("阅读代码，然后实现功能，然后编写测试，然后运行测试")
    )

    step_id = manager.match_step("create_file", {"path": "tests/test_calculator.py"})

    assert step_id is not None
    assert "测试" in manager.state().plan.steps[step_id - 1].objective


def test_create_test_file_matches_test_step_not_implementation_step():
    manager = PlanStateManager(create_task_plan("实现功能，然后编写测试"))
    step_id = manager.match_step("create_file", {"path": "tests/test_calculator.py"})

    assert step_id is not None
    assert "测试" in manager.state().plan.steps[step_id - 1].objective


def test_create_directory_does_not_start_or_complete_feature_step():
    """验证仅创建源码或测试目录不会推进对应计划步骤。"""
    manager = PlanStateManager(
        create_task_plan("实现功能，然后编写测试，然后运行测试")
    )

    assert manager.match_step("create_directory", {"path": "todo_cli"}) is None
    assert manager.match_step("create_directory", {"path": "tests"}) is None
    assert manager.state().current_step_id is None
    assert all(
        step.status is PlanStepStatus.PENDING for step in manager.state().plan.steps
    )


def test_test_package_initializer_does_not_complete_test_authoring_step():
    """验证 tests/__init__.py 不是测试用例完成证据。"""
    manager = PlanStateManager(create_task_plan("实现功能，然后编写测试，然后运行测试"))

    assert manager.match_step("create_file", {"path": "tests/__init__.py"}) is None
    test_step = manager.match_step(
        "create_file", {"path": "tests/test_todo_cli.py"}
    )

    assert test_step is not None
    assert "编写" in manager.state().plan.steps[test_step - 1].objective


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
