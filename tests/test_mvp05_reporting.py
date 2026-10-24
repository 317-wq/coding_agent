"""MVP-0.5 任务报告场景的集成测试。"""

from app.cli.report import render_task_report
from app.core.completion import CompletionDecision, CompletionStatus
from app.core.context import TaskContext
from app.core.recovery import RecoveryAction, RecoverySignals, decide_recovery
from app.core.result_aggregator import TaskResultAggregator
from app.core.task_result import TaskStatus
from app.core.test_evidence import summarize_test_evidence
from app.core.trace_summary import TraceSummaryBuilder
from app.runtime.trace import ExecutionTrace
from app.tools.change_set import ChangeSetSummary


def _empty_changes() -> ChangeSetSummary:
    """创建没有文件变化的测试变更集。"""
    return ChangeSetSummary(touched_count=0, unchanged_count=0, changes=())


def test_success_report_contains_completed_status_and_trace():
    """覆盖成功任务的结构化结果、JSON 字段和 Trace 引用。"""
    trace = ExecutionTrace()
    trace.record("task_started")
    trace.record("task_completed", reason="完成")
    result = TaskResultAggregator().aggregate(
        TaskContext(
            "完成任务",
            test_results=("测试目标：tests/test_ok.py\n测试退出码：0\n测试结果：通过",),
        ),
        _empty_changes(),
        CompletionDecision(CompletionStatus.COMPLETE, "已完成"),
        trace,
    )
    assert result.status is TaskStatus.COMPLETED
    assert result.trace_references
    assert "状态：completed" in render_task_report(result)


def test_failed_test_then_repair_is_recorded():
    """覆盖测试失败后修复次数和最终通过证据。"""
    evidence = summarize_test_evidence(
        (
            "测试目标：tests/test_app.py\n测试退出码：1\n测试结果：失败\n测试错误类型：断言失败",
            "测试目标：tests/test_app.py\n测试退出码：0\n测试结果：通过",
        )
    )
    assert evidence[0].status.value == "failed"
    assert evidence[1].status.value == "passed"
    assert len(evidence) == 2


def test_repair_limit_requests_rollback():
    """覆盖达到两次自动修复上限后的回滚建议。"""
    decision = decide_recovery(
        RecoverySignals(test_failed=True, test_repair_attempts=2, recoverable_paths=("app.py",))
    )
    assert decision.action is RecoveryAction.SUGGEST_ROLLBACK
    assert "上限" in decision.reason


def test_rollback_success_and_user_denial_are_distinguishable():
    """覆盖回滚成功与用户拒绝回滚的 Trace 引用状态。"""
    for status, expected in (("restored", "rollback:restored"), ("cancelled", "rollback:cancelled")):
        trace = ExecutionTrace()
        trace.record("rollback_authorization_requested", paths=("app.py",))
        trace.record("rollback_finished", status=status, reason=status)
        references = TraceSummaryBuilder().build(trace)
        assert references[-1].event == "rollback_finished"
        assert expected.split(":")[1] in references[-1].summary or references[-1].summary == status


def test_environment_error_is_stopped_without_auto_repair():
    """覆盖测试环境错误不触发代码自动修复。"""
    decision = decide_recovery(
        RecoverySignals(test_failed=True, test_error_kind="environment", test_repair_attempts=0)
    )
    assert decision.action is RecoveryAction.STOP
    assert "环境" in decision.reason
