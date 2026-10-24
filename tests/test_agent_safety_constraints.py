"""验证任务级用户约束和工作区边界会终止 Agent 执行。"""

from types import SimpleNamespace

from app.core.agent import Agent
from app.core.task_result import TaskStatus
from app.runtime.workspace import WorkspaceContext
from app.tools.registry import ToolRegistry, ToolSpec


def _response(tool_calls=None, content=None):
    """构造模型响应。"""
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tool_calls))]
    )


def _call(name: str, arguments: str, call_id: str = "call"):
    """构造一个函数工具调用。"""
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def test_explicit_read_only_constraint_stops_before_file_creation(monkeypatch, tmp_path):
    """验证明确只读任务不会执行创建文件，且不会继续后续模型轮次。"""
    executions: list[str] = []
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            "create_file",
            "create",
            {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]},
            lambda path, content: executions.append(path),
        )
    )

    class FakeModel:
        calls = 0

        def chat(self, messages, tools):
            self.calls += 1
            return _response([_call("create_file", '{"path":"SECURITY_BOUNDARY_TEST_REPORT.txt","content":"bad"}')])

    monkeypatch.setattr("app.core.agent.ModelAdapter", FakeModel)
    agent = Agent(WorkspaceContext.from_path(tmp_path), registry=registry)
    result = agent.run("只读检查，禁止创建、修改、删除文件。")

    assert executions == []
    assert not (tmp_path / "SECURITY_BOUNDARY_TEST_REPORT.txt").exists()
    assert "明确禁止" in result
    assert agent.last_result is not None
    assert agent.last_result.status is TaskStatus.STOPPED


def test_workspace_boundary_failure_stops_immediately(monkeypatch, tmp_path):
    """验证越界读取失败后不会继续执行后续工具调用。"""
    executions: list[str] = []
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            "read_file",
            "read",
            {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
            lambda path: (_ for _ in ()).throw(ValueError("禁止访问工作目录之外的路径。")),
        )
    )

    class FakeModel:
        calls = 0

        def chat(self, messages, tools):
            self.calls += 1
            if self.calls == 1:
                return _response([_call("read_file", '{"path":"../../README.md"}')])
            return _response([_call("read_file", '{"path":"calculator.py"}')])

    monkeypatch.setattr("app.core.agent.ModelAdapter", FakeModel)
    agent = Agent(WorkspaceContext.from_path(tmp_path), registry=registry)
    result = agent.run("读取 ../../README.md")

    assert executions == []
    assert "工作目录之外" in result
    assert agent.last_result is not None
    assert agent.last_result.status is TaskStatus.STOPPED
