import json

import pytest

from app.core.result_aggregator import TaskResultAggregator
from app.core.task_result import (
    ChangeSummary,
    FinalVerificationSummary,
    RecoveryRecord,
    TaskResult,
    TaskStatus,
    ToolCallSummary,
)
from app.core.task_result import (
    TestEvidence as Evidence,
)
from app.core.task_result import (
    TestEvidenceStatus as EvidenceStatus,
)
from app.runtime.trace import ExecutionTrace


def test_task_result_serializes_changes_tests_and_recovery():
    result = TaskResult(
        task="实现计算器",
        status=TaskStatus.COMPLETED,
        summary="功能已实现并通过指定测试。",
        changes=ChangeSummary(created=("calculator.py",), modified=("tests/test_calculator.py",)),
        tests=(
            Evidence(
                target="tests/test_calculator.py",
                status=EvidenceStatus.PASSED,
                exit_code=0,
            ),
        ),
        recovery=(RecoveryRecord("continue", "首次测试失败后自动修复"),),
        repair_attempts=1,
        model_request_count=3,
        tool_calls=ToolCallSummary(
            calls_started=2,
            calls_finished=2,
            succeeded=2,
        ),
        trace_event_count=12,
        final_verification=FinalVerificationSummary(
            verified=True,
            verified_path_count=2,
            net_changed_count=2,
            git_status="available",
            git_dirty=True,
            git_changed_count=2,
            test_evidence_count=1,
            evidence_sources=("file_content_hashes", "execution_trace"),
        ),
    )

    payload = json.loads(result.to_json())

    assert result.completed is True
    assert payload["changes"]["created"] == ["calculator.py"]
    assert payload["tests"][0]["status"] == "passed"
    assert payload["recovery"][0]["action"] == "continue"
    assert payload["model_request_count"] == 3
    assert payload["tool_calls"]["succeeded"] == 2
    assert payload["final_verification"]["verified"] is True


def test_task_result_supports_blocked_and_not_run_test():
    result = TaskResult(
        task="运行测试",
        status=TaskStatus.BLOCKED,
        stop_reason="测试环境错误，需要用户处理。",
        tests=(Evidence("tests/test_app.py", EvidenceStatus.NOT_RUN),),
    )

    assert result.completed is False
    assert result.to_dict()["status"] == "blocked"


def test_cancelled_rollback_is_not_an_unresolved_file_failure():
    """验证用户拒绝回滚表示保留修改，而不是回滚执行失败。"""
    trace = ExecutionTrace()
    trace.record(
        "rollback_finished",
        status="cancelled",
        pending_paths=("kept.py",),
    )
    trace.record(
        "rollback_finished",
        status="failed",
        pending_paths=("unresolved.py",),
    )

    assert TaskResultAggregator._unresolved_paths(trace) == ("unresolved.py",)


@pytest.mark.parametrize(
    "factory",
    [
        lambda: TaskResult("", TaskStatus.COMPLETED),
        lambda: TaskResult("任务", TaskStatus.COMPLETED, repair_attempts=-1),
        lambda: ToolCallSummary(calls_started=-1),
        lambda: FinalVerificationSummary(net_changed_count=-1),
        lambda: ChangeSummary(created=("",)),
        lambda: Evidence("", EvidenceStatus.NOT_RUN),
        lambda: RecoveryRecord("", "原因"),
    ],
)
def test_result_models_reject_invalid_fields(factory):
    with pytest.raises((TypeError, ValueError)):
        factory()
