"""验证 Agent 主流程会生成结构化任务结果。"""

from types import SimpleNamespace

from app.cli.report import render_task_report
from app.core.agent import Agent
from app.core.task_result import TaskStatus
from app.runtime.workspace import WorkspaceContext
from app.tools.registry import ToolRegistry, ToolSpec


def _response(content=None):
    """构造没有工具调用的模型响应。"""
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=None))]
    )


def test_agent_run_populates_structured_result_and_cli_report(monkeypatch, tmp_path):
    """验证 Agent.run 完成后可直接渲染同一份 TaskResult。"""
    class FakeModel:
        def chat(self, messages, tools):
            return _response("已完成")

    monkeypatch.setattr("app.core.agent.ModelAdapter", FakeModel)
    agent = Agent(WorkspaceContext.from_path(tmp_path), registry=ToolRegistry())

    assert agent.run("查看项目").startswith("已达到最大执行轮数")
    assert agent.last_result is not None
    assert agent.last_result.status is TaskStatus.STOPPED
    assert '"status": "stopped"' in render_task_report(agent.last_result, "json")


def test_agent_report_uses_final_state_instead_of_model_claims(monkeypatch, tmp_path):
    """验证模型虚假总结不会覆盖最终文件状态和 Trace 调用统计。"""
    registry = ToolRegistry()

    def create_file(path, content):
        """在测试工作区实际创建指定文件。"""
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
        calls = 0

        def chat(self, messages, tools):
            """先创建文件，再给出与事实相反的模型总结。"""
            self.calls += 1
            if self.calls == 1:
                tool_call = SimpleNamespace(
                    id="create-1",
                    function=SimpleNamespace(
                        name="create_file",
                        arguments='{"path":"result.txt","content":"real"}',
                    ),
                )
                return SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(
                                content=None,
                                tool_calls=[tool_call],
                            )
                        )
                    ]
                )
            return _response("没有修改文件，也没有调用任何工具")

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
    assert result.trace_references[-1].event == "final_state_verified"
    assert agent.last_trace.events[-1]["event"] == "final_state_verified"


def test_final_verification_failure_produces_blocked_report(monkeypatch, tmp_path):
    """验证最终状态无法核验时失败关闭，并仍然生成结构化报告。"""

    class FakeModel:
        def chat(self, messages, tools):
            """返回没有可验证执行证据的模型完成声明。"""
            return _response("已经完成")

    def fail_verification(self, workspace, change_set, trace):
        """模拟最终读取磁盘状态时发生冲突。"""
        raise RuntimeError("文件状态冲突")

    monkeypatch.setattr("app.core.agent.ModelAdapter", FakeModel)
    monkeypatch.setattr(
        "app.core.agent.FinalStateVerifier.verify",
        fail_verification,
    )
    agent = Agent(
        WorkspaceContext.from_path(tmp_path),
        max_steps=1,
        registry=ToolRegistry(),
    )

    agent.run("查看项目")

    assert agent.last_result is not None
    assert agent.last_result.status is TaskStatus.BLOCKED
    assert agent.last_result.final_verification.verified is False
    assert "最终状态核验失败" in agent.last_result.stop_reason
