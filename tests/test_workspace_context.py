from pathlib import Path

import pytest

from app import tools
from app.agent import Agent
from app.workspace import WorkspaceContext


@pytest.fixture(autouse=True)
def restore_default_workspace():
    yield
    tools.set_workspace(WorkspaceContext.from_path(tools.DEFAULT_WORKSPACE))


def test_agent_shares_workspace_context_with_tools(monkeypatch, tmp_path: Path):
    marker = tmp_path / "selected-workspace.txt"
    marker.write_text("selected", encoding="utf-8")
    context = WorkspaceContext.from_path(tmp_path)
    monkeypatch.setattr("app.agent.ModelAdapter", lambda: object())

    agent = Agent(workspace=context)

    assert agent.workspace is context
    assert tools.WORKSPACE_CONTEXT is context
    assert tools.WORKSPACE == tmp_path.resolve()
    assert tools.read_file(marker.name) == "selected"


def test_workspace_context_rejects_parent_path(tmp_path: Path):
    context = WorkspaceContext.from_path(tmp_path)

    with pytest.raises(ValueError, match="禁止访问工作目录之外"):
        context.resolve("../outside.txt")


def test_workspace_context_rejects_absolute_path(tmp_path: Path):
    context = WorkspaceContext.from_path(tmp_path)

    with pytest.raises(ValueError, match="不允许使用绝对路径"):
        context.resolve(str(tmp_path / "inside.txt"))
