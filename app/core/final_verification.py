"""任务结束前统一核验磁盘、Git、测试结果和 Trace 统计。"""

from dataclasses import dataclass
from typing import Any

from app.core.task_result import (
    FinalVerificationSummary,
    TestEvidence,
    TestEvidenceStatus,
    ToolCallSummary,
)
from app.core.test_evidence import summarize_test_evidence
from app.runtime.trace import ExecutionTrace
from app.runtime.workspace import WorkspaceContext
from app.tools.change_set import ChangeSetSummary, TaskChangeSet
from app.tools.git_repository import GitWorkingTreeState, inspect_git_worktree


@dataclass(frozen=True)
class FinalStateEvidence:
    """一次最终核验得到的原始结构化证据。"""

    changes: ChangeSetSummary
    tests: tuple[TestEvidence, ...]
    git: GitWorkingTreeState
    model_request_count: int
    tool_calls: ToolCallSummary
    trace_event_count: int
    summary: FinalVerificationSummary


class FinalStateVerifier:
    """从真实运行状态生成报告唯一允许使用的最终证据。"""

    def verify(
        self,
        workspace: WorkspaceContext,
        change_set: TaskChangeSet,
        trace: ExecutionTrace,
    ) -> FinalStateEvidence:
        """重新读取文件内容摘要、Git 状态、测试事件和调用统计。"""
        changes = change_set.summary()
        git_state = inspect_git_worktree(workspace)
        tests = self._tests_from_trace(trace)
        statistics = trace.get_statistics()
        tool_calls = self._tool_calls(statistics.get("tool_calls", {}))
        verified = tool_calls.unfinished == 0
        trace.record(
            "final_state_verified",
            verified=verified,
            comparison_basis="task_initial_state",
            verified_path_count=changes.touched_count,
            net_changed_count=changes.net_changed_count,
            git=git_state.to_dict(),
            test_evidence_count=len(tests),
            model_request_count=statistics.get("model_requests", 0),
            tool_calls=tool_calls.to_dict(),
            evidence_sources=("file_content_hashes", "git_status", "test_trace", "execution_trace"),
        )
        final_statistics = trace.get_statistics()
        summary = FinalVerificationSummary(
            verified=verified,
            verified_path_count=changes.touched_count,
            net_changed_count=changes.net_changed_count,
            git_status=git_state.status.value,
            git_dirty=git_state.dirty,
            git_changed_count=git_state.changed_count,
            test_evidence_count=len(tests),
            evidence_sources=(
                "file_content_hashes",
                "git_status",
                "test_trace",
                "execution_trace",
            ),
        )
        return FinalStateEvidence(
            changes=changes,
            tests=tests,
            git=git_state,
            model_request_count=int(final_statistics.get("model_requests", 0)),
            tool_calls=tool_calls,
            trace_event_count=int(final_statistics.get("total_events", 0)),
            summary=summary,
        )

    @staticmethod
    def _tool_calls(raw: Any) -> ToolCallSummary:
        """将 Trace 的工具统计复制为不可变报告证据。"""
        values = raw if isinstance(raw, dict) else {}
        return ToolCallSummary(
            calls_started=int(values.get("calls_started", 0)),
            calls_finished=int(values.get("calls_finished", 0)),
            succeeded=int(values.get("succeeded", 0)),
            failed=int(values.get("failed", 0)),
            cancelled=int(values.get("cancelled", 0)),
            unfinished=int(values.get("unfinished", 0)),
        )

    @staticmethod
    def _tests_from_trace(trace: ExecutionTrace) -> tuple[TestEvidence, ...]:
        """仅从真实 run_tests 调用完成事件恢复测试证据。"""
        started_targets: dict[str, str] = {}
        evidence: list[TestEvidence] = []
        for event in trace.get_events():
            details = event.get("details", {})
            if event.get("event") == "tool_call_started" and details.get("tool") == "run_tests":
                arguments = details.get("arguments", {})
                target = arguments.get("test_path", ".") if isinstance(arguments, dict) else "."
                started_targets[str(details.get("tool_call_id", ""))] = str(target)
                continue
            if event.get("event") != "tool_call_finished" or details.get("tool") != "run_tests":
                continue
            call_id = str(details.get("tool_call_id", ""))
            target = started_targets.get(call_id, ".")
            status = str(details.get("status", ""))
            if status == "cancelled":
                evidence.append(
                    TestEvidence(
                        target=target,
                        status=TestEvidenceStatus.NOT_RUN,
                        output=str(details.get("result") or details.get("error") or "测试已取消"),
                    )
                )
                continue
            raw = str(details.get("result") or details.get("error") or "")
            parsed = summarize_test_evidence((raw,))
            if not parsed:
                continue
            item = parsed[0]
            if item.target == "未标识测试":
                item = TestEvidence(
                    target=target,
                    status=item.status,
                    exit_code=item.exit_code,
                    error_kind=item.error_kind,
                    output=item.output,
                )
            evidence.append(item)
        return tuple(evidence)
