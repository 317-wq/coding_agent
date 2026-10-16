from pathlib import Path

import pytest

from app import main


def test_parse_args_uses_workspace(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("sys.argv", ["coding-agent", "--workspace", str(tmp_path)])

    assert main._parse_args() == tmp_path.resolve()


def test_parse_args_rejects_missing_workspace(monkeypatch, tmp_path: Path):
    missing = tmp_path / "missing"
    monkeypatch.setattr("sys.argv", ["coding-agent", "--workspace", str(missing)])

    with pytest.raises(SystemExit):
        main._parse_args()


def test_parse_args_rejects_file_workspace(monkeypatch, tmp_path: Path):
    workspace_file = tmp_path / "workspace.txt"
    workspace_file.write_text("not a directory", encoding="utf-8")
    monkeypatch.setattr(
        "sys.argv", ["coding-agent", "--workspace", str(workspace_file)]
    )

    with pytest.raises(SystemExit):
        main._parse_args()
