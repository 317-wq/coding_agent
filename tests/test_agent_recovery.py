from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

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

    registry.register(ToolSpec("edit_file", "修改文件", PATH_PARAMETERS, failing_edit))
    registry.register(ToolSpec("step", "执行步骤", EMPTY_PARAMETERS, lambda: "完成"))
    return registry


def test_agent_replans_before_rollback_after_first_failed_mutation(
    monkeypatch, tmp_path: Path
):
    """验证首次工具失败但已落盘时保留修改，并先重新规划。"""
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
            assert "已刷新项目状态并重新规划" in str(messages)
            return _response(content="恢复后完成")

    monkeypatch.setattr("app.core.agent.ModelAdapter", FakeModel)
    agent = Agent(WorkspaceContext.from_path(tmp_path), registry=registry)

    result = agent.run("读取配置，然后修改代码，然后运行测试")

    assert result == "恢复后完成"
    assert target.read_text(encoding="utf-8") == "broken"
    assert agent.last_recovery_decision.action is RecoveryAction.CONTINUE
    assert agent.last_change_set.summary().modified_paths == ("module.py",)
    assert all(step.status.value == "completed" for step in agent.last_plan.steps)
    assert authorizer.calls == []
    events = agent.last_trace.get_events()
    snapshot = next(event for event in events if event["event"] == "snapshot_created")
    mutation = next(event for event in events if event["event"] == "mutation_applied")
    recovery_event = next(
        event
        for event in events
        if event["event"] == "recovery_decided"
        and event["details"]["action"] == "replan"
    )
    assert snapshot["details"]["paths"] == ["module.py"]
    assert mutation["details"]["tool_status"] == "failure"
    assert mutation["details"]["net_change"] is True
    assert recovery_event["details"]["tool_call_id"] == "edit"
    assert recovery_event["details"]["recovery_id"] == "recovery-edit"
    assert not any(event["event"] == "rollback_finished" for event in events)
    assert not any(
        event["event"] == "recovery_decided"
        and event["details"]["action"] == "continue"
        for event in events
    )


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


def test_agent_repairs_code_import_error_and_reruns_tests(
    monkeypatch, tmp_path: Path
):
    """验证测试收集阶段的循环导入错误会驱动代码修改和成功重跑。"""
    target = tmp_path / "calculator.py"
    target.write_text("from calculator_history import VALUE\n", encoding="utf-8")
    test_runs = 0
    edits = 0
    registry = ToolRegistry()

    def run_tests(test_path="."):
        """首次返回循环导入，代码修改后的第二次运行返回通过。"""
        nonlocal test_runs
        test_runs += 1
        if test_runs == 1:
            return ToolResult.failure(
                "测试目标：.\n测试退出码：2\n测试结果：失败\n"
                "测试错误类型：代码导入错误\n"
                "ImportError: cannot import name 'VALUE' from partially initialized module"
            )
        return ToolResult.success(
            "测试目标：.\n测试退出码：0\n测试结果：通过\n2 passed"
        )

    def edit_file(path):
        """模拟模型根据导入错误完成一次实际代码修复。"""
        nonlocal edits
        edits += 1
        target.write_text("VALUE = 1\n", encoding="utf-8")
        return "修改成功"

    registry.register(
        ToolSpec(
            "run_tests",
            "运行测试",
            {
                "type": "object",
                "properties": {"test_path": {"type": "string"}},
                "additionalProperties": False,
            },
            run_tests,
        )
    )
    registry.register(
        ToolSpec("edit_file", "修改文件", PATH_PARAMETERS, edit_file)
    )

    class FakeModel:
        calls = 0

        def chat(self, messages, tools):
            """依次执行失败测试、代码修复、成功重跑和最终完成。"""
            self.calls += 1
            if self.calls == 1:
                return _response(tool_calls=[_call("run_tests", "test-1")])
            if self.calls == 2:
                assert "先根据错误定位根因" in str(messages)
                return _response(
                    tool_calls=[
                        _call(
                            "edit_file",
                            "edit-1",
                            '{"path":"calculator.py"}',
                        )
                    ]
                )
            if self.calls == 3:
                return _response(tool_calls=[_call("run_tests", "test-2")])
            return _response(content="循环导入已修复，回归测试通过")

    monkeypatch.setattr("app.core.agent.ModelAdapter", FakeModel)
    agent = Agent(WorkspaceContext.from_path(tmp_path), registry=registry)

    result = agent.run("修复循环导入问题，并运行完整回归测试")

    assert result == "循环导入已修复，回归测试通过"
    assert target.read_text(encoding="utf-8") == "VALUE = 1\n"
    assert edits == 1
    assert test_runs == 2
    assert agent.last_result.completed is True
    assert agent.last_result.repair_attempts == 1
    assert len(agent.last_result.tests) == 1
    assert agent.last_result.tests[0].status.value == "passed"
    events = agent.last_trace.get_events()
    recovery = next(
        event
        for event in events
        if event["event"] == "recovery_decided"
        and event["details"]["action"] == "replan"
    )
    assert "项目代码导入失败" in recovery["details"]["reason"]
    assert not any(event["event"] == "rollback_finished" for event in events)


def test_agent_stops_after_rollback_when_test_repair_budget_is_exhausted(
    monkeypatch, tmp_path: Path
):
    """验证两次测试修复仍失败时只回滚一次，并停止重复执行任务。"""
    target = tmp_path / "module.py"
    target.write_text("original", encoding="utf-8")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_module.py").write_text("def test_value(): pass\n", encoding="utf-8")
    authorizer = StubAuthorizer(AuthorizationDecision.allow())
    registry = ToolRegistry(authorizer=authorizer)

    def run_tests(test_path="."):
        """持续返回断言失败，触发两次实现修复上限。"""
        return ToolResult.failure(
            f"测试目标：{test_path}\n测试退出码：1\n"
            "测试结果：失败\n测试错误类型：断言失败\n"
            "FAILED tests/test_module.py::test_value"
        )

    edit_count = 0

    def edit_file(path):
        """每次修复都产生可检测的业务实现修改。"""
        nonlocal edit_count
        edit_count += 1
        target.write_text(f"repair-{edit_count}", encoding="utf-8")
        return "修改成功"

    registry.register(
        ToolSpec(
            "run_tests",
            "运行测试",
            {
                "type": "object",
                "properties": {"test_path": {"type": "string"}},
                "additionalProperties": False,
            },
            run_tests,
        )
    )
    registry.register(ToolSpec("edit_file", "修改文件", PATH_PARAMETERS, edit_file))

    class FakeModel:
        """按失败、修复、重跑两轮的顺序返回动作。"""

        calls = 0

        def chat(self, messages, tools):
            self.calls += 1
            if self.calls in {1, 3, 5}:
                return _response(
                    tool_calls=[
                        _call(
                            "run_tests",
                            f"test-{self.calls}",
                            '{"test_path":"tests"}',
                        )
                    ]
                )
            return _response(
                tool_calls=[
                    _call(
                        "edit_file",
                        f"edit-{self.calls}",
                        '{"path":"module.py"}',
                    )
                ]
            )

    monkeypatch.setattr("app.core.agent.ModelAdapter", FakeModel)
    agent = Agent(WorkspaceContext.from_path(tmp_path), registry=registry)

    result = agent.run("修复 module.py 并运行 tests 回归测试")

    assert "不再从初始状态重复执行" in result
    assert target.read_text(encoding="utf-8") == "original"
    assert [call[0] for call in authorizer.calls] == ["rollback_task_changes"]
    events = agent.last_trace.get_events()
    assert sum(event["event"] == "rollback_finished" for event in events) == 1
    assert events[-2]["event"] == "task_stopped"


def test_simple_task_can_create_recovery_plan_after_test_failure(
    monkeypatch, tmp_path: Path
):
    """验证初始无计划的简单任务可在测试失败后安全建立恢复计划。"""
    target = tmp_path / "module.py"
    target.write_text("broken", encoding="utf-8")
    test_runs = 0
    registry = ToolRegistry()

    def run_tests(test_path="."):
        """首次返回断言失败，真实修改后的第二次测试返回通过。"""
        nonlocal test_runs
        test_runs += 1
        if test_runs == 1:
            return ToolResult.failure(
                f"测试目标：{test_path}\n测试退出码：1\n"
                "测试结果：失败\n测试错误类型：断言失败"
            )
        return f"测试目标：{test_path}\n测试退出码：0\n测试结果：通过"

    def edit_file(path):
        """模拟简单任务测试失败后的实际修复。"""
        target.write_text("fixed", encoding="utf-8")
        return "修改成功"

    registry.register(
        ToolSpec(
            "run_tests",
            "运行测试",
            {
                "type": "object",
                "properties": {"test_path": {"type": "string"}},
                "additionalProperties": False,
            },
            run_tests,
        )
    )
    registry.register(
        ToolSpec("edit_file", "修改文件", PATH_PARAMETERS, edit_file)
    )

    class FakeModel:
        """依次返回失败测试、修复、成功测试和完成总结。"""

        calls = 0

        def chat(self, messages, tools):
            """按简单任务恢复流程返回下一动作。"""
            self.calls += 1
            if self.calls == 1:
                return _response(tool_calls=[_call("run_tests", "test-1")])
            if self.calls == 2:
                return _response(
                    tool_calls=[
                        _call("edit_file", "edit-1", '{"path":"module.py"}')
                    ]
                )
            if self.calls == 3:
                return _response(tool_calls=[_call("run_tests", "test-2")])
            return _response(content="简单任务已修复并通过测试")

    monkeypatch.setattr("app.core.agent.ModelAdapter", FakeModel)
    agent = Agent(WorkspaceContext.from_path(tmp_path), registry=registry)

    result = agent.run("运行测试")

    assert result == "简单任务已修复并通过测试"
    assert target.read_text(encoding="utf-8") == "fixed"
    assert agent.last_plan is not None
    assert agent.last_result.completed is True
    assert test_runs == 2


def test_agent_forces_test_rerun_after_repair_before_accepting_summary(
    monkeypatch, tmp_path: Path
):
    """验证失败后的实际修改必须重跑，模型提前总结不会终止恢复流程。"""
    target = tmp_path / "module.py"
    target.write_text("broken", encoding="utf-8")
    test_runs = 0
    deferred_reads = 0
    registry = ToolRegistry()

    def run_tests(test_path="."):
        """首次失败，修改后的强制重跑返回成功。"""
        nonlocal test_runs
        test_runs += 1
        if test_runs == 1:
            return ToolResult.failure(
                f"测试目标：{test_path}\n测试退出码：1\n"
                "测试结果：失败\n测试错误类型：断言失败"
            )
        return f"测试目标：{test_path}\n测试退出码：0\n测试结果：通过"

    def edit_file(path):
        """模拟测试失败后对业务实现进行一次真实修改。"""
        target.write_text("fixed", encoding="utf-8")
        return "修改成功"

    def read_file(path):
        """记录本应被恢复门控暂缓的读取调用。"""
        nonlocal deferred_reads
        deferred_reads += 1
        return target.read_text(encoding="utf-8")

    registry.register(
        ToolSpec(
            "run_tests",
            "运行测试",
            {
                "type": "object",
                "properties": {"test_path": {"type": "string"}},
                "additionalProperties": False,
            },
            run_tests,
        )
    )
    registry.register(
        ToolSpec("edit_file", "修改文件", PATH_PARAMETERS, edit_file)
    )
    registry.register(
        ToolSpec("read_file", "读取文件", PATH_PARAMETERS, read_file)
    )

    class FakeModel:
        """先提前总结一次，再遵循控制层要求重跑测试。"""

        calls = 0
        tool_sets: ClassVar[list[tuple[str, ...]]] = []

        def chat(self, messages, tools):
            """按失败、修改、错误总结、重跑和完成的顺序返回响应。"""
            self.calls += 1
            self.tool_sets.append(
                tuple(item["function"]["name"] for item in tools)
            )
            if self.calls == 1:
                return _response(
                    tool_calls=[
                        _call("run_tests", "test-1", '{"test_path":"tests/"}')
                    ]
                )
            if self.calls == 2:
                return _response(
                    tool_calls=[
                        _call("edit_file", "edit-1", '{"path":"module.py"}')
                    ]
                )
            if self.calls == 3:
                return _response(
                    tool_calls=[
                        _call("read_file", "read-after-edit", '{"path":"module.py"}')
                    ]
                )
            if self.calls == 4:
                assert "下一步只能调用 run_tests" in str(messages)
                return _response(
                    tool_calls=[
                        _call("run_tests", "test-2", '{"test_path":"tests/"}')
                    ]
                )
            return _response(content="代码修复且测试通过。")

    monkeypatch.setattr("app.core.agent.ModelAdapter", FakeModel)
    agent = Agent(WorkspaceContext.from_path(tmp_path), registry=registry)

    result = agent.run("修复代码并运行 tests/ 回归测试")

    assert result == "代码修复且测试通过。"
    assert test_runs == 2
    assert deferred_reads == 0
    assert agent.last_result.completed is True
    assert agent.last_result.repair_attempts == 1
    assert FakeModel.tool_sets[2] == ("run_tests",)
    assert FakeModel.tool_sets[3] == ("run_tests",)
    rerun_event = next(
        event
        for event in agent.last_trace.get_events()
        if event["event"] == "test_rerun_required"
    )
    assert rerun_event["details"]["test_target"] == "tests/"
    assert rerun_event["details"]["deferred_tool_calls"] == ["read_file"]


def test_agent_blocks_identical_failed_test_until_code_changes(
    monkeypatch, tmp_path: Path
):
    """验证模型不能在没有新文件修改时原样重复失败测试。"""
    test_runs = 0
    registry = ToolRegistry()

    def run_tests(test_path="."):
        """始终返回可复现的断言失败。"""
        nonlocal test_runs
        test_runs += 1
        return ToolResult.failure(
            "测试目标：tests\n测试退出码：1\n测试结果：失败\n"
            "测试错误类型：断言失败\nFAILED tests/test_sample.py::test_value"
        )

    registry.register(
        ToolSpec(
            "run_tests",
            "运行测试",
            {
                "type": "object",
                "properties": {"test_path": {"type": "string"}},
                "additionalProperties": False,
            },
            run_tests,
        )
    )

    class FakeModel:
        """先连续请求相同测试，再根据硬门控结束任务。"""

        calls = 0

        def chat(self, messages, tools):
            """返回测试调用或最终未完成说明。"""
            self.calls += 1
            if self.calls <= 2:
                return _response(
                    tool_calls=[
                        _call(
                            "run_tests",
                            f"test-{self.calls}",
                            '{"test_path":"tests/"}',
                        )
                    ]
                )
            return _response(content="测试与约束冲突，无法安全继续修改。")

    monkeypatch.setattr("app.core.agent.ModelAdapter", FakeModel)
    agent = Agent(WorkspaceContext.from_path(tmp_path), registry=registry)

    result = agent.run("修复代码并运行 tests/，测试失败时分析原因")

    assert result.startswith("最新回归测试仍然失败")
    assert test_runs == 1
    events = agent.last_trace.get_events()
    rejection = next(
        event
        for event in events
        if event["event"] == "safety_rejected"
        and event["details"]["rejection_type"]
        == "test_rerun_without_mutation"
    )
    assert rejection["details"]["tool_call_id"] == "test-2"
    assert agent.last_result.status.value == "blocked"
