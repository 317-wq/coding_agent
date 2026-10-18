import json

import pytest

from app.core.task_result import (
    ChangeSummary,
    RecoveryRecord,
    TaskResult,
    TaskStatus,
    TestEvidence as Evidence,
    TestEvidenceStatus as EvidenceStatus,
)


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
        trace_event_count=12,
    )

    payload = json.loads(result.to_json())

    assert result.completed is True
    assert payload["changes"]["created"] == ["calculator.py"]
    assert payload["tests"][0]["status"] == "passed"
    assert payload["recovery"][0]["action"] == "continue"


def test_task_result_supports_blocked_and_not_run_test():
    result = TaskResult(
        task="运行测试",
        status=TaskStatus.BLOCKED,
        stop_reason="测试环境错误，需要用户处理。",
        tests=(Evidence("tests/test_app.py", EvidenceStatus.NOT_RUN),),
    )

    assert result.completed is False
    assert result.to_dict()["status"] == "blocked"


@pytest.mark.parametrize(
    "factory",
    [
        lambda: TaskResult("", TaskStatus.COMPLETED),
        lambda: TaskResult("任务", TaskStatus.COMPLETED, repair_attempts=-1),
        lambda: ChangeSummary(created=("",)),
        lambda: Evidence("", EvidenceStatus.NOT_RUN),
        lambda: RecoveryRecord("", "原因"),
    ],
)
def test_result_models_reject_invalid_fields(factory):
    with pytest.raises((TypeError, ValueError)):
        factory()
