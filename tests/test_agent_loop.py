from types import SimpleNamespace

import pytest

from app.core.agent import Agent
from app.runtime.model import ModelAdapter, ModelFallbackExhaustedError
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


def _call(call_id, name="step"):
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments="{}"),
    )


def test_complex_task_uses_plan_and_verifies_before_finishing(monkeypatch, tmp_path):
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

    class FakeModel:
        def __init__(self):
            self.calls = 0

        def chat(self, messages, tools):
            self.calls += 1
            if self.calls <= 3:
                tool_name = ("read_file", "create_file", "run_tests")[
                    self.calls - 1
                ]
                return _response(
                    tool_calls=[_call(f"call-{self.calls}", tool_name)]
                )
            return _response(content="任务已完成")

    monkeypatch.setattr("app.core.agent.ModelAdapter", FakeModel)
    agent = Agent(WorkspaceContext.from_path(tmp_path), registry=registry)

    assert agent.run("读取配置，然后修改代码，然后运行测试") == "任务已完成"
    assert agent.last_plan is not None
    assert all(step.status.value == "completed" for step in agent.last_plan.steps)
    assert agent.last_context is not None
    # 测试工具结果进入 test_results，普通工具结果进入 tool_results。
    assert len(agent.last_context.context.tool_results) == 2
    assert len(agent.last_context.context.test_results) == 1


class FakeModelUnavailableError(RuntimeError):
    """测试使用的带 HTTP 状态码模型不可用错误。"""

    def __init__(self, message: str, status_code: int = 429) -> None:
        """保存模拟提供商返回的状态码。"""
        super().__init__(message)
        self.status_code = status_code


class FakeCompletions:
    """按顺序返回响应或抛出异常，并记录请求模型。"""

    def __init__(self, outcomes):
        """初始化待消费的响应与异常序列。"""
        self.outcomes = list(outcomes)
        self.models = []

    def create(self, **kwargs):
        """记录模型并返回下一结果，异常对象会被直接抛出。"""
        self.models.append(kwargs["model"])
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _fake_client(outcomes):
    """构造符合 OpenAI SDK 属性结构的最小测试客户端。"""
    completions = FakeCompletions(outcomes)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return client, completions


def test_model_adapter_falls_back_and_keeps_successful_model():
    """验证额度错误会顺序切换，后续请求沿用已成功模型。"""
    first_success = object()
    second_success = object()
    client, completions = _fake_client(
        [FakeModelUnavailableError("quota exhausted"), first_success, second_success]
    )
    adapter = ModelAdapter(client=client, models=("model-1", "model-2", "model-3"))

    assert adapter.chat([{"role": "user", "content": "test"}]) is first_success
    assert adapter.model == "model-2"
    assert adapter.chat([{"role": "user", "content": "again"}]) is second_success
    assert completions.models == ["model-1", "model-2", "model-2"]


def test_model_adapter_does_not_switch_for_unrelated_error():
    """验证参数等普通错误直接上抛，不错误消耗后续模型。"""
    client, completions = _fake_client([ValueError("invalid tool schema")])
    adapter = ModelAdapter(client=client, models=("model-1", "model-2"))

    with pytest.raises(ValueError, match="invalid tool schema"):
        adapter.chat([{"role": "user", "content": "test"}])

    assert completions.models == ["model-1"]
    assert adapter.model == "model-1"


def test_model_adapter_reports_when_all_models_are_unavailable():
    """验证候选全部耗尽后返回明确且不含密钥的统一错误。"""
    client, completions = _fake_client(
        [
            FakeModelUnavailableError("quota exhausted"),
            FakeModelUnavailableError("model not found", status_code=404),
        ]
    )
    adapter = ModelAdapter(client=client, models=("model-1", "model-2"))

    with pytest.raises(ModelFallbackExhaustedError, match="model-1, model-2"):
        adapter.chat([{"role": "user", "content": "test"}])

    assert completions.models == ["model-1", "model-2"]
