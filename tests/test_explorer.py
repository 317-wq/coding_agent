from pathlib import Path
from types import SimpleNamespace

import pytest

from app import tools
from app.agent import Agent
from app.explorer import ProjectExplorer
from app.workspace import WorkspaceContext


@pytest.fixture(autouse=True)
def restore_default_workspace():
    yield
    tools.set_workspace(WorkspaceContext.from_path(tools.DEFAULT_WORKSPACE))


def test_explorer_extracts_structure_and_key_information(tmp_path: Path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "main.py").write_text("print('ok')", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_main.py").write_text("", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo-agent"\nrequires-python = ">=3.12"\n',
        encoding="utf-8",
    )
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "ignored.py").write_text("", encoding="utf-8")

    snapshot = ProjectExplorer(WorkspaceContext.from_path(tmp_path)).explore()

    assert snapshot.project_types == ("Python",)
    assert snapshot.languages == ("Python (2)",)
    assert snapshot.source_directories == ("app",)
    assert snapshot.entry_points == ("app/main.py",)
    assert "tests/test_main.py" in snapshot.test_paths
    assert "Python 项目名=demo-agent" in snapshot.metadata
    assert all(".venv" not in entry for entry in snapshot.tree)


def test_explorer_marks_limited_scan_as_truncated(tmp_path: Path):
    for index in range(3):
        (tmp_path / f"file_{index}.py").write_text("", encoding="utf-8")

    snapshot = ProjectExplorer(
        WorkspaceContext.from_path(tmp_path), max_entries=2
    ).explore()

    assert len(snapshot.tree) == 2
    assert snapshot.truncated is True


def test_agent_passes_project_snapshot_to_model(monkeypatch, tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "sample-project"\n', encoding="utf-8"
    )
    captured_messages = []

    class FakeModel:
        def chat(self, messages, tools):
            captured_messages.extend(messages)
            message = SimpleNamespace(tool_calls=None, content="完成")
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    monkeypatch.setattr("app.agent.ModelAdapter", FakeModel)
    agent = Agent(workspace=WorkspaceContext.from_path(tmp_path))

    assert agent.run("查看项目") == "完成"
    assert "sample-project" in captured_messages[1]["content"]
    assert "pyproject.toml" in captured_messages[1]["content"]
