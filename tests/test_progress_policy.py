"""覆盖进展分级：重复调用拦截、修复预算按进展计、熔断。"""

from pathlib import Path
from types import SimpleNamespace

from app.core.agent import Agent
from app.core.recovery import RecoveryAction, RecoveryPolicy, RecoverySignals
from app.core.task_safety import TaskSafetySession
from app.runtime.workspace import WorkspaceContext
from app.tools.authorization import AutoApproveAuthorizer
from app.tools.registry import ToolRegistry, ToolSpec
from app.tools.result import ToolResult


def _response(tool_calls=None, content=None):
    """构造一次模型响应。"""
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _call(name: str, call_id: str, arguments: str = "{}"):
    """构造一次工具调用。"""
    function = SimpleNamespace(name=name, arguments=arguments)
    return SimpleNamespace(id=call_id, function=function)


def test_repair_budget_resets_when_failures_decrease(tmp_path: Path):
    """失败测试数下降视为有进展，重置修复预算。"""
    session = TaskSafetySession(WorkspaceContext.from_path(tmp_path))
    arguments = {"test_path": "tests/test_calc.py"}
    three = ToolResult.failure(
        "测试结果：失败\n"
        "FAILED tests/test_calc.py::a\n"
        "FAILED tests/test_calc.py::b\n"
        "FAILED tests/test_calc.py::c"
    )
    two = ToolResult.failure(
        "测试结果：失败\n"
        "FAILED tests/test_calc.py::a\n"
        "FAILED tests/test_calc.py::b"
    )

    session.observe("run_tests", three, arguments=arguments)
    session.record_mutation_applied("calc.py")
    session.observe("run_tests", two, arguments=arguments)

    assert session.test_repair_attempts == 0
    assert session.total_repair_attempts == 1


def test_repair_budget_accumulates_without_progress(tmp_path: Path):
    """失败数没有下降时累积修复次数。"""
    session = TaskSafetySession(WorkspaceContext.from_path(tmp_path))
    arguments = {"test_path": "tests/test_calc.py"}
    failure = ToolResult.failure(
        "测试结果：失败\nFAILED tests/test_calc.py::a"
    )

    session.observe("run_tests", failure, arguments=arguments)
    session.record_mutation_applied("calc.py")
    session.observe("run_tests", failure, arguments=arguments)

    assert session.test_repair_attempts == 1
    assert session.total_repair_attempts == 1


def test_circuit_breaker_stops_after_total_budget():
    """累计修复次数达到上限时熔断停止。"""
    decision = RecoveryPolicy().decide(
        RecoverySignals(
            test_failed=True,
            total_repair_attempts=6,
            test_error_kind="断言失败",
            recoverable_paths=("calc.py",),
        )
    )

    assert decision.action is RecoveryAction.STOP
    assert "熔断" in decision.reason


def test_repeated_identical_call_is_skipped(monkeypatch, tmp_path: Path):
    """相同工具相同参数且没有推进时，第二次调用被跳过。"""
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            "git_status",
            "查看状态",
            {"type": "object", "properties": {}, "additionalProperties": False},
            lambda: ToolResult.success("clean"),
        )
    )

    class LoopModel:
        calls = 0

        def chat(self, messages, tools):
            self.calls += 1
            return _response(
                tool_calls=[_call("git_status", f"git-{self.calls}")]
            )

    monkeypatch.setattr("app.core.agent.ModelAdapter", LoopModel)
    agent = Agent(WorkspaceContext.from_path(tmp_path), registry=registry, max_steps=2)

    agent.run("读取配置，然后修改代码，然后运行测试")

    events = [event["event"] for event in agent.last_trace.get_events()]
    assert "repeated_call_detected" in events


def test_rollback_executes_with_auto_approve(monkeypatch, tmp_path: Path):
    """--yes 等价路径下回滚真实执行并恢复工作区。"""
    from tests.golden_scenarios import (
        PATH_PARAMETERS,
        TEST_PATH_PARAMETERS,
        ScriptedModel,
        _call,
        _failed_test,
        _run_agent,
    )

    (tmp_path / "calculator.py").write_text("VALUE = 1\n", encoding="utf-8")
    edits = {"count": 0}

    def edit(path: str) -> ToolResult:
        edits["count"] += 1
        (tmp_path / path).write_text(
            f"VALUE = {edits['count'] + 8}\n", encoding="utf-8"
        )
        return ToolResult.success(f"修改文件成功并已核验：{path}")

    registry = ToolRegistry(authorizer=AutoApproveAuthorizer())
    registry.register(ToolSpec("edit_file", "修改文件", PATH_PARAMETERS, edit))
    registry.register(
        ToolSpec(
            "run_tests",
            "运行测试",
            TEST_PATH_PARAMETERS,
            lambda test_path=".": _failed_test(test_path),
        )
    )
    model = ScriptedModel(
        [
            [_call("edit_file", "e1", '{"path":"calculator.py"}')],
            [_call("run_tests", "t1", '{"test_path":"tests"}')],
            [_call("edit_file", "e2", '{"path":"calculator.py"}')],
            [_call("run_tests", "t2", '{"test_path":"tests"}')],
            [_call("edit_file", "e3", '{"path":"calculator.py"}')],
            [_call("run_tests", "t3", '{"test_path":"tests"}')],
            "已完成",
        ]
    )

    agent = _run_agent(
        monkeypatch, tmp_path, registry, model, "实现计算器，然后运行测试"
    )

    events = [event["event"] for event in agent.last_trace.get_events()]
    assert "rollback_finished" in events
    assert (tmp_path / "calculator.py").read_text(encoding="utf-8") == "VALUE = 1\n"
