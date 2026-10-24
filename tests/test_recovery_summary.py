"""恢复记录摘要的结构化和渲染测试。"""

from app.core.recovery_summary import RecoverySummaryBuilder
from app.runtime.trace import ExecutionTrace


def test_reports_authorized_rollback():
    """验证授权回滚会记录授权结果和恢复路径。"""
    trace = ExecutionTrace()
    trace.record("rollback_authorization_requested", paths=("app.py",))
    trace.record(
        "rollback_finished",
        status="restored",
        reason="已恢复",
        restored_paths=("app.py",),
    )
    records = RecoverySummaryBuilder().build(trace)
    assert records[0].authorized is True
    assert records[0].restored_paths == ("app.py",)
    assert "已授权" in RecoverySummaryBuilder.render(records, repair_attempts=2)


def test_reports_denied_rollback_and_no_action():
    """验证用户拒绝回滚和未触发恢复均能清晰表达。"""
    trace = ExecutionTrace()
    trace.record("rollback_authorization_requested", paths=("app.py",))
    trace.record(
        "rollback_finished",
        status="cancelled",
        reason="用户拒绝回滚",
        pending_paths=("app.py",),
    )
    records = RecoverySummaryBuilder().build(trace)
    assert records[0].authorized is False
    assert "已拒绝" in RecoverySummaryBuilder.render(records)
    assert "未触发恢复动作" in RecoverySummaryBuilder.render(())
