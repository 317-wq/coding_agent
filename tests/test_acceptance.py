"""覆盖验收项到证据的映射，以及完成判定对验收项的要求。"""

from pathlib import Path

from app.core.acceptance import AcceptanceResult, AcceptanceVerifier
from app.core.completion import CompletionStatus, verify_completion
from app.core.intent import AcceptanceItem, EvidenceKind, TaskIntent
from app.core.planner import create_task_plan
from app.core.task_result import TestEvidence, TestEvidenceStatus
from app.core.task_state import PlanStateManager
from app.runtime.workspace import WorkspaceContext
from app.tools.change_set import TaskChangeSet
from app.tools.result import ToolResult


def _workspace(tmp_path: Path) -> WorkspaceContext:
    """构造临时工作区。"""
    return WorkspaceContext.from_path(tmp_path)


def _intent(*items: AcceptanceItem) -> TaskIntent:
    """构造只用于核验的意图。"""
    return TaskIntent(objective="目标", acceptance=tuple(items))


def _item(kind: EvidenceKind, target: str | None = None, item_id: int = 1) -> AcceptanceItem:
    """构造一条验收项。"""
    return AcceptanceItem(item_id=item_id, text=f"{kind.value}", kind=kind, target=target)


def test_file_exists_reads_workspace(tmp_path: Path):
    """FILE_EXISTS 只认工作区里的真实文件。"""
    (tmp_path / "module.py").write_text("x", encoding="utf-8")
    workspace = _workspace(tmp_path)
    changes = TaskChangeSet(workspace).summary()

    passed = AcceptanceVerifier().verify(
        _intent(_item(EvidenceKind.FILE_EXISTS, "module.py")), workspace, changes
    )
    missing = AcceptanceVerifier().verify(
        _intent(_item(EvidenceKind.FILE_EXISTS, "other.py")), workspace, changes
    )

    assert passed[0].passed is True
    assert missing[0].passed is False


def test_file_changed_uses_change_set(tmp_path: Path):
    """FILE_CHANGED 只认变更集里的净变化。"""
    workspace = _workspace(tmp_path)
    change_set = TaskChangeSet(workspace)
    change_set.touch("module.py")
    (tmp_path / "module.py").write_text("x", encoding="utf-8")
    changes = change_set.summary()

    result = AcceptanceVerifier().verify(
        _intent(_item(EvidenceKind.FILE_CHANGED, "module.py")), workspace, changes
    )

    assert result[0].passed is True


def test_test_passed_requires_matching_evidence(tmp_path: Path):
    """TEST_PASSED 需要目标匹配的通过证据。"""
    workspace = _workspace(tmp_path)
    changes = TaskChangeSet(workspace).summary()
    evidence = (
        TestEvidence(
            target="tests/test_calc.py",
            status=TestEvidenceStatus.PASSED,
            exit_code=0,
        ),
    )

    matched = AcceptanceVerifier().verify(
        _intent(_item(EvidenceKind.TEST_PASSED, "tests/test_calc.py")),
        workspace,
        changes,
        evidence,
    )
    absent = AcceptanceVerifier().verify(
        _intent(_item(EvidenceKind.TEST_PASSED, "tests/test_other.py")),
        workspace,
        changes,
        evidence,
    )

    assert matched[0].passed is True
    assert absent[0].passed is False


def test_test_passed_without_evidence_fails(tmp_path: Path):
    """没有测试证据时验收不通过。"""
    workspace = _workspace(tmp_path)
    changes = TaskChangeSet(workspace).summary()

    result = AcceptanceVerifier().verify(
        _intent(_item(EvidenceKind.TEST_PASSED)), workspace, changes
    )

    assert result[0].passed is False
    assert "没有测试证据" in result[0].detail


def test_content_contains_scans_sources(tmp_path: Path):
    """CONTENT_CONTAINS 在源码中查找符号。"""
    (tmp_path / "calc.py").write_text("raise ZeroDivisionError()\n", encoding="utf-8")
    workspace = _workspace(tmp_path)
    changes = TaskChangeSet(workspace).summary()

    found = AcceptanceVerifier().verify(
        _intent(_item(EvidenceKind.CONTENT_CONTAINS, "ZeroDivisionError")),
        workspace,
        changes,
    )
    absent = AcceptanceVerifier().verify(
        _intent(_item(EvidenceKind.CONTENT_CONTAINS, "ValueError")),
        workspace,
        changes,
    )

    assert found[0].passed is True
    assert absent[0].passed is False


def test_workspace_read_requires_successful_read(tmp_path: Path):
    """WORKSPACE_READ 只认成功的读取记录。"""
    workspace = _workspace(tmp_path)
    changes = TaskChangeSet(workspace).summary()

    read = AcceptanceVerifier().verify(
        _intent(_item(EvidenceKind.WORKSPACE_READ)),
        workspace,
        changes,
        workspace_read=True,
    )
    not_read = AcceptanceVerifier().verify(
        _intent(_item(EvidenceKind.WORKSPACE_READ)),
        workspace,
        changes,
        workspace_read=False,
    )

    assert read[0].passed is True
    assert not_read[0].passed is False


def test_completion_requires_all_acceptance_items(tmp_path: Path):
    """完成判定在验收项未满足时返回 incomplete。"""
    plan = create_task_plan("修改代码，然后运行测试")
    manager = PlanStateManager(plan)
    for step in manager.state().plan.steps:
        manager.start_step(step.step_id)
        manager.complete_step(step.step_id)
    unmet = AcceptanceResult(
        item=AcceptanceItem(
            item_id=1, text="测试通过", kind=EvidenceKind.TEST_PASSED
        ),
        passed=False,
        detail="没有测试证据",
    )

    decision = verify_completion(
        manager.state().plan,
        [ToolResult.success("已执行")],
        [],
        [unmet],
    )

    assert decision.status is CompletionStatus.INCOMPLETE
    assert "未满足验收条件" in decision.reason
