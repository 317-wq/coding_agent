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


def test_trace_records_git_snapshot_changes_and_recovery(monkeypatch, tmp_path):
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="create_file",
            description="创建文件",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
            executor=lambda path, content: _create_file(
                tmp_path, path, content
            ),
        )
    )

    class FakeModel:
        calls = 0

        def chat(self, messages, tools):
            self.calls += 1
            if self.calls == 1:
                return _response(
                    tool_calls=[
                        SimpleNamespace(
                            id="create",
                            function=SimpleNamespace(
                                name="create_file",
                                arguments='{"path":"new.txt","content":"new"}',
                            ),
                        )
                    ]
                )
            return _response(content="完成")

    monkeypatch.setattr("app.agent.ModelAdapter", FakeModel)
    agent = Agent(WorkspaceContext.from_path(tmp_path), registry=registry)

    assert agent.run("创建一个文件") == "完成"
    names = [event["event"] for event in agent.last_trace.get_events()]
    assert "git_baseline_detected" in names
    assert "snapshot_created" in names
    assert "change_set_updated" in names
    assert "recovery_decided" in names


def _create_file(root, path, content):
    (root / path).write_text(content, encoding="utf-8")
    return "创建成功"
