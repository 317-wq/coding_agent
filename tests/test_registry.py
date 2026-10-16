import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import tools
from app.agent import Agent
from app.registry import (
    DEFAULT_TOOL_REGISTRY,
    RiskLevel,
    ToolRegistry,
    ToolSpec,
)
from app.result import ToolResult, ToolStatus
from app.workspace import WorkspaceContext


@pytest.fixture(autouse=True)
def restore_default_workspace():
    yield
    tools.set_workspace(WorkspaceContext.from_path(tools.DEFAULT_WORKSPACE))


def make_spec(name="echo", executor=lambda value: value):
    return ToolSpec(
        name=name,
        description="返回输入内容。",
        parameters={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        executor=executor,
    )


def test_registry_stores_complete_tool_spec_and_executes_it():
    registry = ToolRegistry()
    spec = make_spec()

    registry.register(spec)

    assert registry.get("echo") is spec
    assert registry.names() == ("echo",)
    result = registry.execute("echo", {"value": "hello"})
    assert result == ToolResult.success("hello")
    assert registry.model_definitions() == [spec.to_model_definition()]


def test_registry_rejects_duplicate_tool_name():
    registry = ToolRegistry()
    registry.register(make_spec())

    try:
        registry.register(make_spec())
    except ValueError as exc:
        assert str(exc) == "工具已注册：echo"
    else:
        raise AssertionError("重复工具名称应被拒绝")


def test_registry_returns_error_for_unknown_tool():
    registry = ToolRegistry()

    result = registry.execute("missing", {})

    assert result.status is ToolStatus.FAILURE
    assert result.error == "不支持的工具：missing"


@pytest.mark.parametrize(
    ("output", "status"),
    [
        ("完成", ToolStatus.SUCCESS),
        ("错误：失败", ToolStatus.FAILURE),
        ("已取消删除：demo.txt", ToolStatus.CANCELLED),
    ],
)
def test_registry_normalizes_legacy_tool_output(output, status):
    registry = ToolRegistry()
    registry.register(make_spec(executor=lambda value: output))

    assert registry.execute("echo", {"value": "x"}).status is status


def test_default_registry_contains_every_builtin_tool():
    definition_names = tuple(
        definition["function"]["name"] for definition in tools.TOOL_DEFINITIONS
    )

    assert DEFAULT_TOOL_REGISTRY.names() == definition_names
    assert DEFAULT_TOOL_REGISTRY.get("delete_file").risk_level is RiskLevel.HIGH
    assert DEFAULT_TOOL_REGISTRY.get("run_tests").risk_level is RiskLevel.MEDIUM
    assert DEFAULT_TOOL_REGISTRY.get("read_file").risk_level is RiskLevel.LOW


def test_model_definition_is_a_copy():
    spec = make_spec()
    definition = spec.to_model_definition()

    definition["function"]["parameters"]["required"].append("extra")

    assert spec.parameters["required"] == ["value"]


def test_agent_uses_injected_registry_for_definition_and_execution(
    monkeypatch, tmp_path: Path
):
    registry = ToolRegistry()
    registry.register(make_spec())
    requests = []

    class FakeModel:
        def chat(self, messages, tools):
            requests.append((messages, tools))
            if len(requests) == 1:
                function = SimpleNamespace(
                    name="echo", arguments='{"value": "hello"}'
                )
                tool_call = SimpleNamespace(id="call-1", function=function)
                message = SimpleNamespace(content=None, tool_calls=[tool_call])
            else:
                content = json.loads(messages[-1]["content"])
                assert content == {
                    "status": "success",
                    "result": "hello",
                    "error": None,
                }
                message = SimpleNamespace(content="完成", tool_calls=None)
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    monkeypatch.setattr("app.agent.ModelAdapter", FakeModel)
    agent = Agent(
        workspace=WorkspaceContext.from_path(tmp_path), registry=registry
    )

    assert agent.run("调用 echo") == "完成"
    assert requests[0][1] == registry.model_definitions()
