import subprocess
from pathlib import Path

from app.core.recovery import RecoveryAction, RecoverySignals, decide_recovery
from app.runtime.workspace import WorkspaceContext
from app.tools.authorization import AuthorizationDecision
from app.tools.change_set import TaskChangeSet
from app.tools.git_repository import GitRepositoryStatus, inspect_git_repository
from app.tools.rollback import AuthorizedRollback, RollbackStatus


class StubAuthorizer:
    def __init__(self, decision):
        self.decision = decision
        self.calls = []

    def authorize(self, tool_name, arguments):
        self.calls.append((tool_name, arguments))
        return self.decision


def _git_init(path: Path) -> None:
    subprocess.run(
        ["git", "init", "--initial-branch=main", str(path)],
        capture_output=True,
        check=True,
        text=True,
    )


def test_non_git_workspace_still_supports_snapshot_and_rollback(tmp_path: Path):
    workspace = WorkspaceContext.from_path(tmp_path)
    assert inspect_git_repository(workspace).status is GitRepositoryStatus.NOT_REPOSITORY

    target = tmp_path / "module.py"
    target.write_text("before", encoding="utf-8")
    changes = TaskChangeSet(workspace)
    changes.touch("module.py")
    target.write_text("after", encoding="utf-8")

    result = AuthorizedRollback(
        changes, StubAuthorizer(AuthorizationDecision.allow())
    ).execute()

    assert result.status is RollbackStatus.RESTORED
    assert target.read_text(encoding="utf-8") == "before"


def test_dirty_workspace_change_is_not_treated_as_task_change(tmp_path: Path):
    _git_init(tmp_path)
    user_file = tmp_path / "user.txt"
    user_file.write_text("user dirty", encoding="utf-8")
    agent_file = tmp_path / "agent.txt"
    agent_file.write_text("before", encoding="utf-8")
    changes = TaskChangeSet(WorkspaceContext.from_path(tmp_path))
    changes.touch("agent.txt")
    agent_file.write_text("agent dirty", encoding="utf-8")

    result = AuthorizedRollback(
        changes, StubAuthorizer(AuthorizationDecision.allow())
    ).execute()

    assert result.status is RollbackStatus.RESTORED
    assert user_file.read_text(encoding="utf-8") == "user dirty"
    assert agent_file.read_text(encoding="utf-8") == "before"


def test_rollback_covers_create_modify_delete_in_one_task(tmp_path: Path):
    existing = tmp_path / "existing.txt"
    removed = tmp_path / "removed.txt"
    existing.write_text("old", encoding="utf-8")
    removed.write_text("restore", encoding="utf-8")
    changes = TaskChangeSet(WorkspaceContext.from_path(tmp_path))
    for path in ("existing.txt", "removed.txt", "created.txt"):
        changes.touch(path)
    existing.write_text("new", encoding="utf-8")
    removed.unlink()
    (tmp_path / "created.txt").write_text("created", encoding="utf-8")

    result = AuthorizedRollback(
        changes, StubAuthorizer(AuthorizationDecision.allow())
    ).execute()

    assert result.status is RollbackStatus.RESTORED
    assert existing.read_text(encoding="utf-8") == "old"
    assert removed.read_text(encoding="utf-8") == "restore"
    assert not (tmp_path / "created.txt").exists()


def test_denied_authorization_keeps_all_task_changes(tmp_path: Path):
    target = tmp_path / "module.py"
    target.write_text("before", encoding="utf-8")
    changes = TaskChangeSet(WorkspaceContext.from_path(tmp_path))
    changes.touch("module.py")
    target.write_text("after", encoding="utf-8")
    authorizer = StubAuthorizer(AuthorizationDecision.deny("用户拒绝"))

    result = AuthorizedRollback(changes, authorizer).execute()

    assert result.status is RollbackStatus.CANCELLED
    assert target.read_text(encoding="utf-8") == "after"
    assert authorizer.calls[0][0] == "rollback_task_changes"


def test_recovery_action_marks_replan_after_failed_execution():
    decision = decide_recovery(
        RecoverySignals(plan_failed=True, recovery_attempts=0)
    )

    assert decision.action is RecoveryAction.REPLAN
