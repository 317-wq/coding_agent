import pytest

from app.tools.result import ToolResult, ToolStatus


def test_tool_result_serializes_stable_fields():
    result = ToolResult.success("created")

    assert result.status is ToolStatus.SUCCESS
    assert result.to_dict() == {
        "status": "success",
        "result": "created",
        "error": None,
    }
    assert result.to_json() == (
        '{"status": "success", "result": "created", "error": null}'
    )


def test_failure_and_cancelled_results_have_distinct_fields():
    failure = ToolResult.failure("boom")
    cancelled = ToolResult.cancelled("用户取消")

    assert failure.to_dict() == {
        "status": "failure",
        "result": None,
        "error": "boom",
    }
    assert cancelled.to_dict() == {
        "status": "cancelled",
        "result": "用户取消",
        "error": None,
    }


def test_failure_factory_supplies_fallback_error():
    result = ToolResult.failure("")

    assert result.error == "工具执行失败，但未提供错误信息。"


@pytest.mark.parametrize(
    "result",
    [
        lambda: ToolResult(ToolStatus.SUCCESS, error="boom"),
        lambda: ToolResult(ToolStatus.FAILURE),
        lambda: ToolResult(ToolStatus.CANCELLED, error="boom"),
    ],
)
def test_tool_result_rejects_inconsistent_fields(result):
    with pytest.raises(ValueError):
        result()
