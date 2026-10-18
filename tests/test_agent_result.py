import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import tools
from app.agent import Agent
from app.registry import ToolRegistry, ToolSpec
from app.result import ToolResult
from app.workspace import WorkspaceContext

EMPTY_PARAMETERS = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}


@pytest.fixture(autouse=True)
def restore_default_workspace():
    yield
    tools.set_workspace(WorkspaceContext.from_path(tools.DEFAULT_WORKSPACE))


def make_spec(name, executor):
    return ToolSpec(
        name=name,
        description=f"执行 {name}。",
        parameters=EMPTY_PARAMETERS,
        executor=executor,
    )


def make_tool_call(name, call_id, arguments="{}"):
    function = SimpleNamespace(name=name, arguments=arguments)
    return SimpleNamespace(id=call_id, function=function)


def make_response(content=None, tool_calls=None):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def test_agent_returns_failure_json_and_stops_current_tool_batch(
    monkeypatch, tmp_path: Path
):
    executions = []
    registry = ToolRegistry()

    def fail():
        executions.append("fail")
        return ToolResult.failure("boom")

    def should_not_run():
        executions.append("second")
        return "unexpected"

    registry.register(make_spec("fail", fail))
    registry.register(make_spec("second", should_not_run))

    class FakeModel:
        def __init__(self):
            self.requests = 0

        def chat(self, messages, tools):
            self.requests += 1
            if self.requests == 1:
                return make_response(
                    tool_calls=[
                        make_tool_call("fail", "call-1"),
                        make_tool_call("second", "call-2"),
                    ]
                )
            assert json.loads(messages[-1]["content"]) == {
                "status": "failure",
                "result": None,
                "error": "boom",
            }
            return make_response(content="已处理失败")

    monkeypatch.setattr("app.agent.ModelAdapter", FakeModel)
    agent = Agent(WorkspaceContext.from_path(tmp_path), registry=registry)

    assert agent.run("测试失败") == "完成判定已阻塞且没有可恢复变更，停止当前执行。"
    assert executions == ["fail"]
    events = agent.last_trace.get_events()
    started = next(event for event in events if event["event"] == "task_started")
    finished = next(
        event for event in events if event["event"] == "tool_call_finished"
    )
    assert started["details"]["workspace"] == str(tmp_path)
    assert finished["details"]["status"] == "failure"
    assert finished["details"]["error"] == "boom"


def test_agent_returns_cancelled_json_and_continues(monkeypatch, tmp_path: Path):
    registry = ToolRegistry()
    registry.register(
        make_spec("cancel", lambda: ToolResult.cancelled("用户取消"))
    )

    class FakeModel:
        def __init__(self):
            self.requests = 0

        def chat(self, messages, tools):
            self.requests += 1
            if self.requests == 1:
                return make_response(
                    tool_calls=[make_tool_call("cancel", "call-1")]
                )
            assert json.loads(messages[-1]["content"])["status"] == "cancelled"
            return make_response(content="取消已确认")

    monkeypatch.setattr("app.agent.ModelAdapter", FakeModel)
    agent = Agent(WorkspaceContext.from_path(tmp_path), registry=registry)

    assert agent.run("测试取消") == "取消已确认"


def test_agent_returns_structured_json_for_invalid_arguments(
    monkeypatch, tmp_path: Path
):
    registry = ToolRegistry()
    registry.register(make_spec("noop", lambda: "ok"))

    class FakeModel:
        def __init__(self):
            self.requests = 0

        def chat(self, messages, tools):
            self.requests += 1
            if self.requests == 1:
                return make_response(
                    tool_calls=[make_tool_call("noop", "call-1", "[]")]
                )
            result = json.loads(messages[-1]["content"])
            assert result["status"] == "failure"
            assert result["error"].startswith("工具参数解析失败：")
            return make_response(content="参数错误已处理")

    monkeypatch.setattr("app.agent.ModelAdapter", FakeModel)
    agent = Agent(WorkspaceContext.from_path(tmp_path), registry=registry)

    assert agent.run("测试参数") == "完成判定已阻塞且没有可恢复变更，停止当前执行。"


def test_agent_keeps_tool_call_limit(monkeypatch, tmp_path: Path):
    executions = []
    registry = ToolRegistry()
    registry.register(
        make_spec("once", lambda: executions.append("once") or "ok")
    )

    class FakeModel:
        def chat(self, messages, tools):
            return make_response(
                tool_calls=[
                    make_tool_call("once", "call-1"),
                    make_tool_call("once", "call-2"),
                ]
            )

    monkeypatch.setattr("app.agent.ModelAdapter", FakeModel)
    agent = Agent(
        WorkspaceContext.from_path(tmp_path),
        max_tool_calls=1,
        registry=registry,
    )

    result = agent.run("测试工具上限")

    assert result.startswith("已达到工具调用上限 1")
    assert executions == ["once"]


def test_agent_keeps_step_limit(monkeypatch, tmp_path: Path):
    registry = ToolRegistry()
    registry.register(make_spec("noop", lambda: "ok"))

    class FakeModel:
        def chat(self, messages, tools):
            return make_response(
                tool_calls=[make_tool_call("noop", "call-1")]
            )

    monkeypatch.setattr("app.agent.ModelAdapter", FakeModel)
    agent = Agent(
        WorkspaceContext.from_path(tmp_path), max_steps=1, registry=registry
    )

    assert agent.run("测试轮数上限").startswith("已达到最大执行轮数 1")
