import subprocess
from pathlib import Path

from app.runtime.workspace import WorkspaceContext
from app.tools.git_tools import GitReadTools
from app.tools.registry import build_default_registry
from app.tools.result import ToolStatus


def _git(path: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *arguments],
        cwd=path,
        capture_output=True,
        check=True,
        text=True,
    )


def _repository(path: Path) -> None:
    _git(path, "init", "--initial-branch=main")
    _git(path, "config", "user.name", "Test User")
    _git(path, "config", "user.email", "test@example.com")
    (path / "tracked.txt").write_text("before\n", encoding="utf-8")
    _git(path, "add", "tracked.txt")
    _git(path, "commit", "-m", "initial")


def test_status_and_diff_read_workspace_changes(tmp_path: Path):
    _repository(tmp_path)
    (tmp_path / "tracked.txt").write_text("after\n", encoding="utf-8")
    reader = GitReadTools(WorkspaceContext.from_path(tmp_path))

    assert "tracked.txt" in reader.status()
    diff = reader.diff()
    assert "-before" in diff
    assert "+after" in diff


def test_staged_diff_is_selected_explicitly(tmp_path: Path):
    _repository(tmp_path)
    (tmp_path / "tracked.txt").write_text("staged\n", encoding="utf-8")
    _git(tmp_path, "add", "tracked.txt")
    reader = GitReadTools(WorkspaceContext.from_path(tmp_path))

    assert reader.diff() == "工作区内没有对应的 Git 差异。"
    assert "+staged" in reader.diff(staged=True)


def test_subdirectory_workspace_does_not_expose_parent_changes(tmp_path: Path):
    _repository(tmp_path)
    child = tmp_path / "child"
    child.mkdir()
    (tmp_path / "tracked.txt").write_text("parent change\n", encoding="utf-8")
    (child / "inside.txt").write_text("inside\n", encoding="utf-8")
    reader = GitReadTools(WorkspaceContext.from_path(child))

    status = reader.status()
    assert "child/inside.txt" in status
    assert "tracked.txt" not in status


def test_non_repository_is_reported_without_failing_read_tool(tmp_path: Path):
    reader = GitReadTools(WorkspaceContext.from_path(tmp_path))

    assert reader.status() == "当前工作区不是 Git 仓库，无法提供 Git 查询结果。"
    assert reader.diff() == "当前工作区不是 Git 仓库，无法提供 Git 查询结果。"

    registry = build_default_registry(WorkspaceContext.from_path(tmp_path))
    assert registry.execute("git_status", {}).status is ToolStatus.SUCCESS


def test_git_query_outside_workspace_is_rejected(tmp_path: Path):
    reader = GitReadTools(WorkspaceContext.from_path(tmp_path))

    assert reader.diff("../outside").startswith("错误：Git 查询路径无效")


def test_registry_exposes_only_fixed_read_only_git_tools(tmp_path: Path):
    _repository(tmp_path)
    registry = build_default_registry(WorkspaceContext.from_path(tmp_path))

    assert "git_status" in registry.names()
    assert "git_diff" in registry.names()
    assert registry.execute("git_status", {}).status is ToolStatus.SUCCESS
    rejected = registry.execute("git_diff", {"command": "reset --hard"})
    assert rejected.status is ToolStatus.FAILURE
    assert "未知参数" in rejected.error
