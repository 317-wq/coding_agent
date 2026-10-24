"""关键 Trace 引用提取测试。"""

from app.core.trace_summary import TraceSummaryBuilder
from app.runtime.trace import ExecutionTrace


def test_extracts_limited_key_events_and_short_summary():
    """验证只保留关键事件并限制数量。"""
    trace = ExecutionTrace()
    trace.record("task_started", workspace="/safe/workspace")
    trace.record("tool_call_started", tool="read_file")
    trace.record("completion_checked", status="complete", reason="已完成")
    trace.record("task_completed", reason="完成")
    references = TraceSummaryBuilder().build(trace, limit=2)
    assert [item.event for item in references] == ["task_started", "completion_checked"]
    assert references[1].summary == "已完成"


def test_limited_trace_references_keep_final_verification():
    """验证关键事件较多时仍保留最终状态核验证据。"""
    trace = ExecutionTrace()
    trace.record("task_started")
    trace.record("completion_checked", status="complete")
    trace.record("task_completed")
    trace.record("final_state_verified", verified=True)

    references = TraceSummaryBuilder().build(trace, limit=2)

    assert references[0].event == "task_started"
    assert references[-1].event == "final_state_verified"
    assert "/safe/workspace" not in references[0].summary


def test_trace_reference_limit_must_be_positive():
    """验证 Trace 引用上限参数。"""
    trace = ExecutionTrace()
    try:
        TraceSummaryBuilder().build(trace, limit=0)
    except ValueError as exc:
        assert "上限" in str(exc)
    else:
        raise AssertionError("应拒绝非正引用上限")
