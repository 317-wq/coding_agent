import json
import subprocess
from pathlib import Path

from app.runtime.workspace import WorkspaceContext
from app.tools.git_repository import (
    GitRepositoryStatus,
    inspect_git_repository,
    inspect_git_worktree,
)


def _git_init(path: Path) -> None:
    subprocess.run(
        ["git", "init", "--initial-branch=main", str(path)],
        capture_output=True,
        check=True,
        text=True,
    )


def test_identifies_git_repository_and_branch(tmp_path: Path):
    _git_init(tmp_path)

    info = inspect_git_repository(WorkspaceContext.from_path(tmp_path))

    assert info.status is GitRepositoryStatus.AVAILABLE
    assert info.available is True
    assert info.repository_root == tmp_path.resolve()
    assert info.branch == "main"
    assert info.detached_head is False
    assert json.loads(info.to_json()) == info.to_dict()


def test_workspace_subdirectory_resolves_repository_root(tmp_path: Path):
    _git_init(tmp_path)
    child = tmp_path / "src" / "feature"
    child.mkdir(parents=True)

    info = inspect_git_repository(WorkspaceContext.from_path(child))

    assert info.repository_root == tmp_path.resolve()
    assert info.branch == "main"


def test_non_git_workspace_degrades_explicitly(tmp_path: Path):
    info = inspect_git_repository(WorkspaceContext.from_path(tmp_path))

    assert info.status is GitRepositoryStatus.NOT_REPOSITORY
    assert info.available is False
    assert info.repository_root is None
    assert info.reason == "工作区不在 Git 仓库中。"


def test_missing_git_executable_degrades_explicitly(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("app.tools.git_repository.shutil.which", lambda name: None)

    info = inspect_git_repository(WorkspaceContext.from_path(tmp_path))

    assert info.status is GitRepositoryStatus.GIT_UNAVAILABLE
    assert info.available is False
    assert info.reason == "当前环境未找到 Git 可执行文件。"


def test_worktree_state_reports_actual_dirty_entry_count(tmp_path: Path):
    """验证最终 Git 核验直接统计工作区当前变更。"""
    _git_init(tmp_path)
    (tmp_path / "one.py").write_text("one", encoding="utf-8")
    (tmp_path / "two.py").write_text("two", encoding="utf-8")

    state = inspect_git_worktree(WorkspaceContext.from_path(tmp_path))

    assert state.status is GitRepositoryStatus.AVAILABLE
    assert state.dirty is True
    assert state.changed_count == 2
    assert state.to_dict()["changed_count"] == 2
