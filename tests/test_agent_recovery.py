from pathlib import Path
from types import SimpleNamespace

from app.core.agent import Agent
from app.core.recovery import RecoveryAction
from app.runtime.workspace import WorkspaceContext
from app.tools.authorization import AuthorizationDecision
from app.tools.registry import ToolRegistry, ToolSpec
from app.tools.result import ToolResult

EMPTY_PARAMETERS = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}
PATH_PARAMETERS = {
    "type": "object",
    "properties": {"path": {"type": "string"}},
    "required": ["path"],
    "additionalProperties": False,
}


class StubAuthorizer:
    def __init__(self, decision):
        self.decision = decision
        self.calls = []

    def authorize(self, tool_name, arguments):
        self.calls.append((tool_name, arguments))
        return self.decision


def _call(name, call_id, arguments="{}"):
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def _response(content=None, tool_calls=None):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _registry(tmp_path, authorizer):
    registry = ToolRegistry(authorizer=authorizer)

    def failing_edit(path):
        (tmp_path / path).write_text("broken", encoding="utf-8")
        return ToolResult.failure("模拟修改失败")

    registry.register(
        ToolSpec("edit_file", "修改文件", PATH_PARAMETERS, failing_edit)
    )
    registry.register(
        ToolSpec("step", "执行步骤", EMPTY_PARAMETERS, lambda: "完成")
    )
    return registry


def test_agent_rolls_back_and_replans_after_failed_mutation(
    monkeypatch, tmp_path: Path
):
    target = tmp_path / "module.py"
    target.write_text("original", encoding="utf-8")
    authorizer = StubAuthorizer(AuthorizationDecision.allow())
    registry = _registry(tmp_path, authorizer)

    class FakeModel:
        calls = 0

        def chat(self, messages, tools):
            self.calls += 1
            if self.calls == 1:
                return _response(
                    tool_calls=[_call("edit_file", "edit", '{"path":"module.py"}')]
                )
            if self.calls <= 4:
                return _response(tool_calls=[_call("step", f"step-{self.calls}")])
            assert "已授权回滚" in str(messages)
            return _response(content="恢复后完成")

    monkeypatch.setattr("app.core.agent.ModelAdapter", FakeModel)
    agent = Agent(WorkspaceContext.from_path(tmp_path), registry=registry)

    result = agent.run("读取配置，然后修改代码，然后运行测试")

    assert result == "恢复后完成"
    assert target.read_text(encoding="utf-8") == "original"
    assert agent.last_recovery_decision.action is RecoveryAction.CONTINUE
    assert agent.last_change_set.summary().changes == ()
    assert all(step.status.value == "completed" for step in agent.last_plan.steps)
    assert authorizer.calls[0][0] == "rollback_task_changes"


def test_agent_preserves_changes_and_requests_confirmation_when_rollback_is_denied(
    monkeypatch, tmp_path: Path
):
    target = tmp_path / "module.py"
    target.write_text("original", encoding="utf-8")
    authorizer = StubAuthorizer(AuthorizationDecision.deny("用户拒绝"))
    registry = _registry(tmp_path, authorizer)

    class FakeModel:
        def chat(self, messages, tools):
            return _response(
                tool_calls=[_call("edit_file", "edit", '{"path":"module.py"}')]
            )

    monkeypatch.setattr("app.core.agent.ModelAdapter", FakeModel)
    agent = Agent(WorkspaceContext.from_path(tmp_path), registry=registry)

    result = agent.run("读取配置，然后修改代码，然后运行测试")

    assert "用户拒绝回滚" in result
    assert "请确认" in result
    assert target.read_text(encoding="utf-8") == "broken"
    assert agent.last_recovery_decision.action is RecoveryAction.SUGGEST_ROLLBACK
