"""Typer CLI 工作区参数测试。"""

from pathlib import Path

from typer.testing import CliRunner

from app.cli import main

runner = CliRunner()


def test_cli_uses_workspace_and_custom_limits(monkeypatch, tmp_path: Path):
    """验证 Typer 将工作区和资源上限传给交互会话。"""
    captured = {}

    def fake_session(workspace, output_format, max_steps, max_tool_calls):
        """记录解析后的 CLI 参数，避免测试启动真实 Agent。"""
        captured.update(
            workspace=workspace,
            output_format=output_format.value,
            max_steps=max_steps,
            max_tool_calls=max_tool_calls,
        )

    monkeypatch.setattr(main, "_run_session", fake_session)
    result = runner.invoke(
        main.app,
        [
            "--workspace",
            str(tmp_path),
            "--output-format",
            "json",
            "--max-steps",
            "30",
            "--max-tool-calls",
            "60",
        ],
    )

    assert result.exit_code == 0
    assert captured["workspace"].root == tmp_path.resolve()
    assert captured["output_format"] == "json"
    assert captured["max_steps"] == 30
    assert captured["max_tool_calls"] == 60


def test_cli_rejects_missing_workspace(tmp_path: Path):
    """验证不存在的工作区返回 Typer 参数错误。"""
    result = runner.invoke(
        main.app,
        ["--workspace", str(tmp_path / "missing")],
    )

    assert result.exit_code == 2
    assert "workspace" in result.output.lower()


def test_cli_rejects_file_workspace(tmp_path: Path):
    """验证普通文件不能作为工作区。"""
    workspace_file = tmp_path / "workspace.txt"
    workspace_file.write_text("not a directory", encoding="utf-8")

    result = runner.invoke(
        main.app,
        ["--workspace", str(workspace_file)],
    )

    assert result.exit_code == 2
    assert "workspace" in result.output.lower()


def test_cli_help_is_rendered_by_typer():
    """验证快捷入口提供 Typer/Rich 帮助页。"""
    result = runner.invoke(main.app, ["--help"])

    assert result.exit_code == 0
    assert "Mini Coding Agent" in result.output
    assert "--max-steps" in result.output
