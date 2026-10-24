"""覆盖删除类高风险工具的单一授权入口、授权清单与拒绝路径。"""

from pathlib import Path

from app.runtime.workspace import WorkspaceContext
from app.tools.authorization import AuthorizationDecision
from app.tools.registry import RiskLevel, build_default_registry
from app.tools.result import ToolStatus


class RecordingAuthorizer:
    """记录授权调用并按预设结果返回。"""

    def __init__(self, decision: AuthorizationDecision) -> None:
        self.decision = decision
        self.calls: list[tuple[str, dict]] = []

    def authorize(self, tool_name, arguments):
        """记录一次授权请求。"""
        self.calls.append((tool_name, arguments))
        return self.decision


def _registry(tmp_path: Path, decision: AuthorizationDecision):
    """在临时工作区上构建默认注册表并替换授权器。"""
    registry = build_default_registry(WorkspaceContext.from_path(tmp_path))
    authorizer = RecordingAuthorizer(decision)
    registry.authorizer = authorizer
    return registry, authorizer


def test_delete_file_authorizes_once_and_deletes(tmp_path: Path):
    """验证删除单个文件只经注册表授权一次，且不再触发工具内部确认。"""
    target = tmp_path / "a.txt"
    target.write_text("x", encoding="utf-8")
    registry, authorizer = _registry(tmp_path, AuthorizationDecision.allow())

    result = registry.execute("delete_file", {"path": "a.txt"})

    assert result.status is ToolStatus.SUCCESS
    assert not target.exists()
    assert authorizer.calls == [("delete_file", {"path": "a.txt"})]


def test_delete_tool_never_prompts_inside_executor(tmp_path: Path, monkeypatch):
    """验证工具内部不再调用 input 请求确认。"""

    def forbidden_input(*args, **kwargs):
        raise AssertionError("工具内部不应再请求确认")

    monkeypatch.setattr("builtins.input", forbidden_input)
    target = tmp_path / "b.txt"
    target.write_text("x", encoding="utf-8")
    registry, _ = _registry(tmp_path, AuthorizationDecision.allow())

    result = registry.execute("delete_file", {"path": "b.txt"})

    assert result.status is ToolStatus.SUCCESS
    assert not target.exists()


def test_delete_directory_authorization_lists_files(tmp_path: Path):
    """验证目录删除在授权前提供完整文件清单，并一次删除。"""
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "a.py").write_text("a", encoding="utf-8")
    (package / "b.py").write_text("b", encoding="utf-8")
    registry, authorizer = _registry(tmp_path, AuthorizationDecision.allow())

    result = registry.execute("delete_directory", {"path": "pkg"})

    assert result.status is ToolStatus.SUCCESS
    assert not package.exists()
    tool_name, arguments = authorizer.calls[0]
    assert tool_name == "delete_directory"
    assert arguments["file_count"] == 2
    assert sorted(arguments["files"]) == ["pkg/a.py", "pkg/b.py"]


def test_denied_delete_keeps_files(tmp_path: Path):
    """验证授权被拒绝时不删除任何内容。"""
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "a.py").write_text("a", encoding="utf-8")
    registry, _ = _registry(tmp_path, AuthorizationDecision.deny("用户拒绝"))

    result = registry.execute("delete_directory", {"path": "pkg"})

    assert result.status is ToolStatus.CANCELLED
    assert "用户拒绝" in (result.result or "")
    assert (package / "a.py").exists()


def test_authorization_decision_is_recorded(tmp_path: Path):
    """验证授权决定通过事件记录器进入 Trace。"""
    (tmp_path / "c.txt").write_text("x", encoding="utf-8")
    registry, _ = _registry(tmp_path, AuthorizationDecision.deny("测试拒绝"))
    events: list[tuple[str, dict]] = []

    registry.execute(
        "delete_file",
        {"path": "c.txt"},
        event_recorder=lambda name, **details: events.append((name, details)),
    )

    assert events[0][0] == "tool_authorization_decided"
    assert events[0][1]["approved"] is False
    assert events[0][1]["tool"] == "delete_file"


def test_non_interactive_delete_is_cancelled_with_reason(tmp_path: Path, monkeypatch):
    """验证非交互环境明确拒绝高风险操作并保留文件。"""
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    target = tmp_path / "d.txt"
    target.write_text("x", encoding="utf-8")
    registry = build_default_registry(WorkspaceContext.from_path(tmp_path))

    result = registry.execute("delete_file", {"path": "d.txt"})

    assert result.status is ToolStatus.CANCELLED
    assert "非交互" in (result.result or "")
    assert target.exists()


def test_only_deletions_are_high_risk_and_medium_is_gone(tmp_path: Path):
    """验证风险等级只保留 LOW/HIGH，且只有删除属于高风险。"""
    registry = build_default_registry(WorkspaceContext.from_path(tmp_path))

    assert not hasattr(RiskLevel, "MEDIUM")
    for name in ("create_file", "edit_file", "run_tests", "read_file"):
        assert registry.get(name).risk_level is RiskLevel.LOW
    for name in ("delete_file", "delete_directory"):
        assert registry.get(name).risk_level is RiskLevel.HIGH
