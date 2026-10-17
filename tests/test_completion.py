import json

from app.completion import CompletionStatus, verify_completion
from app.planner import create_task_plan
from app.result import ToolResult
from app.task_state import PlanStateManager


def _completed_plan():
    plan = create_task_plan("修改代码")
    manager = PlanStateManager(plan)
    manager.start_step(1)
    manager.complete_step(1)
    return manager.state().plan


def test_completed_plan_requires_structured_evidence():
    decision = verify_completion(_completed_plan())

    assert decision.status is CompletionStatus.INCOMPLETE
    assert decision.completed is False
    assert "证据" in decision.reason


def test_successful_tool_and_test_results_complete_task():
    decision = verify_completion(
        _completed_plan(),
        [ToolResult.success("文件已写入")],
        ["pytest：通过"],
    )

    assert decision.status is CompletionStatus.COMPLETE
    assert decision.completed is True
    assert "文件已写入" in decision.evidence
    assert json.loads(decision.to_json()) == decision.to_dict()


def test_pending_steps_prevent_completion():
    plan = create_task_plan("修改代码")
    decision = verify_completion(plan, [ToolResult.success("已执行")])

    assert decision.status is CompletionStatus.INCOMPLETE
    assert decision.pending_step_ids == (1,)


def test_failed_tool_or_test_blocks_completion():
    plan = _completed_plan()
    tool_decision = verify_completion(plan, [ToolResult.failure("写入失败")])
    test_decision = verify_completion(plan, test_results=["测试结果：失败"])

    assert tool_decision.status is CompletionStatus.BLOCKED
    assert test_decision.status is CompletionStatus.BLOCKED


def test_failed_plan_step_blocks_completion():
    plan = create_task_plan("修改代码")
    manager = PlanStateManager(plan)
    manager.start_step(1)
    manager.fail_step(1, "执行异常")

    decision = verify_completion(
        manager.state().plan, [ToolResult.success("已执行")]
    )
    assert decision.status is CompletionStatus.BLOCKED
    assert decision.failed_step_ids == (1,)


def test_only_successful_test_text_counts_as_test_evidence():
    plan = _completed_plan()
    decision = verify_completion(plan, test_results=["检查完成，但未运行测试"])

    assert decision.status is CompletionStatus.INCOMPLETE
