import json
from pathlib import Path

import pytest

from app.change_set import ChangeSetError, FileChangeKind, TaskChangeSet
from app.change_snapshot import TaskSnapshotStore
from app.workspace import WorkspaceContext


def test_reports_created_modified_deleted_and_unchanged_files(tmp_path: Path):
    (tmp_path / "modified.txt").write_text("before", encoding="utf-8")
    (tmp_path / "deleted.txt").write_text("delete", encoding="utf-8")
    (tmp_path / "same.txt").write_text("same", encoding="utf-8")
    changes = TaskChangeSet(WorkspaceContext.from_path(tmp_path))
    for path in ("created.txt", "modified.txt", "deleted.txt", "same.txt"):
        changes.touch(path)

    (tmp_path / "created.txt").write_text("created", encoding="utf-8")
    (tmp_path / "modified.txt").write_text("after", encoding="utf-8")
    (tmp_path / "deleted.txt").unlink()

    summary = changes.summary()
    assert summary.created_paths == ("created.txt",)
    assert summary.modified_paths == ("modified.txt",)
    assert summary.deleted_paths == ("deleted.txt",)
    assert summary.touched_count == 4
    assert summary.unchanged_count == 1
    assert json.loads(summary.to_json()) == summary.to_dict()


def test_summary_recomputes_current_state(tmp_path: Path):
    target = tmp_path / "module.py"
    target.write_text("before", encoding="utf-8")
    changes = TaskChangeSet(WorkspaceContext.from_path(tmp_path))
    changes.touch("module.py")

    target.write_text("after", encoding="utf-8")
    assert changes.summary().modified_paths == ("module.py",)
    target.write_text("before", encoding="utf-8")
    summary = changes.summary()
    assert summary.changes == ()
    assert summary.unchanged_count == 1


def test_permission_only_change_is_modified(tmp_path: Path):
    target = tmp_path / "script.sh"
    target.write_text("echo ok\n", encoding="utf-8")
    target.chmod(0o644)
    changes = TaskChangeSet(WorkspaceContext.from_path(tmp_path))
    changes.touch("script.sh")

    target.chmod(0o755)
    change = changes.summary().changes[0]
    assert change.kind is FileChangeKind.MODIFIED
    assert change.to_dict()["before_mode"] == "0644"
    assert change.to_dict()["after_mode"] == "0755"


def test_created_then_removed_path_has_no_net_change(tmp_path: Path):
    changes = TaskChangeSet(WorkspaceContext.from_path(tmp_path))
    changes.touch("temporary.txt")
    target = tmp_path / "temporary.txt"
    target.write_text("temporary", encoding="utf-8")
    target.unlink()

    assert changes.summary().changes == ()


def test_rejects_workspace_mismatch_and_unsafe_current_state(tmp_path: Path):
    other = tmp_path / "other"
    other.mkdir()
    workspace = WorkspaceContext.from_path(tmp_path)
    foreign_store = TaskSnapshotStore(WorkspaceContext.from_path(other))
    with pytest.raises(ValueError, match="同一工作区"):
        TaskChangeSet(workspace, foreign_store)

    changes = TaskChangeSet(workspace)
    changes.touch("target.txt")
    (tmp_path / "target.txt").mkdir()
    with pytest.raises(ChangeSetError, match="不是普通文件"):
        changes.summary()


def test_rejects_current_file_over_size_limit(tmp_path: Path):
    store = TaskSnapshotStore(
        WorkspaceContext.from_path(tmp_path), max_file_size=4
    )
    changes = TaskChangeSet(WorkspaceContext.from_path(tmp_path), store)
    changes.touch("new.txt")
    (tmp_path / "new.txt").write_bytes(b"12345")

    with pytest.raises(ChangeSetError, match="变更摘要上限"):
        changes.summary()
