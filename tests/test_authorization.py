from pathlib import Path

from app.authorization import (
    AuthorizationDecision,
    ConsoleAuthorizer,
)
from app.registry import RiskLevel, ToolRegistry, ToolSpec
from app.result import ToolStatus


class StubAuthorizer:
    def __init__(self, decision: AuthorizationDecision) -> None:
        self.decision = decision
        self.calls = []

    def authorize(self, tool_name, arguments):
        self.calls.append((tool_name, arguments))
        return self.decision


def make_delete_spec(executor):
    return ToolSpec(
        name="delete_file",
        description="删除文件。",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
        executor=executor,
        risk_level=RiskLevel.HIGH,
    )


def test_denied_high_risk_tool_is_cancelled_without_execution(tmp_path: Path):
    target = tmp_path / "keep.txt"
    target.write_text("keep", encoding="utf-8")
    executions = []
    authorizer = StubAuthorizer(AuthorizationDecision.deny("测试拒绝。"))
    registry = ToolRegistry(authorizer=authorizer)
    registry.register(make_delete_spec(lambda path: executions.append(path)))

    result = registry.execute("delete_file", {"path": str(target)})

    assert result.status is ToolStatus.CANCELLED
    assert result.result == "已取消执行高风险工具 delete_file：测试拒绝。"
    assert executions == []
    assert target.read_text(encoding="utf-8") == "keep"


def test_approved_high_risk_tool_executes():
    authorizer = StubAuthorizer(AuthorizationDecision.allow())
    registry = ToolRegistry(authorizer=authorizer)
    registry.register(make_delete_spec(lambda path: f"deleted:{path}"))

    result = registry.execute("delete_file", {"path": "demo.txt"})

    assert result.status is ToolStatus.SUCCESS
    assert result.result == "deleted:demo.txt"
    assert authorizer.calls == [("delete_file", {"path": "demo.txt"})]


def test_low_risk_tool_does_not_request_authorization():
    authorizer = StubAuthorizer(AuthorizationDecision.deny())
    registry = ToolRegistry(authorizer=authorizer)
    registry.register(
        ToolSpec(
            name="read_file",
            description="读取文件。",
            parameters={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            executor=lambda: "content",
            risk_level=RiskLevel.LOW,
        )
    )

    result = registry.execute("read_file", {})

    assert result.status is ToolStatus.SUCCESS
    assert authorizer.calls == []


def test_console_authorizer_requires_explicit_y(monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: "no")

    denied = ConsoleAuthorizer().authorize("delete_file", {"path": "a.txt"})

    monkeypatch.setattr("builtins.input", lambda prompt: "Y")
    approved = ConsoleAuthorizer().authorize(
        "delete_file", {"path": "a.txt"}
    )

    assert denied.approved is False
    assert approved.approved is True


def test_console_authorizer_denies_non_interactive_session(monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    decision = ConsoleAuthorizer().authorize(
        "delete_file", {"path": "a.txt"}
    )

    assert decision == AuthorizationDecision.deny(
        "非交互环境默认拒绝授权。"
    )
