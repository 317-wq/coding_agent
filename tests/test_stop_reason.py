"""覆盖停止原因的兜底顺序与待处理步骤编号。"""

from app.core.completion import CompletionDecision, CompletionStatus
from app.core.result_aggregator import TaskResultAggregator
from app.core.task_result import TaskStatus
from app.runtime.trace import ExecutionTrace


def test_stop_reason_falls_back_to_last_completion_check():
    """没有终止事件时，使用最近一次完成判定的原因。"""
    trace = ExecutionTrace()
    trace.record(
        "completion_checked",
        status="incomplete",
        reason="仍有必要计划步骤未完成。",
        pending_step_ids=[2],
    )

    reason = TaskResultAggregator._stop_reason(None, trace, TaskStatus.STOPPED)

    assert reason == "仍有必要计划步骤未完成。（待处理步骤：2）"


def test_stop_reason_prefers_terminal_event():
    """有终止事件时，终止事件优先于完成判定。"""
    trace = ExecutionTrace()
    trace.record(
        "completion_checked",
        status="incomplete",
        reason="仍有必要计划步骤未完成。",
        pending_step_ids=[2],
    )
    trace.record("task_stopped", reason="回滚未获授权：用户拒绝；任务已停止。")

    reason = TaskResultAggregator._stop_reason(None, trace, TaskStatus.STOPPED)

    assert reason == "回滚未获授权：用户拒绝；任务已停止。"


def test_pending_steps_are_appended_for_incomplete_completion():
    """完成判定未完成时，原因后附待处理步骤编号。"""
    completion = CompletionDecision(
        status=CompletionStatus.INCOMPLETE,
        reason="仍有必要计划步骤未完成。",
        pending_step_ids=(2, 4),
    )

    reason = TaskResultAggregator._stop_reason(
        completion, None, TaskStatus.STOPPED
    )

    assert reason == "仍有必要计划步骤未完成。（待处理步骤：2、4）"


def test_pending_steps_are_not_appended_when_blocked():
    """阻塞原因不追加步骤编号。"""
    completion = CompletionDecision(
        status=CompletionStatus.BLOCKED,
        reason="存在失败或取消的工具结果。",
        pending_step_ids=(2,),
    )

    reason = TaskResultAggregator._stop_reason(
        completion, None, TaskStatus.BLOCKED
    )

    assert reason == "存在失败或取消的工具结果。"
