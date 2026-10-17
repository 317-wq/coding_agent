import json
from pathlib import Path

from app.authorization import AuthorizationDecision
from app.change_set import TaskChangeSet
from app.rollback import AuthorizedRollback, RollbackStatus
from app.workspace import WorkspaceContext


class StubAuthorizer:
    def __init__(self, decision, callback=None):
        self.decision = decision
        self.callback = callback
        self.calls = []

    def authorize(self, tool_name, arguments):
        self.calls.append((tool_name, arguments))
        if self.callback:
            self.callback()
        return self.decision


def _changes(tmp_path: Path):
    existing = tmp_path / "existing.txt"
    removed = tmp_path / "removed.txt"
    existing.write_text("before", encoding="utf-8")
    removed.write_text("restore", encoding="utf-8")
    existing.chmod(0o640)
    changes = TaskChangeSet(WorkspaceContext.from_path(tmp_path))
    for path in ("existing.txt", "removed.txt", "created.txt"):
        changes.touch(path)
    existing.write_text("after", encoding="utf-8")
    existing.chmod(0o600)
    removed.unlink()
    (tmp_path / "created.txt").write_text("new", encoding="utf-8")
    return changes


def test_approved_rollback_restores_created_modified_and_deleted(tmp_path: Path):
    changes = _changes(tmp_path)
    authorizer = StubAuthorizer(AuthorizationDecision.allow())

    result = AuthorizedRollback(changes, authorizer).execute()

    assert result.status is RollbackStatus.RESTORED
    assert result.successful is True
    assert result.restored_paths == (
        "existing.txt",
        "removed.txt",
        "created.txt",
    )
    assert (tmp_path / "existing.txt").read_text(encoding="utf-8") == "before"
    assert (tmp_path / "existing.txt").stat().st_mode & 0o777 == 0o640
    assert (tmp_path / "removed.txt").read_text(encoding="utf-8") == "restore"
    assert not (tmp_path / "created.txt").exists()
    assert changes.summary().changes == ()
    assert json.loads(result.to_json()) == result.to_dict()


def test_denied_rollback_preserves_current_files(tmp_path: Path):
    changes = _changes(tmp_path)
    authorizer = StubAuthorizer(AuthorizationDecision.deny("用户拒绝"))

    result = AuthorizedRollback(changes, authorizer).execute()

    assert result.status is RollbackStatus.CANCELLED
    assert result.pending_paths == (
        "existing.txt",
        "removed.txt",
        "created.txt",
    )
    assert (tmp_path / "existing.txt").read_text(encoding="utf-8") == "after"
    assert (tmp_path / "created.txt").exists()


def test_authorization_receives_only_task_change_paths(tmp_path: Path):
    changes = _changes(tmp_path)
    unrelated = tmp_path / "unrelated.txt"
    unrelated.write_text("user change", encoding="utf-8")
    authorizer = StubAuthorizer(AuthorizationDecision.allow())

    AuthorizedRollback(changes, authorizer).execute()

    assert authorizer.calls == [
        (
            "rollback_task_changes",
            {
                "paths": ["existing.txt", "removed.txt", "created.txt"],
                "changed_count": 3,
            },
        )
    ]
    assert unrelated.read_text(encoding="utf-8") == "user change"


def test_change_during_authorization_is_reported_as_conflict(tmp_path: Path):
    changes = _changes(tmp_path)

    def external_change():
        (tmp_path / "existing.txt").write_text("external", encoding="utf-8")

    authorizer = StubAuthorizer(
        AuthorizationDecision.allow(), callback=external_change
    )
    result = AuthorizedRollback(changes, authorizer).execute()

    assert result.status is RollbackStatus.CONFLICT
    assert result.restored_paths == ()
    assert (tmp_path / "existing.txt").read_text(encoding="utf-8") == "external"


def test_no_changes_does_not_request_authorization(tmp_path: Path):
    changes = TaskChangeSet(WorkspaceContext.from_path(tmp_path))
    changes.touch("missing.txt")
    authorizer = StubAuthorizer(AuthorizationDecision.allow())

    result = AuthorizedRollback(changes, authorizer).execute()

    assert result.status is RollbackStatus.NO_CHANGES
    assert result.successful is True
    assert authorizer.calls == []
