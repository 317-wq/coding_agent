from types import SimpleNamespace

from app.core.agent import Agent
from app.runtime.workspace import WorkspaceContext
from app.tools.registry import ToolRegistry, ToolSpec
from app.tools.result import ToolResult

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
    for tool_name in ("read_file", "create_file", "run_tests"):
        registry.register(
            ToolSpec(
                name=tool_name,
                description=f"执行 {tool_name}",
                parameters=PARAMETERS,
                executor=lambda: ToolResult.success("步骤完成"),
            )
        )

    def call(call_id, name):
        return SimpleNamespace(
            id=call_id,
            function=SimpleNamespace(name=name, arguments="{}"),
        )

    class FakeModel:
        calls = 0

        def chat(self, messages, tools):
            self.calls += 1
            if self.calls <= 3:
                name = ("read_file", "create_file", "run_tests")[self.calls - 1]
                return _response(tool_calls=[call(f"call-{self.calls}", name)])
            return _response(content="已完成")

    monkeypatch.setattr("app.core.agent.ModelAdapter", FakeModel)
    agent = Agent(WorkspaceContext.from_path(tmp_path), registry=registry)

    assert agent.run("读取配置，然后修改代码，然后运行测试") == "已完成"
    events = agent.last_trace.get_events()
    names = [event["event"] for event in events]
    assert "complexity_decided" in names
    assert "plan_created" in names
    # 实现步骤先进入 running、再被测试阶段推进为 completed，共 4 次状态变化。
    assert names.count("plan_step_status_changed") == 4
    assert "completion_checked" in names
    completion = next(
        event for event in events if event["event"] == "completion_checked"
    )
    assert completion["details"]["completed"] is True


def test_agent_logs_are_only_printed_in_debug_mode(monkeypatch, tmp_path, capsys):
    """验证默认 Agent 不刷底层日志，调试模式恢复完整执行输出。"""

    class FakeModel:
        """返回无需工具调用的确定性完成结果。"""

        def chat(self, messages, tools):
            return _response(content="已完成")

    monkeypatch.setattr("app.core.agent.ModelAdapter", FakeModel)

    quiet_agent = Agent(WorkspaceContext.from_path(tmp_path), max_steps=1)
    quiet_agent.run("回答当前问题")
    assert capsys.readouterr().out == ""

    debug_agent = Agent(
        WorkspaceContext.from_path(tmp_path), max_steps=1, debug=True
    )
    debug_agent.run("回答当前问题")
    output = capsys.readouterr().out
    assert "[Agent] 第 1/1 轮" in output
    assert "[Trace] task_started" in output
    assert "[Trace Summary]" in output


def test_trace_records_git_snapshot_changes_without_continue_noise(
    monkeypatch, tmp_path
):
    """验证正常写入可追踪，且不会记录无动作的恢复决策。"""
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
            executor=lambda path, content: ToolResult.success(
                _create_file(tmp_path, path, content)
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

    monkeypatch.setattr("app.core.agent.ModelAdapter", FakeModel)
    agent = Agent(WorkspaceContext.from_path(tmp_path), registry=registry)

    assert agent.run("创建一个文件") == "完成"
    names = [event["event"] for event in agent.last_trace.get_events()]
    assert "git_baseline_detected" in names
    assert "snapshot_created" in names
    assert "change_set_updated" in names
    assert "recovery_decided" not in names
    tool_events = [
        event
        for event in agent.last_trace.get_events()
        if event["details"].get("tool_call_id") == "create"
    ]
    assert {event["event"] for event in tool_events} >= {
        "tool_call_started",
        "snapshot_created",
        "mutation_applied",
        "change_set_updated",
        "tool_call_finished",
    }


def _create_file(root, path, content):
    (root / path).write_text(content, encoding="utf-8")
    return "创建成功"
