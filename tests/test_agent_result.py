import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import tools
from app.core.agent import Agent
from app.runtime.workspace import WorkspaceContext
from app.tools.registry import ToolRegistry, ToolSpec
from app.tools.result import ToolResult

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

    monkeypatch.setattr("app.core.agent.ModelAdapter", FakeModel)
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


def test_agent_returns_cancelled_json_and_blocks_completion(monkeypatch, tmp_path: Path):
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

    monkeypatch.setattr("app.core.agent.ModelAdapter", FakeModel)
    agent = Agent(WorkspaceContext.from_path(tmp_path), registry=registry)

    assert agent.run("测试取消") == "完成判定已阻塞且没有可恢复变更，停止当前执行。"


def test_cancelled_tool_stops_remaining_calls_in_batch(monkeypatch, tmp_path: Path):
    """验证批次中工具取消后不执行其后的工具调用。"""
    executions = []
    registry = ToolRegistry()
    registry.register(make_spec("cancel", lambda: ToolResult.cancelled("用户取消")))
    registry.register(make_spec("second", lambda: executions.append("second") or "ok"))

    class FakeModel:
        requests = 0

        def chat(self, messages, tools):
            self.requests += 1
            if self.requests == 1:
                return make_response(
                    tool_calls=[
                        make_tool_call("cancel", "call-1"),
                        make_tool_call("second", "call-2"),
                    ]
                )
            return make_response(content="已停止")

    monkeypatch.setattr("app.core.agent.ModelAdapter", FakeModel)
    agent = Agent(WorkspaceContext.from_path(tmp_path), registry=registry)
    agent.run("测试批次取消")

    assert executions == []
    aborted = [event for event in agent.last_trace.get_events() if event["event"] == "tool_batch_aborted"]
    assert aborted[0]["details"]["skipped_calls"] == 1


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

    monkeypatch.setattr("app.core.agent.ModelAdapter", FakeModel)
    agent = Agent(WorkspaceContext.from_path(tmp_path), registry=registry)

    assert agent.run("测试参数") == "完成判定已阻塞且没有可恢复变更，停止当前执行。"


def test_agent_skips_identical_successful_edit(monkeypatch, tmp_path: Path):
    """验证 Agent 不会执行第二次完全相同且仍然生效的文件修改。"""
    target = tmp_path / "completion_probe.py"
    target.write_text("return 42", encoding="utf-8")
    executions = []
    registry = ToolRegistry()

    def edit_file(path, old_text, new_text):
        executions.append(path)
        content = target.read_text(encoding="utf-8")
        target.write_text(content.replace(old_text, new_text), encoding="utf-8")
        return "修改成功"

    registry.register(
        ToolSpec(
            "edit_file",
            "修改文件",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_text": {"type": "string"},
                    "new_text": {"type": "string"},
                },
                "required": ["path", "old_text", "new_text"],
            },
            edit_file,
        )
    )
    arguments = (
        '{"path":"completion_probe.py","old_text":"return 42",'
        '"new_text":"return 43"}'
    )

    class FakeModel:
        requests = 0

        def chat(self, messages, tools):
            self.requests += 1
            if self.requests <= 2:
                return make_response(
                    tool_calls=[
                        make_tool_call(
                            "edit_file", f"call-{self.requests}", arguments
                        )
                    ]
                )
            return make_response(content="完成")

    monkeypatch.setattr("app.core.agent.ModelAdapter", FakeModel)
    agent = Agent(WorkspaceContext.from_path(tmp_path), registry=registry)
    agent.run("修改 completion_probe.py")

    assert executions == ["completion_probe.py"]
    assert target.read_text(encoding="utf-8") == "return 43"
    names = [event["event"] for event in agent.last_trace.get_events()]
    assert "duplicate_tool_call_skipped" in names


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

    monkeypatch.setattr("app.core.agent.ModelAdapter", FakeModel)
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

    monkeypatch.setattr("app.core.agent.ModelAdapter", FakeModel)
    agent = Agent(
        WorkspaceContext.from_path(tmp_path), max_steps=1, registry=registry
    )

    assert agent.run("测试轮数上限").startswith("已达到最大执行轮数 1")
