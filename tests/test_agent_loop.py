from types import SimpleNamespace

from app.core.agent import Agent
from app.runtime.workspace import WorkspaceContext
from app.tools.registry import ToolRegistry, ToolSpec

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


def _call(call_id):
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name="step", arguments="{}"),
    )


def test_complex_task_uses_plan_and_verifies_before_finishing(monkeypatch, tmp_path):
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="step",
            description="执行步骤",
            parameters=PARAMETERS,
            executor=lambda: "步骤完成",
        )
    )

    class FakeModel:
        def __init__(self):
            self.calls = 0

        def chat(self, messages, tools):
            self.calls += 1
            if self.calls <= 3:
                return _response(tool_calls=[_call(f"call-{self.calls}")])
            return _response(content="任务已完成")

    monkeypatch.setattr("app.core.agent.ModelAdapter", FakeModel)
    agent = Agent(WorkspaceContext.from_path(tmp_path), registry=registry)

    assert agent.run("读取配置，然后修改代码，然后运行测试") == "任务已完成"
    assert agent.last_plan is not None
    assert all(step.status.value == "completed" for step in agent.last_plan.steps)
    assert agent.last_context is not None
    assert len(agent.last_context.context.tool_results) == 3
