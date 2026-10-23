"""覆盖变更集无法安全分析时 Agent 主循环的降级行为。

这些用例保护的是“不允许异常穿透 CLI、不允许报告丢失”的边界：触碰路径被外部
改成符号链接时，任务必须以 ``blocked`` 结束并给出可读原因，而不是抛异常。
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.agent import Agent
from app.core.task_result import TaskStatus
from app.runtime.workspace import WorkspaceContext
from app.tools.registry import ToolRegistry, ToolSpec

PATH_PARAMETERS = {
    "type": "object",
    "properties": {"path": {"type": "string"}},
    "required": ["path"],
    "additionalProperties": False,
}


def _response(content=None, tool_calls=None):
    """构造一个最小模型响应。"""
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content, tool_calls=tool_calls)
            )
        ]
    )


def _call(name, call_id, arguments):
    """构造一次工具调用。"""
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def test_agent_degrades_to_blocked_when_change_set_is_unreadable(
    monkeypatch, tmp_path: Path
):
    """验证触碰路径被替换为符号链接时任务降级为 blocked，而不是抛出异常。"""

    def break_into_symlink(path: str) -> str:
        """写入文件后立刻替换为符号链接，使变更集无法安全比较。"""
        target = tmp_path / path
        target.write_text("内容", encoding="utf-8")
        target.unlink()
        target.symlink_to(tmp_path / "elsewhere")
        return "已写入"

    registry = ToolRegistry()
    registry.register(
        ToolSpec("edit_file", "修改文件", PATH_PARAMETERS, break_into_symlink)
    )

    class FakeModel:
        """第一次调用工具触发变更集异常，之后返回普通总结。"""

        calls = 0

        def chat(self, messages, tools):
            self.calls += 1
            if self.calls == 1:
                return _response(
                    tool_calls=[_call("edit_file", "edit", '{"path":"module.py"}')]
                )
            return _response(content="已完成")

    monkeypatch.setattr("app.core.agent.ModelAdapter", FakeModel)
    agent = Agent(WorkspaceContext.from_path(tmp_path), registry=registry)

    answer = agent.run("修改代码，然后运行测试")

    assert "变更集状态无法安全分析" in answer
    assert agent.last_result is not None
    assert agent.last_result.status is TaskStatus.BLOCKED
    assert agent.last_result.completed is False
    assert agent.last_result.stop_reason

    events = [event["event"] for event in agent.last_trace.get_events()]
    assert "change_set_failed" in events
    assert "task_stopped" in events
    assert "final_state_verification_failed" in events


def test_keyboard_interrupt_is_not_swallowed_by_degradation(
    monkeypatch, tmp_path: Path
):
    """验证新增的降级捕获不会吞掉用户中断。"""

    class FakeModel:
        """直接抛出键盘中断，模拟用户按 Ctrl+C。"""

        def chat(self, messages, tools):
            raise KeyboardInterrupt

    monkeypatch.setattr("app.core.agent.ModelAdapter", FakeModel)
    agent = Agent(WorkspaceContext.from_path(tmp_path))

    with pytest.raises(KeyboardInterrupt):
        agent.run("修改代码，然后运行测试")
