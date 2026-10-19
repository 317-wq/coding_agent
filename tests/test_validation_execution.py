"""验证一次性验收任务的规划和执行控制。"""

from types import SimpleNamespace

from app.core.agent import Agent
from app.core.complexity import TaskComplexity, judge_task_complexity
from app.core.task_safety import TaskSafetySession
from app.runtime.workspace import WorkspaceContext
from app.tools.registry import ToolRegistry, ToolSpec
from app.tools.result import ToolResult


def _response(tool_calls=None, content=None):
    """构造模型响应。"""
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tool_calls))]
    )


def _call(name: str, arguments: str):
    """构造函数工具调用。"""
    return SimpleNamespace(
        id="call-1",
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def test_boundary_validation_is_simple_without_generic_plan():
    """验证安全边界验收任务不会被判为通用复杂开发任务。"""
    decision = judge_task_complexity("验证安全边界：读取越界路径并记录结果")
    assert decision.complexity is TaskComplexity.SIMPLE
    assert decision.requires_planning is False


def test_development_task_with_acceptance_section_is_not_validation_only(tmp_path):
    """验证完整开发需求不会因出现验收或完成状态字样而失败即停。"""
    task = (
        "开发 todo_cli 项目并创建代码和测试。\n"
        "验收标准：所有命令可用，并验证是否通过回归测试。"
    )

    decision = judge_task_complexity(task)
    session = TaskSafetySession(WorkspaceContext.from_path(tmp_path), task=task)

    assert decision.requires_planning is True
    assert session.validation_only is False


def test_read_only_final_acceptance_is_validation_only(tmp_path):
    """验证带否定开发约束的最终验收使用轻量一次性执行路径。"""
    task = (
        "对当前项目执行最终验收，只列出文件并运行完整测试，"
        "不逐个读取文件，不修改代码，并报告自动修复次数。"
    )

    decision = judge_task_complexity(task)
    session = TaskSafetySession(WorkspaceContext.from_path(tmp_path), task=task)

    assert decision.requires_planning is False
    assert session.validation_only is True


def test_validation_failure_stops_without_repeating_tests(monkeypatch, tmp_path):
    """验证验收测试失败后只执行一次，不再运行其他测试或重规划。"""
    calls: list[str] = []
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            "run_tests",
            "运行测试",
            {"type": "object", "properties": {"test_path": {"type": "string"}}, "additionalProperties": False},
            lambda test_path=".": (calls.append(test_path), ToolResult.failure("测试路径不存在：" + test_path))[1],
        )
    )

    class FakeModel:
        requests = 0

        def chat(self, messages, tools):
            self.requests += 1
            return _response([_call("run_tests", '{"test_path":"../../missing.py"}')])

    monkeypatch.setattr("app.core.agent.ModelAdapter", FakeModel)
    agent = Agent(WorkspaceContext.from_path(tmp_path), registry=registry)
    result = agent.run("验收安全边界：测试路径越界时只尝试一次并记录结果")

    assert calls == ["../../missing.py"]
    assert "测试路径不存在" in result
    assert agent.last_trace.get_statistics()["model_requests"] == 1
