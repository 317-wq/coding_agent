from pathlib import Path

from app.recovery import RecoveryAction
from app.result import ToolResult
from app.task_safety import TaskSafetySession
from app.workspace import WorkspaceContext


def test_prepare_file_mutation_keeps_first_snapshot(tmp_path: Path):
    target = tmp_path / "module.py"
    target.write_text("before", encoding="utf-8")
    session = TaskSafetySession(WorkspaceContext.from_path(tmp_path))

    session.prepare_tool("edit_file", {"path": "module.py"})
    target.write_text("after", encoding="utf-8")
    session.prepare_tool("edit_file", {"path": "module.py"})

    snapshot = session.change_set.snapshot_store.get("module.py")
    assert snapshot.content == b"before"
    assert len(session.change_set.snapshot_store.snapshots) == 1


def test_prepare_directory_delete_captures_regular_files(tmp_path: Path):
    directory = tmp_path / "package"
    directory.mkdir()
    (directory / "one.py").write_text("one", encoding="utf-8")
    nested = directory / "nested"
    nested.mkdir()
    (nested / "two.py").write_text("two", encoding="utf-8")
    session = TaskSafetySession(WorkspaceContext.from_path(tmp_path))

    paths = session.prepare_tool("delete_directory", {"path": "package"})

    assert paths == ("package/nested/two.py", "package/one.py")


def test_observe_failure_uses_current_recoverable_paths(tmp_path: Path):
    target = tmp_path / "module.py"
    target.write_text("before", encoding="utf-8")
    session = TaskSafetySession(WorkspaceContext.from_path(tmp_path))
    session.prepare_tool("edit_file", {"path": "module.py"})
    target.write_text("after", encoding="utf-8")

    decision = session.observe(
        "edit_file", ToolResult.failure("修改失败"), plan_failed=True
    )

    assert decision.action is RecoveryAction.SUGGEST_ROLLBACK
    assert decision.recoverable_paths == ("module.py",)


def test_reset_after_recovery_starts_new_change_session(tmp_path: Path):
    session = TaskSafetySession(WorkspaceContext.from_path(tmp_path))
    session.prepare_tool("create_file", {"path": "new.py"})

    session.reset_after_recovery()

    assert session.recovery_attempts == 1
    assert session.change_set.summary().touched_count == 0
