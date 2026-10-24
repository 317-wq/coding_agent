"""未完成任务下一步建议测试。"""

from app.core.task_result import ChangeSummary, TaskStatus
from app.core.task_result import TestEvidence as Evidence
from app.core.task_result import TestEvidenceStatus as EvidenceStatus
from app.core.task_suggestions import build_task_suggestions


def test_blocked_task_gets_actionable_suggestions():
    """验证测试失败、未恢复文件和阻塞状态均产生明确建议。"""
    suggestions = build_task_suggestions(
        TaskStatus.BLOCKED,
        "测试失败",
        ChangeSummary(unresolved=("app.py",)),
        (Evidence("tests/test_app.py", EvidenceStatus.FAILED, 1),),
    )
    assert any("修复失败测试" in item for item in suggestions)
    assert any("授权回滚" in item for item in suggestions)
    assert any("阻塞原因" in item for item in suggestions)


def test_completed_task_has_no_next_steps():
    """验证已完成任务不会输出误导性的后续建议。"""
    assert build_task_suggestions(TaskStatus.COMPLETED, None, ChangeSummary(), ()) == ()
