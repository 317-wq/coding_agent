"""最终状态核验器的磁盘、Git、测试和 Trace 证据测试。"""

import subprocess
from pathlib import Path

from app.core.final_verification import FinalStateVerifier
from app.core.task_result import TestEvidenceStatus as EvidenceStatus
from app.runtime.trace import ExecutionTrace
from app.runtime.workspace import WorkspaceContext
from app.tools.change_set import TaskChangeSet


def _git_init(path: Path) -> None:
    """初始化供最终 Git 状态核验使用的临时仓库。"""
    subprocess.run(
        ["git", "init", "--initial-branch=main", str(path)],
        capture_output=True,
        check=True,
        text=True,
    )


def test_final_verifier_rechecks_all_runtime_evidence(tmp_path: Path):
    """验证最终核验只使用当前磁盘和真实 Trace 生成统一证据。"""
    _git_init(tmp_path)
    target = tmp_path / "module.py"
    target.write_text("before", encoding="utf-8")
    workspace = WorkspaceContext.from_path(tmp_path)
    changes = TaskChangeSet(workspace)
    changes.touch("module.py")
    target.write_text("after", encoding="utf-8")
    trace = ExecutionTrace()
    trace.record("task_started", workspace=str(tmp_path))
    trace.record("model_request_started", step=1)
    trace.record(
        "tool_call_started",
        tool="run_tests",
        tool_call_id="test-1",
        arguments={"test_path": "tests/test_module.py"},
    )
    trace.record(
        "tool_call_finished",
        tool="run_tests",
        tool_call_id="test-1",
        status="success",
        result=(
            "测试目标：tests/test_module.py\n"
            "测试退出码：0\n"
            "测试结果：通过"
        ),
        error=None,
    )
    trace.record("task_completed", answer="模型声称没有修改，也没有调用工具")

    evidence = FinalStateVerifier().verify(workspace, changes, trace)

    assert evidence.changes.modified_paths == ("module.py",)
    assert evidence.tests[0].target == "tests/test_module.py"
    assert evidence.tests[0].status is EvidenceStatus.PASSED
    assert evidence.git.dirty is True
    assert evidence.git.changed_count == 1
    assert evidence.model_request_count == 1
    assert evidence.tool_calls.calls_started == 1
    assert evidence.tool_calls.calls_finished == 1
    assert evidence.trace_event_count == len(trace.events)
    assert evidence.summary.verified is True
    assert trace.events[-1]["event"] == "final_state_verified"


def test_final_verifier_respects_failed_tool_status_without_exit_code(
    tmp_path: Path,
):
    """验证 Trace 中失败的测试调用不会因无退出码被报告为通过。"""
    _git_init(tmp_path)
    workspace = WorkspaceContext.from_path(tmp_path)
    changes = TaskChangeSet(workspace)
    trace = ExecutionTrace()
    trace.record("task_started", workspace=str(tmp_path))
    trace.record(
        "tool_call_started",
        tool="run_tests",
        tool_call_id="missing-test",
        arguments={"test_path": "tests/missing.py"},
    )
    trace.record(
        "tool_call_finished",
        tool="run_tests",
        tool_call_id="missing-test",
        status="failure",
        result=None,
        error="测试路径不存在：tests/missing.py",
    )

    evidence = FinalStateVerifier().verify(workspace, changes, trace)

    assert evidence.tests[0].status is EvidenceStatus.FAILED


def test_final_verifier_keeps_only_latest_result_for_same_test_target(tmp_path: Path):
    """验证最终报告不会重复展示同一测试目标的历史失败。"""
    workspace = WorkspaceContext.from_path(tmp_path)
    changes = TaskChangeSet(workspace)
    trace = ExecutionTrace()
    trace.record("task_started", workspace=str(tmp_path))
    for index, status in enumerate(("failure", "success"), start=1):
        trace.record(
            "tool_call_started",
            tool="run_tests",
            tool_call_id=f"test-{index}",
            arguments={"test_path": "."},
        )
        trace.record(
            "tool_call_finished",
            tool="run_tests",
            tool_call_id=f"test-{index}",
            status=status,
            result=(
                f"测试目标：.\n测试退出码：{1 if status == 'failure' else 0}\n"
                f"测试结果：{'失败' if status == 'failure' else '通过'}"
            ),
            error=None,
        )

    evidence = FinalStateVerifier().verify(workspace, changes, trace)

    assert len(evidence.tests) == 1
    assert evidence.tests[0].target == "."
    assert evidence.tests[0].status is EvidenceStatus.PASSED
