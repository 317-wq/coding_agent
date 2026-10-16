from pathlib import Path

import pytest

from app import main, tools
from app.authorization import AuthorizationDecision
from app.registry import DEFAULT_TOOL_REGISTRY, ToolRegistry
from app.result import ToolStatus
from app.workspace import WorkspaceContext


class DenyAuthorizer:
    def authorize(self, tool_name, arguments):
        return AuthorizationDecision.deny("集成测试拒绝授权。")


@pytest.fixture(autouse=True)
def restore_default_workspace():
    yield
    tools.set_workspace(WorkspaceContext.from_path(tools.DEFAULT_WORKSPACE))


@pytest.fixture
def selected_workspace(tmp_path: Path) -> Path:
    tools.set_workspace(WorkspaceContext.from_path(tmp_path))
    return tmp_path


def test_cli_has_explicit_default_and_accepts_selected_workspace(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setattr("sys.argv", ["coding-agent"])
    assert main._parse_args().root == tools.DEFAULT_WORKSPACE

    monkeypatch.setattr(
        "sys.argv", ["coding-agent", "--workspace", str(tmp_path)]
    )
    assert main._parse_args().root == tmp_path.resolve()


def test_registered_file_search_and_pytest_tools(selected_workspace: Path):
    registry = DEFAULT_TOOL_REGISTRY

    created_directory = registry.execute("create_directory", {"path": "src"})
    created_file = registry.execute(
        "create_file",
        {"path": "src/example.py", "content": "VALUE = 'needle'\n"},
    )
    read = registry.execute("read_file", {"path": "src/example.py"})
    edited = registry.execute(
        "edit_file",
        {
            "path": "src/example.py",
            "old_text": "needle",
            "new_text": "updated",
        },
    )
    searched = registry.execute(
        "search_code", {"keyword": "updated", "path": "src"}
    )

    assert created_directory.status is ToolStatus.SUCCESS
    assert created_file.status is ToolStatus.SUCCESS
    assert read.result == "VALUE = 'needle'\n"
    assert edited.status is ToolStatus.SUCCESS
    assert "src/example.py:1" in searched.result

    (selected_workspace / "test_pass.py").write_text(
        "def test_pass():\n    assert True\n", encoding="utf-8"
    )
    (selected_workspace / "test_fail.py").write_text(
        "def test_fail():\n    assert False\n", encoding="utf-8"
    )

    passed = registry.execute("run_tests", {"test_path": "test_pass.py"})
    failed = registry.execute("run_tests", {"test_path": "test_fail.py"})

    assert passed.status is ToolStatus.SUCCESS
    assert "1 passed" in passed.result
    assert failed.status is ToolStatus.FAILURE
    assert "1 failed" in failed.error


def test_registry_returns_structured_errors_for_invalid_calls():
    unknown = DEFAULT_TOOL_REGISTRY.execute("unknown", {})
    missing = DEFAULT_TOOL_REGISTRY.execute("read_file", {})
    extra = DEFAULT_TOOL_REGISTRY.execute(
        "read_file", {"path": "demo.py", "extra": True}
    )

    assert unknown.status is ToolStatus.FAILURE
    assert missing.status is ToolStatus.FAILURE
    assert "缺少必填参数" in missing.error
    assert extra.status is ToolStatus.FAILURE
    assert "存在未知参数" in extra.error


def test_workspace_boundary_rejects_parent_absolute_and_symlink_paths(
    selected_workspace: Path, tmp_path: Path
):
    outside = tmp_path.parent / "outside-mvp-02.txt"
    outside.write_text("outside", encoding="utf-8")
    (selected_workspace / "outside-link.txt").symlink_to(outside)

    parent = DEFAULT_TOOL_REGISTRY.execute(
        "read_file", {"path": "../outside-mvp-02.txt"}
    )
    absolute = DEFAULT_TOOL_REGISTRY.execute(
        "read_file", {"path": str(outside)}
    )
    symlink = DEFAULT_TOOL_REGISTRY.execute(
        "read_file", {"path": "outside-link.txt"}
    )

    assert parent.status is ToolStatus.FAILURE
    assert absolute.status is ToolStatus.FAILURE
    assert symlink.status is ToolStatus.FAILURE


def test_delete_refusal_is_cancelled_and_preserves_file(
    selected_workspace: Path,
):
    target = selected_workspace / "keep.txt"
    target.write_text("keep", encoding="utf-8")
    registry = ToolRegistry(authorizer=DenyAuthorizer())
    registry.register(DEFAULT_TOOL_REGISTRY.get("delete_file"))

    result = registry.execute("delete_file", {"path": "keep.txt"})

    assert result.status is ToolStatus.CANCELLED
    assert target.read_text(encoding="utf-8") == "keep"
