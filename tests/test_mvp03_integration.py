from types import SimpleNamespace

import pytest

from app.core.agent import Agent
from app.core.completion import CompletionStatus, verify_completion
from app.core.context import ContextManager
from app.core.planner import create_task_plan
from app.core.task_state import PlanStateManager
from app.runtime.workspace import WorkspaceContext
from app.tools.registry import ToolRegistry
from app.tools.result import ToolResult


def _answer(text="完成"):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=text, tool_calls=None)
            )
        ]
    )


def test_simple_task_keeps_direct_agent_path(monkeypatch, tmp_path):
    class FakeModel:
        def chat(self, messages, tools):
            return _answer("直接完成")

    monkeypatch.setattr("app.core.agent.ModelAdapter", FakeModel)
    agent = Agent(WorkspaceContext.from_path(tmp_path), registry=ToolRegistry())

    assert agent.run("查看一个文件").startswith("已达到最大执行轮数")
    assert agent.last_plan is None


def test_complex_task_plan_state_and_completion_evidence():
    plan = create_task_plan("读取配置，然后修改代码，然后运行测试")
    state = PlanStateManager(plan)
    state.start_step(1)
    state.complete_step(1)
    state.start_step(2)
    state.complete_step(2)
    state.start_step(3)
    state.complete_step(3)

    decision = verify_completion(
        state.state().plan,
        tool_results=[ToolResult.success("修改已写入")],
        test_results=["测试通过"],
    )
    assert decision.status is CompletionStatus.COMPLETE
    assert state.state().completed_step_ids == (1, 2, 3)


def test_context_limit_preserves_task_and_recent_failure():
    context = ContextManager("修复任务", max_entries=2)
    context.add_tool_result(ToolResult.success("旧结果"))
    context.add_tool_result(ToolResult.failure("关键错误"))
    context.add_test_result("测试通过")

    assert context.context.task == "修复任务"
    assert any(item.error == "关键错误" for item in context.context.tool_results)
    assert context.context.truncated_count == 1


@pytest.mark.parametrize(
    ("tool_result", "test_result"),
    [
        (ToolResult.failure("工具失败"), "测试通过"),
        (
            ToolResult.success("已执行"),
            "测试目标：tests/test_module.py\n测试退出码：1\n测试结果：失败",
        ),
    ],
)
def test_completion_rejects_failed_evidence(tool_result, test_result):
    plan = create_task_plan("修改代码")
    state = PlanStateManager(plan)
    state.start_step(1)
    state.complete_step(1)

    decision = verify_completion(
        state.state().plan,
        tool_results=[tool_result],
        test_results=[test_result],
    )
    assert decision.status is CompletionStatus.BLOCKED
