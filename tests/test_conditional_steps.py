"""覆盖条件步骤与无进展收敛。"""

from pathlib import Path
from types import SimpleNamespace

from app.core.agent import Agent
from app.core.planner import create_task_plan
from app.runtime.workspace import WorkspaceContext
from app.tools.registry import ToolRegistry, ToolSpec
from app.tools.result import ToolResult
from tests.stub_tools import passed_test

REPAIR_TASK = "运行测试，修复实现中的问题，不要修改测试文件，直到测试通过。"

EMPTY_PARAMETERS = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}


def _response(content=None, tool_calls=None):
    """构造一次模型响应。"""
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _call(name: str, call_id: str, arguments: str = "{}"):
    """构造一次工具调用。"""
    function = SimpleNamespace(name=name, arguments=arguments)
    return SimpleNamespace(id=call_id, function=function)


def test_conditional_repair_marks_implement_step():
    """条件词只标记实现步骤，不标记测试执行步骤。"""
    plan = create_task_plan(REPAIR_TASK)

    implement = [step for step in plan.steps if "实现或修改" in step.objective]
    run = [step for step in plan.steps if "运行指定测试" in step.objective]
    assert implement and implement[0].conditional is True
    assert run and run[0].conditional is False


def test_conditional_step_is_satisfied_when_tests_already_pass(
    monkeypatch, tmp_path: Path
):
    """测试本来就通过时，条件步骤不再阻塞完成。"""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_calc.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    registry = ToolRegistry()
    registry.register(
        ToolSpec("run_tests", "运行测试", EMPTY_PARAMETERS, passed_test)
    )

    class FakeModel:
        calls = 0

        def chat(self, messages, tools):
            self.calls += 1
            if self.calls == 1:
                return _response(tool_calls=[_call("run_tests", "run-1")])
            return _response(content="测试已通过，无需修改。")

    monkeypatch.setattr("app.core.agent.ModelAdapter", FakeModel)
    agent = Agent(WorkspaceContext.from_path(tmp_path), registry=registry)

    agent.run(REPAIR_TASK)

    assert agent.last_result is not None
    assert agent.last_result.completed is True
    events = [event["event"] for event in agent.last_trace.get_events()]
    assert "conditional_step_satisfied" in events


def test_task_stops_when_plan_makes_no_progress(monkeypatch, tmp_path: Path):
    """连续多轮没有推进计划步骤时停止，而不是空转到上限。"""
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            "read_file",
            "读取文件",
            {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "additionalProperties": False,
            },
            lambda path=".": ToolResult.success("内容"),
        )
    )

    class LoopModel:
        calls = 0

        def chat(self, messages, tools):
            self.calls += 1
            return _response(
                tool_calls=[
                    _call("read_file", f"read-{self.calls}", '{"path":"a.py"}')
                ]
            )

    monkeypatch.setattr("app.core.agent.ModelAdapter", LoopModel)
    agent = Agent(WorkspaceContext.from_path(tmp_path), registry=registry, max_steps=20)

    result = agent.run("读取配置，然后修改代码，然后运行测试")

    assert "没有推进计划步骤" in result
    assert agent.last_result is not None
    assert agent.last_result.status.value == "stopped"
