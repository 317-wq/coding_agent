import json

from app.core.completion import CompletionStatus, verify_completion
from app.core.planner import create_task_plan
from app.core.task_state import PlanStateManager
from app.tools.result import ToolResult


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


def test_target_test_evidence_cannot_complete_pending_plan():
    """验证测试通过不能覆盖仍未完成的必要计划步骤。"""
    plan = create_task_plan("修改代码")
    decision = verify_completion(
        plan,
        [ToolResult.success("修改文件成功并已核验")],
        ["测试目标：tests/test_calculator.py\n测试结果：通过"],
    )

    assert decision.status is CompletionStatus.INCOMPLETE
    assert decision.completed is False
    assert decision.pending_step_ids == (1,)


def test_failed_tool_preserves_all_pending_steps_in_blocked_decision():
    """验证工具失败时返回 blocked，并保留全部待处理步骤。"""
    plan = create_task_plan("读取配置，然后修改代码，然后运行测试")
    decision = verify_completion(plan, [ToolResult.failure("读取失败")])

    assert decision.status is CompletionStatus.BLOCKED
    assert decision.completed is False
    assert decision.pending_step_ids == (1, 2, 3)
    assert decision.evidence == ("读取失败",)


def test_cancelled_tool_is_not_success_evidence_for_simple_task():
    """验证被取消的工具调用不能让简单任务判定完成。"""
    from app.core.completion import CompletionVerifier

    decision = CompletionVerifier().verify_basic(
        [ToolResult.cancelled("用户取消")]
    )
    assert decision.status is CompletionStatus.BLOCKED
    assert decision.completed is False


def test_latest_result_for_same_test_target_supersedes_old_failure():
    """验证代码修复后的成功重跑可以覆盖同一目标的历史失败。"""
    from app.core.completion import CompletionVerifier

    decision = CompletionVerifier().verify_basic(
        test_results=(
            "测试目标：tests\n测试退出码：1\n测试结果：失败",
            "测试目标：tests\n测试退出码：0\n测试结果：通过\n2 passed",
        )
    )

    assert decision.status is CompletionStatus.COMPLETE
