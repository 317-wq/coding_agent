import json

from app.runtime.trace import REDACTED, ExecutionTrace


def test_trace_records_workspace_and_structured_tool_statistics(tmp_path):
    trace = ExecutionTrace()
    trace.record(
        "task_started", task="inspect project", workspace=str(tmp_path)
    )

    for status in ("success", "failure", "cancelled"):
        trace.record("tool_call_started", tool=f"tool-{status}")
        trace.record(
            "tool_call_finished",
            tool=f"tool-{status}",
            status=status,
            result="done" if status != "failure" else None,
            error="boom" if status == "failure" else None,
        )

    trace.record("task_completed", answer="done")
    statistics = trace.get_statistics()

    assert statistics["workspace"] == str(tmp_path)
    assert statistics["tool_calls"] == {
        "calls_started": 3,
        "calls_finished": 3,
        "succeeded": 1,
        "failed": 1,
        "cancelled": 1,
        "unfinished": 0,
    }


def test_trace_keeps_unfinished_count_with_structured_results():
    trace = ExecutionTrace()
    trace.record("task_started", task="test", workspace="/workspace")
    trace.record("tool_call_started", tool="unfinished")

    assert trace.get_statistics()["tool_calls"]["unfinished"] == 1


def test_trace_assigns_stable_id_and_monotonic_sequence():
    """验证同一任务事件共享 Trace 标识并具有严格递增序号。"""
    trace = ExecutionTrace(trace_id="trace-test")
    trace.record("task_started")
    trace.record("task_completed")

    events = trace.get_events()
    assert [event["trace_id"] for event in events] == [
        "trace-test",
        "trace-test",
    ]
    assert [event["sequence"] for event in events] == [1, 2]
    assert trace.get_statistics()["trace_id"] == "trace-test"


def test_trace_sanitizes_secrets_and_sensitive_file_paths():
    trace = ExecutionTrace()
    trace.record(
        "tool_call_finished",
        status="failure",
        arguments={
            "path": ".env",
            "api_key": "plain-secret",
        },
        error=(
            "api_key=secret-value; "
            "Authorization: Bearer abc.def; failed reading /tmp/project/.env"
        ),
    )

    serialized = json.dumps(trace.get_events(), ensure_ascii=False)

    assert "plain-secret" not in serialized
    assert "secret-value" not in serialized
    assert "abc.def" not in serialized
    assert "/tmp/project/.env" not in serialized
    assert serialized.count(REDACTED) >= 4
