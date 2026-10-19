import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import tools
from app.cli.main import _parse_options
from app.core.agent import Agent
from app.runtime.workspace import WorkspaceContext
from app.tools.authorization import AuthorizationDecision
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
    finished = next(event for event in events if event["event"] == "tool_call_finished")
    assert started["details"]["workspace"] == str(tmp_path)
    assert finished["details"]["status"] == "failure"
    assert finished["details"]["error"] == "boom"


def test_replanned_task_can_complete_after_earlier_tool_failure(
    monkeypatch, tmp_path: Path
):
    """验证已触发重规划的路径错误不会永久阻塞后续成功任务。"""
    target = tmp_path / "module.py"
    target.write_text("before", encoding="utf-8")
    registry = ToolRegistry()
    path_parameters = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
        "additionalProperties": False,
    }

    def read_file(path):
        """模拟错误路径失败、正确路径成功的读取工具。"""
        if path != "module.py":
            return ToolResult.failure(f"文件不存在：{path}")
        return target.read_text(encoding="utf-8")

    def edit_file(path):
        """模拟恢复计划中的真实文件修改。"""
        target.write_text("after", encoding="utf-8")
        return "修改成功"

    registry.register(ToolSpec("read_file", "读取文件", path_parameters, read_file))
    registry.register(ToolSpec("edit_file", "修改文件", path_parameters, edit_file))
    registry.register(
        ToolSpec(
            "run_tests",
            "运行测试",
            {
                "type": "object",
                "properties": {"test_path": {"type": "string"}},
                "additionalProperties": False,
            },
            lambda test_path=".": (
                f"测试目标：{test_path}\n测试退出码：0\n测试结果：通过"
            ),
        )
    )

    class FakeModel:
        """依次制造路径错误、恢复执行并提交完成说明。"""

        calls = 0

        def chat(self, messages, tools):
            """按恢复场景返回下一项工具调用。"""
            self.calls += 1
            calls = {
                1: make_tool_call(
                    "read_file", "wrong-read", '{"path":"src/module.py"}'
                ),
                2: make_tool_call(
                    "read_file", "correct-read", '{"path":"module.py"}'
                ),
                3: make_tool_call(
                    "edit_file", "repair-edit", '{"path":"module.py"}'
                ),
                4: make_tool_call(
                    "run_tests", "passing-test", '{"test_path":"tests"}'
                ),
            }
            if self.calls in calls:
                return make_response(tool_calls=[calls[self.calls]])
            return make_response(content="路径错误已恢复，修改和测试均已完成。")

    monkeypatch.setattr("app.core.agent.ModelAdapter", FakeModel)
    agent = Agent(WorkspaceContext.from_path(tmp_path), registry=registry)

    result = agent.run("读取配置，然后修改代码，然后运行测试")

    assert result == "路径错误已恢复，修改和测试均已完成。"
    assert target.read_text(encoding="utf-8") == "after"
    assert agent.last_result.completed is True
    assert not any(
        event["event"] == "rollback_authorization_requested"
        for event in agent.last_trace.get_events()
    )


def test_test_replan_preserves_completed_read_and_implementation_steps(
    monkeypatch, tmp_path: Path
):
    """验证测试修复重规划不会要求重复已完成的阅读和实现步骤。"""
    target = tmp_path / "module.py"
    target.write_text("broken", encoding="utf-8")
    registry = ToolRegistry()
    path_parameters = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
        "additionalProperties": False,
    }
    test_runs = 0

    def edit_file(path):
        """依次完成初始实现和测试失败后的修复。"""
        current = target.read_text(encoding="utf-8")
        target.write_text(f"{current}-fixed", encoding="utf-8")
        return "修改成功"

    def run_tests(test_path="."):
        """首次返回断言失败，修复后返回通过。"""
        nonlocal test_runs
        test_runs += 1
        if test_runs == 1:
            return ToolResult.failure(
                f"测试目标：{test_path}\n测试退出码：1\n"
                "测试结果：失败\n测试错误类型：断言失败"
            )
        return f"测试目标：{test_path}\n测试退出码：0\n测试结果：通过"

    registry.register(
        ToolSpec(
            "read_file",
            "读取文件",
            path_parameters,
            lambda path: target.read_text(encoding="utf-8"),
        )
    )
    registry.register(ToolSpec("edit_file", "修改文件", path_parameters, edit_file))
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
        """执行完整的读取、实现、失败测试、修复和成功测试流程。"""

        calls = 0

        def chat(self, messages, tools):
            """返回当前阶段需要执行的唯一工具调用。"""
            self.calls += 1
            sequence = {
                1: make_tool_call("read_file", "read", '{"path":"module.py"}'),
                2: make_tool_call("edit_file", "initial-edit", '{"path":"module.py"}'),
                3: make_tool_call("run_tests", "failed-test", '{"test_path":"tests"}'),
                4: make_tool_call("edit_file", "repair-edit", '{"path":"module.py"}'),
                5: make_tool_call("run_tests", "passed-test", '{"test_path":"tests"}'),
            }
            if self.calls in sequence:
                return make_response(tool_calls=[sequence[self.calls]])
            return make_response(content="修复和回归测试均已完成。")

    monkeypatch.setattr("app.core.agent.ModelAdapter", FakeModel)
    agent = Agent(WorkspaceContext.from_path(tmp_path), registry=registry)

    result = agent.run("读取配置，然后修改代码，然后运行测试")

    assert result == "修复和回归测试均已完成。"
    assert agent.last_result.completed is True
    assert agent.model.calls == 6
    assert all(step.status.value == "completed" for step in agent.last_plan.steps)


def test_workspace_boundary_rejection_has_explicit_correlated_trace(
    monkeypatch, tmp_path: Path
):
    """验证路径安全拒绝拥有独立事件并关联到对应工具调用。"""
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            "read_file",
            "读取文件",
            {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
                "additionalProperties": False,
            },
            lambda path: ToolResult.failure("禁止访问工作目录之外的路径。"),
        )
    )

    class FakeModel:
        def chat(self, messages, tools):
            """请求一次越界路径读取。"""
            return make_response(
                tool_calls=[
                    make_tool_call(
                        "read_file",
                        "boundary-1",
                        '{"path":"../../README.md"}',
                    )
                ]
            )

    monkeypatch.setattr("app.core.agent.ModelAdapter", FakeModel)
    agent = Agent(WorkspaceContext.from_path(tmp_path), registry=registry)

    result = agent.run("读取 ../../README.md")

    assert "工作目录之外" in result
    events = agent.last_trace.get_events()
    rejection = next(event for event in events if event["event"] == "safety_rejected")
    finished = next(event for event in events if event["event"] == "tool_call_finished")
    stopped = next(event for event in events if event["event"] == "task_stopped")
    assert rejection["details"]["rejection_type"] == "workspace_boundary"
    assert rejection["details"]["tool_call_id"] == "boundary-1"
    assert finished["details"]["tool_call_id"] == "boundary-1"
    assert stopped["details"]["tool_call_id"] == "boundary-1"
    assert rejection["trace_id"] == finished["trace_id"] == stopped["trace_id"]
    assert rejection["sequence"] < finished["sequence"] < stopped["sequence"]
    assert agent.last_trace.get_statistics()["tool_calls"]["unfinished"] == 0


def test_failed_edit_without_disk_change_does_not_report_snapshot_or_mutation(
    monkeypatch, tmp_path: Path
):
    """验证编辑失败且文件未变化时 Trace 不会声称已创建有效修改快照。"""
    target = tmp_path / "module.py"
    target.write_text("return 42", encoding="utf-8")
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            "edit_file",
            "修改文件",
            {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
                "additionalProperties": False,
            },
            lambda path: ToolResult.failure("旧文本不存在"),
        )
    )

    class FakeModel:
        requests = 0

        def chat(self, messages, tools):
            """首次请求失败编辑，后续返回停止说明。"""
            self.requests += 1
            if self.requests == 1:
                return make_response(
                    tool_calls=[
                        make_tool_call("edit_file", "call-1", '{"path":"module.py"}')
                    ]
                )
            return make_response(content="编辑失败")

    monkeypatch.setattr("app.core.agent.ModelAdapter", FakeModel)
    agent = Agent(WorkspaceContext.from_path(tmp_path), registry=registry)

    agent.run("修改 module.py")

    assert target.read_text(encoding="utf-8") == "return 42"
    events = agent.last_trace.get_events()
    names = [event["event"] for event in events]
    summary = [event for event in events if event["event"] == "change_set_updated"][-1][
        "details"
    ]
    assert "snapshot_created" not in names
    assert "mutation_applied" not in names
    assert summary["comparison_basis"] == "task_initial_state"
    assert summary["touched_count"] == 1
    assert summary["unchanged_count"] == 1
    assert summary["net_changed_count"] == 0
    assert summary["modified_paths"] == []


def test_failed_edit_does_not_reuse_prior_net_change_as_new_mutation(
    monkeypatch, tmp_path: Path
):
    """验证文件已有净变化时，后续未落盘失败不会重复记录修改事件。"""
    target = tmp_path / "module.py"
    target.write_text("before", encoding="utf-8")

    class DenyAuthorizer:
        def authorize(self, tool_name, arguments):
            """拒绝测试中的回滚请求以保留检查现场。"""
            return AuthorizationDecision.deny("测试保留现场")

    registry = ToolRegistry(authorizer=DenyAuthorizer())

    def edit_file(path, content):
        """首次写入文件，指定失败内容时保持磁盘不变。"""
        if content == "fail":
            return ToolResult.failure("模拟未落盘失败")
        target.write_text(content, encoding="utf-8")
        return "修改成功"

    registry.register(
        ToolSpec(
            "edit_file",
            "修改文件",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
            edit_file,
        )
    )

    class FakeModel:
        requests = 0

        def chat(self, messages, tools):
            """依次返回成功写入和未落盘失败写入。"""
            self.requests += 1
            content = "after" if self.requests == 1 else "fail"
            return make_response(
                tool_calls=[
                    make_tool_call(
                        "edit_file",
                        f"call-{self.requests}",
                        json.dumps({"path": "module.py", "content": content}),
                    )
                ]
            )

    monkeypatch.setattr("app.core.agent.ModelAdapter", FakeModel)
    agent = Agent(WorkspaceContext.from_path(tmp_path), registry=registry)

    agent.run("修改 module.py")

    events = agent.last_trace.get_events()
    assert target.read_text(encoding="utf-8") == "after"
    assert sum(event["event"] == "snapshot_created" for event in events) == 1
    assert sum(event["event"] == "mutation_applied" for event in events) == 1


def test_agent_returns_cancelled_json_and_blocks_completion(
    monkeypatch, tmp_path: Path
):
    registry = ToolRegistry()
    registry.register(make_spec("cancel", lambda: ToolResult.cancelled("用户取消")))

    class FakeModel:
        def __init__(self):
            self.requests = 0

        def chat(self, messages, tools):
            self.requests += 1
            if self.requests == 1:
                return make_response(tool_calls=[make_tool_call("cancel", "call-1")])
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
    aborted = [
        event
        for event in agent.last_trace.get_events()
        if event["event"] == "tool_batch_aborted"
    ]
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
        '{"path":"completion_probe.py","old_text":"return 42","new_text":"return 43"}'
    )

    class FakeModel:
        requests = 0

        def chat(self, messages, tools):
            self.requests += 1
            if self.requests <= 2:
                return make_response(
                    tool_calls=[
                        make_tool_call("edit_file", f"call-{self.requests}", arguments)
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
    registry.register(make_spec("once", lambda: executions.append("once") or "ok"))

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
            return make_response(tool_calls=[make_tool_call("noop", "call-1")])

    monkeypatch.setattr("app.core.agent.ModelAdapter", FakeModel)
    agent = Agent(WorkspaceContext.from_path(tmp_path), max_steps=1, registry=registry)

    assert agent.run("测试轮数上限").startswith("已达到最大执行轮数 1")


def test_agent_uses_larger_default_execution_limits(tmp_path: Path):
    """验证默认轮数和工具调用数足以支持多轮诊断修复。"""
    agent = Agent(WorkspaceContext.from_path(tmp_path))

    assert agent.max_steps == 20
    assert agent.max_tool_calls == 40


def test_cli_accepts_custom_execution_limits(monkeypatch, tmp_path: Path):
    """验证 CLI 参数能够覆盖默认执行资源上限。"""
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "agent",
            "--workspace",
            str(tmp_path),
            "--max-steps",
            "30",
            "--max-tool-calls",
            "60",
        ],
    )

    workspace, output_format, max_steps, max_tool_calls = _parse_options()

    assert workspace.root == tmp_path.resolve()
    assert output_format == "text"
    assert max_steps == 30
    assert max_tool_calls == 60


def test_final_report_uses_disk_and_trace_instead_of_model_claims(
    monkeypatch, tmp_path: Path
):
    """验证最终报告不会采信与实际文件及调用记录冲突的模型总结。"""
    registry = ToolRegistry()

    def create_file(path, content):
        """在测试工作区实际创建文件。"""
        (tmp_path / path).write_text(content, encoding="utf-8")
        return "创建成功"

    registry.register(
        ToolSpec(
            "create_file",
            "创建文件",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
            create_file,
        )
    )

    class FakeModel:
        requests = 0

        def chat(self, messages, tools):
            """先调用创建工具，再返回与事实冲突的自由文本。"""
            self.requests += 1
            if self.requests == 1:
                return make_response(
                    tool_calls=[
                        make_tool_call(
                            "create_file",
                            "create-1",
                            '{"path":"result.txt","content":"real"}',
                        )
                    ]
                )
            return make_response(content="没有修改文件，也没有调用工具")

    monkeypatch.setattr("app.core.agent.ModelAdapter", FakeModel)
    agent = Agent(WorkspaceContext.from_path(tmp_path), registry=registry)

    agent.run("创建 result.txt")

    result = agent.last_result
    assert result is not None
    assert result.changes.created == ("result.txt",)
    assert result.tool_calls.calls_started == 1
    assert result.tool_calls.calls_finished == 1
    assert result.tool_calls.succeeded == 1
    assert result.trace_event_count == len(agent.last_trace.events)
    assert result.final_verification.verified is True
    assert result.final_verification.verified_path_count == 1
    assert agent.last_trace.events[-1]["event"] == "final_state_verified"
