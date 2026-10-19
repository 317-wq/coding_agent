import json

import pytest

from app.core.completion import CompletionStatus
from app.core.recovery import (
    RecoveryAction,
    RecoveryPolicy,
    RecoverySignals,
    decide_recovery,
)


def test_no_failure_signal_continues_execution():
    decision = decide_recovery(RecoverySignals())

    assert decision.action is RecoveryAction.CONTINUE
    assert decision.triggers == ()


def test_transient_tool_failures_continue_below_threshold():
    decision = decide_recovery(RecoverySignals(consecutive_tool_failures=2))

    assert decision.action is RecoveryAction.CONTINUE
    assert decision.triggers == ("连续工具失败 2 次",)


@pytest.mark.parametrize(
    "signals",
    [
        RecoverySignals(consecutive_tool_failures=3),
        RecoverySignals(test_failed=True),
        RecoverySignals(plan_failed=True),
    ],
)
def test_hard_failure_without_changes_requests_replan(signals):
    decision = decide_recovery(signals)

    assert decision.action is RecoveryAction.REPLAN


def test_test_failure_with_changes_prioritizes_repair():
    decision = decide_recovery(
        RecoverySignals(
            test_failed=True,
            plan_failed=True,
            recoverable_paths=("app/main.py", "app/main.py", "tests/test_main.py"),
        )
    )

    assert decision.action is RecoveryAction.REPLAN
    assert decision.recoverable_paths == ("app/main.py", "tests/test_main.py")
    assert decision.triggers == ("测试失败", "计划失败")


def test_test_repair_attempt_limit_requests_rollback_decision():
    decision = decide_recovery(
        RecoverySignals(
            test_failed=True,
            test_repair_attempts=2,
            recoverable_paths=("app/main.py",),
        )
    )

    assert decision.action is RecoveryAction.SUGGEST_ROLLBACK
    assert "上限" in decision.reason


def test_collection_error_replans_for_code_repair():
    """验证测试收集失败会进入代码分析修复，而不是立即停止。"""
    decision = decide_recovery(
        RecoverySignals(
            test_error_kind="collection",
            recoverable_paths=("app/main.py",),
        )
    )

    assert decision.action is RecoveryAction.REPLAN
    assert "收集失败" in decision.reason
    assert json.loads(decision.to_json()) == decision.to_dict()


def test_code_import_error_replans_until_repair_limit():
    """验证循环导入类错误允许两轮修改，达到上限后才建议回滚。"""
    first = decide_recovery(
        RecoverySignals(test_error_kind="import", test_repair_attempts=0)
    )
    exhausted = decide_recovery(
        RecoverySignals(
            test_error_kind="import",
            test_repair_attempts=2,
            recoverable_paths=("calculator.py",),
        )
    )

    assert first.action is RecoveryAction.REPLAN
    assert "项目代码导入失败" in first.reason
    assert exhausted.action is RecoveryAction.SUGGEST_ROLLBACK


def test_environment_error_still_stops_without_code_changes():
    """验证真实测试环境错误不会误导 Agent 修改业务代码。"""
    decision = decide_recovery(RecoverySignals(test_error_kind="environment"))

    assert decision.action is RecoveryAction.STOP
    assert "环境" in decision.reason


def test_blocked_completion_without_changes_stops():
    decision = decide_recovery(
        RecoverySignals(completion_status=CompletionStatus.BLOCKED)
    )

    assert decision.action is RecoveryAction.STOP
    assert decision.triggers == ("完成判定阻塞",)


def test_recovery_attempt_limit_stops_even_with_recoverable_changes():
    decision = RecoveryPolicy(max_recovery_attempts=2).decide(
        RecoverySignals(
            test_failed=True,
            recoverable_paths=("app/main.py",),
            recovery_attempts=2,
        )
    )

    assert decision.action is RecoveryAction.STOP
    assert decision.recoverable_paths == ("app/main.py",)


def test_incomplete_completion_is_not_a_failure_trigger():
    decision = decide_recovery(
        RecoverySignals(completion_status=CompletionStatus.INCOMPLETE)
    )

    assert decision.action is RecoveryAction.CONTINUE


def test_signal_and_policy_limits_are_validated():
    with pytest.raises(ValueError, match="失败次数"):
        RecoverySignals(consecutive_tool_failures=-1)
    with pytest.raises(ValueError, match="路径不能为空"):
        RecoverySignals(recoverable_paths=(" ",))
    with pytest.raises(ValueError, match="失败阈值"):
        RecoveryPolicy(consecutive_failure_threshold=0)
    with pytest.raises(ValueError, match="恢复尝试上限"):
        RecoveryPolicy(max_recovery_attempts=0)
