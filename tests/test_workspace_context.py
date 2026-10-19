from pathlib import Path

import pytest

from app import tools
from app.core.agent import Agent
from app.runtime.workspace import WorkspaceContext


@pytest.fixture(autouse=True)
def restore_default_workspace():
    yield
    tools.set_workspace(WorkspaceContext.from_path(tools.DEFAULT_WORKSPACE))


def test_agent_shares_workspace_context_with_tools(monkeypatch, tmp_path: Path):
    marker = tmp_path / "selected-workspace.txt"
    marker.write_text("selected", encoding="utf-8")
    context = WorkspaceContext.from_path(tmp_path)
    monkeypatch.setattr("app.core.agent.ModelAdapter", lambda: object())

    agent = Agent(workspace=context)

    assert agent.workspace is context
    assert tools.WORKSPACE_CONTEXT is context
    assert tools.WORKSPACE == tmp_path.resolve()
    assert tools.read_file(marker.name) == "selected"


def test_workspace_context_rejects_parent_path(tmp_path: Path):
    context = WorkspaceContext.from_path(tmp_path)

    with pytest.raises(ValueError, match="禁止访问工作目录之外"):
        context.resolve("../outside.txt")


def test_workspace_context_normalizes_absolute_path_inside_workspace(tmp_path: Path):
    """验证所有工具可共享工作区内绝对路径的统一规范化规则。"""
    context = WorkspaceContext.from_path(tmp_path)
    target = tmp_path / "inside.txt"

    assert context.resolve(str(target)) == target.resolve()
    assert context.relative_path(str(target)) == "inside.txt"


def test_workspace_context_rejects_absolute_path_outside_workspace(tmp_path: Path):
    """验证绝对路径规范化不会放宽工作区边界。"""
    context = WorkspaceContext.from_path(tmp_path)

    with pytest.raises(ValueError, match="禁止访问工作目录之外"):
        context.resolve("/tmp/outside.txt")
