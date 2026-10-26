"""Typer CLI 工作区参数测试。"""

from pathlib import Path

from typer.testing import CliRunner

from app.cli import main
from app.core.task_result import (
    ChangeSummary,
    TaskResult,
    TaskStatus,
)
from app.core.task_result import (
    TestEvidence as Evidence,
)
from app.core.task_result import (
    TestEvidenceStatus as EvidenceStatus,
)
from app.tools.authorization import AutoApproveAuthorizer

runner = CliRunner()


def test_cli_uses_workspace_and_custom_limits(monkeypatch, tmp_path: Path):
    """验证 Typer 将工作区和资源上限传给交互会话。"""
    captured = {}

    def fake_session(
        workspace, output_format, max_steps, max_tool_calls, debug, authorizer=None
    ):
        """记录解析后的 CLI 参数，避免测试启动真实 Agent。"""
        captured.update(
            workspace=workspace,
            output_format=output_format.value,
            max_steps=max_steps,
            max_tool_calls=max_tool_calls,
            debug=debug,
            authorizer=authorizer,
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
            "--debug",
        ],
    )

    assert result.exit_code == 0
    assert captured["workspace"].root == tmp_path.resolve()
    assert captured["output_format"] == "json"
    assert captured["max_steps"] == 30
    assert captured["max_tool_calls"] == 60
    assert captured["debug"] is True
    assert captured["authorizer"] is None


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
    assert "--debug" in result.output


def test_default_result_view_omits_trace_details():
    """验证默认 Rich 结果只展示用户需要的文件和测试摘要。"""
    result = TaskResult(
        task="创建页面",
        status=TaskStatus.COMPLETED,
        summary="任务已完成。",
        changes=ChangeSummary(created=("login.html",)),
        tests=(
            Evidence(
                target="tests",
                status=EvidenceStatus.PASSED,
                exit_code=0,
            ),
        ),
        trace_event_count=42,
    )

    with main.console.capture() as capture:
        main.console.print(main._render_user_result(result))
    output = capture.get()

    assert "任务完成" in output
    assert "login.html" in output
    assert "tests" in output
    assert "Trace" not in output
    assert "42" not in output


def test_default_result_view_localizes_stopped_status_without_duplicate_reason():
    """验证停止状态使用中文，并避免摘要和原因重复显示。"""
    result = TaskResult(
        task="创建页面",
        status=TaskStatus.STOPPED,
        summary="max_steps",
        stop_reason="max_steps",
    )

    with main.console.capture() as capture:
        main.console.print(main._render_user_result(result))
    output = capture.get()

    assert "任务已停止" in output
    assert "任务stopped" not in output
    assert "已达到最大执行轮数，任务尚未完成。" in output
    assert "max_steps" not in output


def test_progress_renderer_distinguishes_directories_and_failed_tests():
    """验证默认进度正确标注目录创建和测试失败。"""
    renderer = main.ProgressRenderer(main.console)

    with main.console.capture() as capture:
        renderer(
            {
                "event": "mutation_applied",
                "details": {"tool": "create_directory", "path": "expense_tracker"},
            }
        )
        renderer(
            {
                "event": "tool_call_finished",
                "details": {
                    "tool": "run_tests",
                    "status": "failure",
                    "error": "测试目标：.\n测试结果：失败",
                },
            }
        )
    output = capture.get()

    assert "创建目录 expense_tracker" in output
    assert "! 测试未通过" in output
    assert "✓ 测试未通过" not in output


def test_cli_yes_enables_auto_approve(monkeypatch, tmp_path: Path):
    """验证 --yes 注入自动授权器。"""
    captured = {}

    def fake_session(
        workspace, output_format, max_steps, max_tool_calls, debug, authorizer=None
    ):
        """记录授权器参数，避免测试启动真实 Agent。"""
        captured["authorizer"] = authorizer

    monkeypatch.setattr(main, "_run_session", fake_session)

    result = runner.invoke(main.app, ["--workspace", str(tmp_path), "-y"])

    assert result.exit_code == 0
    assert isinstance(captured["authorizer"], AutoApproveAuthorizer)
