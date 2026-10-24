"""覆盖结构化测试结果模型及其在判定链路上的使用。"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.agent import Agent
from app.core.completion import CompletionStatus, verify_completion
from app.core.final_verification import FinalStateVerifier
from app.core.planner import create_task_plan
from app.core.task_result import TestEvidenceStatus
from app.core.task_state import PlanStateManager
from app.core.test_evidence import summarize_test_evidence
from app.runtime.trace import ExecutionTrace
from app.runtime.workspace import WorkspaceContext
from app.tools.registry import ToolRegistry, ToolSpec
from app.tools.result import ToolResult
from app.tools.test_result import TestRunResult, TestRunStatus

EMPTY_PARAMETERS = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}

PASSED_TEXT = "测试目标：tests\n测试退出码：0\n测试结果：通过\n1 passed"


def _passed_run(target: str = "tests", output: str = "1 passed") -> TestRunResult:
    """构造一次通过的测试结果。"""
    return TestRunResult(
        target=target,
        exit_code=0,
        status=TestRunStatus.PASSED,
        output=output,
    )


def _failed_run(target: str = "tests") -> TestRunResult:
    """构造一次失败的测试结果。"""
    return TestRunResult(
        target=target,
        exit_code=1,
        status=TestRunStatus.FAILED,
        error_kind="断言失败",
        output="assert 1 == 2",
    )


def test_test_run_result_roundtrips_through_dict():
    """验证结构化结果可稳定序列化与恢复。"""
    run = _failed_run()

    payload = run.to_dict()
    restored = TestRunResult.from_dict(payload)

    assert restored == run
    assert payload["status"] == "failed"
    assert payload["error_kind"] == "断言失败"


def test_passed_run_requires_zero_exit_code():
    """验证通过结论不允许与退出码矛盾。"""
    with pytest.raises(ValueError, match="退出码"):
        TestRunResult(target="tests", exit_code=1, status=TestRunStatus.PASSED)


def test_empty_target_is_rejected():
    """验证测试目标不能为空。"""
    with pytest.raises(ValueError, match="测试目标"):
        TestRunResult(target=" ", exit_code=0, status=TestRunStatus.PASSED)


def test_summary_accepts_structured_result_without_parsing():
    """验证摘要器直接读取结构化结果，不依赖文本字段。"""
    evidence = summarize_test_evidence((_passed_run(),))

    assert evidence[0].status is TestEvidenceStatus.PASSED
    assert evidence[0].target == "tests"
    assert evidence[0].exit_code == 0


def test_completion_reads_structured_failure_without_text_keywords():
    """验证失败结论来自结构化状态，而不是输出里的关键词。"""
    plan = create_task_plan("修改代码")
    manager = PlanStateManager(plan)
    manager.start_step(1)
    manager.complete_step(1)

    decision = verify_completion(
        manager.state().plan,
        [ToolResult.success("已执行")],
        [_failed_run()],
    )

    assert decision.status is CompletionStatus.BLOCKED


def test_completion_accepts_structured_passing_result():
    """验证结构化通过与工具证据可以共同完成判定。"""
    plan = create_task_plan("修改代码")
    manager = PlanStateManager(plan)
    manager.start_step(1)
    manager.complete_step(1)

    decision = verify_completion(
        manager.state().plan,
        [ToolResult.success("已执行")],
        [_passed_run()],
    )

    assert decision.status is CompletionStatus.COMPLETE


def test_trace_payload_is_used_before_text_fallback():
    """验证最终核验优先读取 Trace 中的结构化测试载荷。"""
    trace = ExecutionTrace()
    trace.record(
        "tool_call_started",
        tool="run_tests",
        tool_call_id="call-1",
        arguments={"test_path": "tests"},
    )
    trace.record(
        "tool_call_finished",
        tool="run_tests",
        tool_call_id="call-1",
        status="success",
        result=PASSED_TEXT,
        test=_passed_run().to_dict(),
    )

    evidence = FinalStateVerifier._tests_from_trace(trace)

    assert evidence[0].status is TestEvidenceStatus.PASSED
    assert evidence[0].target == "tests"


def test_trace_without_payload_falls_back_to_text():
    """验证缺少结构化载荷的旧 Trace 仍可按文本恢复证据。"""
    trace = ExecutionTrace()
    trace.record(
        "tool_call_started",
        tool="run_tests",
        tool_call_id="call-2",
        arguments={"test_path": "tests"},
    )
    trace.record(
        "tool_call_finished",
        tool="run_tests",
        tool_call_id="call-2",
        status="success",
        result=PASSED_TEXT,
    )

    evidence = FinalStateVerifier._tests_from_trace(trace)

    assert evidence[0].status is TestEvidenceStatus.PASSED


def test_agent_keeps_structured_result_in_context_and_trace(monkeypatch, tmp_path: Path):
    """验证 Agent 把 run_tests 的结构化结果写入上下文与 Trace。"""
    registry = ToolRegistry()
    for tool_name in ("read_file", "create_file"):
        registry.register(
            ToolSpec(tool_name, f"执行 {tool_name}", EMPTY_PARAMETERS, lambda: "ok")
        )

    def run_tests_stub() -> ToolResult:
        """返回带结构化载荷的测试结果。"""
        return ToolResult.success(PASSED_TEXT, data=_passed_run())

    registry.register(
        ToolSpec("run_tests", "运行测试", EMPTY_PARAMETERS, run_tests_stub)
    )

    def _response(content=None, tool_calls=None):
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=content, tool_calls=tool_calls)
                )
            ]
        )

    class FakeModel:
        calls = 0

        def chat(self, messages, tools):
            self.calls += 1
            if self.calls <= 3:
                name = ("read_file", "create_file", "run_tests")[self.calls - 1]
                return _response(
                    tool_calls=[
                        SimpleNamespace(
                            id=f"call-{self.calls}",
                            function=SimpleNamespace(name=name, arguments="{}"),
                        )
                    ]
                )
            return _response(content="已完成")

    monkeypatch.setattr("app.core.agent.ModelAdapter", FakeModel)
    agent = Agent(WorkspaceContext.from_path(tmp_path), registry=registry)

    assert agent.run("读取配置，然后修改代码，然后运行测试") == "已完成"

    stored = agent.last_context.context.test_results
    assert len(stored) == 1
    assert isinstance(stored[0], TestRunResult)

    finished = [
        event
        for event in agent.last_trace.get_events()
        if event["event"] == "tool_call_finished" and event["details"].get("tool") == "run_tests"
    ]
    assert finished[0]["details"]["test"]["status"] == "passed"
    assert agent.last_result is not None
    assert agent.last_result.tests[0].status is TestEvidenceStatus.PASSED
