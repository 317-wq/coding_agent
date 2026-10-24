"""结果汇总器从结构化运行证据生成任务结果的测试。"""

from app.core.completion import CompletionDecision, CompletionStatus
from app.core.context import TaskContext
from app.core.final_verification import FinalStateEvidence
from app.core.result_aggregator import TaskResultAggregator
from app.core.task_result import (
    FinalVerificationSummary,
    ToolCallSummary,
)
from app.core.task_result import (
    TestEvidence as Evidence,
)
from app.core.task_result import (
    TestEvidenceStatus as EvidenceStatus,
)
from app.runtime.trace import ExecutionTrace
from app.tools.change_set import ChangeSetSummary
from app.tools.git_repository import GitRepositoryStatus, GitWorkingTreeState


def _changes() -> ChangeSetSummary:
    return ChangeSetSummary(touched_count=2, unchanged_count=0, changes=())


def test_aggregator_uses_actual_changes_and_parses_test_evidence():
    trace = ExecutionTrace()
    trace.record("task_started", workspace="/tmp/workspace")
    trace.record("task_completed", reason="done")
    context = TaskContext(
        task="实现功能",
        test_results=(
            "测试目标：tests/test_app.py\n测试退出码：0\n测试结果：通过",
        ),
    )
    # ChangeSetSummary 的路径属性来自 changes，不能由模型文本补充。
    from app.tools.change_set import FileChange, FileChangeKind

    changes = ChangeSetSummary(
        touched_count=1,
        unchanged_count=0,
        changes=(FileChange("app.py", FileChangeKind.MODIFIED, 1, 2, "a", "b", 0o644, 0o644),),
    )
    result = TaskResultAggregator().aggregate(
        context,
        changes,
        CompletionDecision(CompletionStatus.COMPLETE, "完成"),
        trace,
    )
    assert result.status.value == "completed"
    assert result.changes.modified == ("app.py",)
    assert result.tests[0].target == "tests/test_app.py"
    assert result.tests[0].exit_code == 0
    assert result.trace_event_count == 2


def test_cancelled_rollback_keeps_changes_without_marking_them_unresolved():
    """验证用户拒绝回滚表示保留修改，不代表文件恢复冲突。"""
    trace = ExecutionTrace()
    trace.record("task_started")
    trace.record(
        "rollback_finished",
        status="cancelled",
        reason="用户拒绝回滚",
        restored_paths=(),
        pending_paths=("app.py",),
    )
    result = TaskResultAggregator().aggregate(
        TaskContext(
            task="修复功能",
            test_results=(
                "测试目标：tests/test_app.py\n测试退出码：1\n测试结果：失败\n测试错误类型：断言失败",
            ),
        ),
        _changes(),
        CompletionDecision(CompletionStatus.BLOCKED, "测试失败，等待用户处理"),
        trace,
        repair_attempts=2,
    )
    assert result.status.value == "blocked"
    assert result.stop_reason == "测试失败，等待用户处理"
    assert result.changes.unresolved == ()
    assert result.tests[0].error_kind == "断言失败"
    assert result.repair_attempts == 2
    assert result.recovery[0].action == "rollback:cancelled"


def test_failed_rollback_reports_pending_paths_as_unresolved():
    """验证真正失败的回滚仍会报告尚未恢复的文件。"""
    trace = ExecutionTrace()
    trace.record(
        "rollback_finished",
        status="failed",
        reason="恢复写入失败",
        restored_paths=(),
        pending_paths=("app.py",),
    )

    result = TaskResultAggregator().aggregate(
        TaskContext(task="修复功能"),
        _changes(),
        CompletionDecision(CompletionStatus.BLOCKED, "回滚失败"),
        trace,
    )

    assert result.changes.unresolved == ("app.py",)


def test_final_failed_test_evidence_overrides_model_completion():
    """验证模型声称完成时，最终失败测试仍会把报告降级为阻塞。"""
    trace = ExecutionTrace()
    trace.record("task_started")
    trace.record("task_completed", answer="所有测试通过")
    tests = (
        Evidence(
            "tests/test_app.py",
            EvidenceStatus.FAILED,
            exit_code=1,
        ),
    )
    final_evidence = FinalStateEvidence(
        changes=_changes(),
        tests=tests,
        git=GitWorkingTreeState(GitRepositoryStatus.NOT_REPOSITORY),
        model_request_count=1,
        tool_calls=ToolCallSummary(calls_started=1, calls_finished=1, failed=1),
        trace_event_count=2,
        summary=FinalVerificationSummary(
            verified=True,
            test_evidence_count=1,
            evidence_sources=("test_trace",),
        ),
    )

    result = TaskResultAggregator().aggregate(
        TaskContext(task="修复功能"),
        _changes(),
        CompletionDecision(CompletionStatus.COMPLETE, "模型判定完成"),
        trace,
        final_evidence=final_evidence,
    )

    assert result.status.value == "blocked"
    assert result.completed is False
    assert result.stop_reason == "最终状态核验发现最新测试证据未通过。"
    assert result.summary == result.stop_reason
    assert result.tests == tests
    assert result.tool_calls.failed == 1


def test_passing_parent_test_overrides_historical_child_failure():
    """验证父级测试成功后最终报告不再被历史子用例失败阻塞。"""
    trace = ExecutionTrace()
    trace.record("task_started")
    trace.record("task_completed", answer="完整测试通过")
    tests = (
        Evidence(
            "tests/test_app.py::TestApp::test_one",
            EvidenceStatus.FAILED,
            exit_code=1,
        ),
        Evidence("tests/test_app.py", EvidenceStatus.PASSED, exit_code=0),
    )
    final_evidence = FinalStateEvidence(
        changes=_changes(),
        tests=tests,
        git=GitWorkingTreeState(GitRepositoryStatus.NOT_REPOSITORY),
        model_request_count=1,
        tool_calls=ToolCallSummary(calls_started=2, calls_finished=2),
        trace_event_count=2,
        summary=FinalVerificationSummary(
            verified=True,
            test_evidence_count=2,
            evidence_sources=("test_trace",),
        ),
    )

    result = TaskResultAggregator().aggregate(
        TaskContext(task="修复功能"),
        _changes(),
        CompletionDecision(CompletionStatus.COMPLETE, "完整测试通过"),
        trace,
        final_evidence=final_evidence,
    )

    assert result.completed is True
