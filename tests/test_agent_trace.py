from types import SimpleNamespace

from app.agent import Agent
from app.registry import ToolRegistry, ToolSpec
from app.workspace import WorkspaceContext

PARAMETERS = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}


def _response(content=None, tool_calls=None):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content, tool_calls=tool_calls)
            )
        ]
    )


def test_trace_records_planning_state_and_completion_events(monkeypatch, tmp_path):
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="step",
            description="执行步骤",
            parameters=PARAMETERS,
            executor=lambda: "步骤完成",
        )
    )

    def call(call_id):
        return SimpleNamespace(
            id=call_id,
            function=SimpleNamespace(name="step", arguments="{}"),
        )

    class FakeModel:
        calls = 0

        def chat(self, messages, tools):
            self.calls += 1
            if self.calls <= 3:
                return _response(tool_calls=[call(f"call-{self.calls}")])
            return _response(content="已完成")

    monkeypatch.setattr("app.agent.ModelAdapter", FakeModel)
    agent = Agent(WorkspaceContext.from_path(tmp_path), registry=registry)

    assert agent.run("读取配置，然后修改代码，然后运行测试") == "已完成"
    events = agent.last_trace.get_events()
    names = [event["event"] for event in events]
    assert "complexity_decided" in names
    assert "plan_created" in names
    assert names.count("plan_step_status_changed") == 3
    assert "completion_checked" in names
    completion = next(
        event for event in events if event["event"] == "completion_checked"
    )
    assert completion["details"]["completed"] is True
