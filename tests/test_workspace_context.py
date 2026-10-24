"""覆盖工作区上下文与工具实例的绑定关系。"""

from pathlib import Path

import pytest

from app.core.agent import Agent
from app.runtime.workspace import WorkspaceContext
from app.tools.registry import build_default_registry
from app.tools.result import ToolStatus


def test_agent_registry_is_bound_to_its_workspace(monkeypatch, tmp_path: Path):
    """验证 Agent 的默认注册表绑定到它自己的工作区。"""
    marker = tmp_path / "selected-workspace.txt"
    marker.write_text("selected", encoding="utf-8")
    context = WorkspaceContext.from_path(tmp_path)
    monkeypatch.setattr("app.core.agent.ModelAdapter", lambda: object())

    agent = Agent(workspace=context)

    assert agent.workspace is context
    result = agent.registry.execute("read_file", {"path": marker.name})
    assert result.status is ToolStatus.SUCCESS
    assert result.result == "selected"


def test_two_workspaces_run_in_parallel_without_global_state(tmp_path: Path):
    """验证同进程两个工作区的工具与注册表互不影响。"""
    left = tmp_path / "left"
    right = tmp_path / "right"
    left.mkdir()
    right.mkdir()
    (left / "name.txt").write_text("left", encoding="utf-8")
    (right / "name.txt").write_text("right", encoding="utf-8")

    left_registry = build_default_registry(WorkspaceContext.from_path(left))
    right_registry = build_default_registry(WorkspaceContext.from_path(right))

    assert left_registry.execute("read_file", {"path": "name.txt"}).result == "left"
    assert right_registry.execute("read_file", {"path": "name.txt"}).result == "right"


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
