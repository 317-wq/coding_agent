"""R5 重构的行为基线：用确定性假模型复现典型任务场景。

每个场景装配一个已运行完成的 :class:`Agent`，供 golden 测试比对
``TaskResult`` 的规范化快照。场景只使用假模型与桩工具，不依赖网络或真实
模型，因此可以在任何环境重复得到同一份结果。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.core.agent import Agent
from app.core.task_result import TaskResult
from app.runtime.workspace import WorkspaceContext
from app.tools.authorization import AuthorizationDecision
from app.tools.registry import ToolRegistry, ToolSpec
from app.tools.result import ToolResult
from app.tools.test_result import TestRunResult, TestRunStatus

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
TEST_PATH_PARAMETERS = {
    "type": "object",
    "properties": {"test_path": {"type": "string"}},
    "required": [],
    "additionalProperties": False,
}

VOLATILE_FIELDS = ("trace_references", "trace_event_count")
"""随实现调整而变化的字段，不纳入行为基线。"""


def _response(content: str | None = None, tool_calls: list | None = None):
    """构造一个最小模型响应。"""
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content, tool_calls=tool_calls)
            )
        ]
    )


def _call(name: str, call_id: str, arguments: str = "{}"):
    """构造一次工具调用。"""
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


class ScriptedModel:
    """按脚本顺序返回响应；最后一步会重复返回，保证任务能收尾。"""

    def __init__(self, steps: list[Any]) -> None:
        """保存脚本步骤。"""
        self._steps = list(steps)

    def chat(self, messages, tools):
        """返回下一条脚本响应。"""
        step = self._steps.pop(0) if len(self._steps) > 1 else self._steps[0]
        if isinstance(step, str):
            return _response(content=step)
        return _response(tool_calls=step)


class StubAuthorizer:
    """按固定结论回应授权请求。"""

    def __init__(self, decision: AuthorizationDecision) -> None:
        """保存固定授权结论。"""
        self.decision = decision

    def authorize(self, tool_name, arguments):
        """返回固定授权结论。"""
        return self.decision


def _passed_test(target: str = "tests") -> ToolResult:
    """构造一次通过的测试结果。"""
    run = TestRunResult(
        target=target,
        exit_code=0,
        status=TestRunStatus.PASSED,
        output="1 passed",
    )
    text = f"测试目标：{target}\n测试退出码：0\n测试结果：通过\n1 passed"
    return ToolResult.success(text, data=run)


def _failed_test(target: str = "tests") -> ToolResult:
    """构造一次失败的测试结果。"""
    run = TestRunResult(
        target=target,
        exit_code=1,
        status=TestRunStatus.FAILED,
        error_kind="断言失败",
        output="assert 1 == 2",
    )
    text = (
        f"测试目标：{target}\n"
        "测试退出码：1\n"
        "测试结果：失败\n"
        "测试错误类型：断言失败\n"
        "assert 1 == 2"
    )
    return ToolResult.failure(text, data=run)


def _run_agent(monkeypatch, tmp_path: Path, registry: ToolRegistry, model, task: str):
    """用指定注册表与脚本模型运行一次任务。"""
    monkeypatch.setattr("app.core.agent.ModelAdapter", lambda: model)
    agent = Agent(WorkspaceContext.from_path(tmp_path), registry=registry)
    agent.run(task)
    return agent


def normal_completion(monkeypatch, tmp_path: Path) -> Agent:
    """三个工具依次推进计划后正常完成。"""
    registry = ToolRegistry()
    for name in ("read_file", "create_file"):
        registry.register(
            ToolSpec(
                name,
                f"执行 {name}",
                EMPTY_PARAMETERS,
                lambda: ToolResult.success("ok"),
            )
        )
    registry.register(
        ToolSpec(
            "run_tests",
            "运行测试",
            TEST_PATH_PARAMETERS,
            lambda test_path=".": _passed_test(test_path),
        )
    )
    model = ScriptedModel(
        [
            [_call("read_file", "c1")],
            [_call("create_file", "c2")],
            [_call("run_tests", "c3")],
            "已完成",
        ]
    )
    return _run_agent(
        monkeypatch,
        tmp_path,
        registry,
        model,
        "读取配置，然后修改代码，然后运行测试",
    )


def test_failure_then_repair(monkeypatch, tmp_path: Path) -> Agent:
    """测试失败后修改代码并重跑通过，自动修复计一次。"""
    (tmp_path / "calculator.py").write_text("VALUE = 1\n", encoding="utf-8")
    state = {"tests": 0}

    edits = {"count": 0}

    def edit(path: str) -> ToolResult:
        edits["count"] += 1
        (tmp_path / path).write_text(
            f"VALUE = {edits['count'] + 1}\n", encoding="utf-8"
        )
        return ToolResult.success(f"修改文件成功并已核验：{path}")

    def run_tests(test_path: str = ".") -> ToolResult:
        state["tests"] += 1
        return (
            _failed_test(test_path)
            if state["tests"] == 1
            else _passed_test(test_path)
        )

    registry = ToolRegistry()
    registry.register(ToolSpec("edit_file", "修改文件", PATH_PARAMETERS, edit))
    registry.register(
        ToolSpec("run_tests", "运行测试", TEST_PATH_PARAMETERS, run_tests)
    )
    model = ScriptedModel(
        [
            [_call("edit_file", "e1", '{"path":"calculator.py"}')],
            [_call("run_tests", "t1", '{"test_path":"tests"}')],
            [_call("edit_file", "e2", '{"path":"calculator.py"}')],
            [_call("run_tests", "t2", '{"test_path":"tests"}')],
            "已完成",
        ]
    )
    return _run_agent(monkeypatch, tmp_path, registry, model, "实现计算器，然后运行测试")


def _repair_exhaustion(
    monkeypatch, tmp_path: Path, decision: AuthorizationDecision
) -> Agent:
    """让两次自动修复都失败，触发回滚授权流程。"""
    (tmp_path / "calculator.py").write_text("VALUE = 1\n", encoding="utf-8")

    edits = {"count": 0}

    def edit(path: str) -> ToolResult:
        edits["count"] += 1
        (tmp_path / path).write_text(
            f"VALUE = {edits['count'] + 8}\n", encoding="utf-8"
        )
        return ToolResult.success(f"修改文件成功并已核验：{path}")

    registry = ToolRegistry(authorizer=StubAuthorizer(decision))
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
    return _run_agent(monkeypatch, tmp_path, registry, model, "实现计算器，然后运行测试")


def rollback_approved(monkeypatch, tmp_path: Path) -> Agent:
    """两次修复耗尽后用户同意回滚。"""
    return _repair_exhaustion(monkeypatch, tmp_path, AuthorizationDecision.allow())


def rollback_denied(monkeypatch, tmp_path: Path) -> Agent:
    """两次修复耗尽后用户拒绝回滚。"""
    return _repair_exhaustion(monkeypatch, tmp_path, AuthorizationDecision.deny("用户拒绝"))


def change_set_failure(monkeypatch, tmp_path: Path) -> Agent:
    """触碰路径被外部改成符号链接，任务降级为 blocked。"""

    def break_into_symlink(path: str) -> ToolResult:
        target = tmp_path / path
        target.write_text("内容", encoding="utf-8")
        target.unlink()
        target.symlink_to(tmp_path / "elsewhere")
        return ToolResult.success("已写入")

    registry = ToolRegistry()
    registry.register(
        ToolSpec("edit_file", "修改文件", PATH_PARAMETERS, break_into_symlink)
    )
    model = ScriptedModel(
        [
            [_call("edit_file", "e1", '{"path":"module.py"}')],
            "已完成",
        ]
    )
    return _run_agent(monkeypatch, tmp_path, registry, model, "修改代码，然后运行测试")


def replan_limit(monkeypatch, tmp_path: Path) -> Agent:
    """连续失败触发重规划，达到上限后停止。"""

    def failing_edit(path: str) -> ToolResult:
        return ToolResult.failure("模拟修改失败")

    registry = ToolRegistry()
    registry.register(
        ToolSpec("edit_file", "修改文件", PATH_PARAMETERS, failing_edit)
    )
    model = ScriptedModel(
        [
            [_call("edit_file", f"e{i}", '{"path":"module.py"}')]
            for i in range(1, 6)
        ]
        + ["已完成"]
    )
    return _run_agent(monkeypatch, tmp_path, registry, model, "修改代码，然后运行测试")


SCENARIOS: dict[str, Callable[..., Agent]] = {
    "normal_completion": normal_completion,
    "test_failure_then_repair": test_failure_then_repair,
    "rollback_approved": rollback_approved,
    "rollback_denied": rollback_denied,
    "change_set_failure": change_set_failure,
    "replan_limit": replan_limit,
}


def normalize_result(result: TaskResult) -> dict[str, Any]:
    """把任务结果转换为不包含易变字段的行为快照。"""
    data = result.to_dict()
    for field in VOLATILE_FIELDS:
        data.pop(field, None)
    return data
