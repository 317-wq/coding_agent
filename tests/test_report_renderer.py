"""CLI 任务报告渲染测试。"""

import json

from app.cli.report import render_task_report
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


def _result() -> TaskResult:
    """创建同时包含变更、测试和恢复证据的任务结果。"""
    return TaskResult(
        task="实现功能",
        status=TaskStatus.COMPLETED,
        summary="完成",
        changes=ChangeSummary(created=("app.py",)),
        tests=(Evidence("tests/test_app.py", EvidenceStatus.PASSED, 0),),
        recovery=(RecoveryRecord("continue", "无需回滚"),),
        repair_attempts=1,
        model_request_count=2,
        tool_calls=ToolCallSummary(
            calls_started=2,
            calls_finished=2,
            succeeded=1,
            failed=1,
        ),
        trace_event_count=4,
        final_verification=FinalVerificationSummary(
            verified=True,
            verified_path_count=1,
            net_changed_count=1,
            git_status="available",
            git_dirty=True,
            git_changed_count=1,
            test_evidence_count=1,
            evidence_sources=("file_content_hashes", "execution_trace"),
        ),
    )


def test_text_report_contains_same_structured_sections():
    """验证中文报告包含状态、变更、测试和恢复信息。"""
    report = render_task_report(_result())
    assert "状态：completed" in report
    assert "创建（1）" in report
    assert "tests/test_app.py：通过" in report
    assert "自动修复 1 次" in report
    assert "最终状态核验：" in report
    assert "文件：按任务初始状态核验 1 个路径，当前净变化 1 个" in report
    assert "模型请求 2 次；工具调用开始 2 次、完成 2 次" in report


def test_json_report_is_task_result_json():
    """验证 JSON 输出与 TaskResult 序列化数据一致。"""
    payload = json.loads(render_task_report(_result(), "json"))
    assert payload["status"] == "completed"
    assert payload["changes"]["created"] == ["app.py"]


def test_text_report_shows_next_steps_for_incomplete_task():
    """验证文本报告展示未完成任务的下一步建议。"""
    result = TaskResult(
        task="修复功能",
        status=TaskStatus.BLOCKED,
        summary="测试失败",
        stop_reason="测试失败",
        next_steps=("修复失败测试后重新运行指定测试。",),
    )
    report = render_task_report(result)
    assert "下一步建议：" in report
    assert "修复失败测试" in report


def test_text_report_shows_trace_references():
    """验证报告展示关键 Trace 引用而非完整事件载荷。"""
    from app.core.trace_summary import TraceReference

    result = TaskResult(
        task="读取文件",
        status=TaskStatus.COMPLETED,
        trace_references=(TraceReference("task_completed", "now", "完成"),),
    )
    report = render_task_report(result)
    assert "关键 Trace：" in report
    assert "task_completed" in report
