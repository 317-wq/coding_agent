"""Agent Loop 使用的任务变更会话与恢复信号收集。"""

from typing import Any

from app.change_set import ChangeSetError, TaskChangeSet
from app.completion import CompletionStatus
from app.recovery import (
    RecoveryAction,
    RecoveryDecision,
    RecoveryPolicy,
    RecoverySignals,
)
from app.result import ToolResult, ToolStatus
from app.rollback import RollbackStatus
from app.workspace import WorkspaceContext

FILE_MUTATION_TOOLS = {"create_file", "edit_file", "delete_file"}


class TaskSafetySession:
    """连接写入前快照、变更集与失败恢复策略。"""

    def __init__(
        self,
        workspace: WorkspaceContext,
        policy: RecoveryPolicy | None = None,
    ) -> None:
        self.workspace = workspace
        self.policy = policy or RecoveryPolicy()
        self.change_set = TaskChangeSet(workspace)
        self.consecutive_tool_failures = 0
        self.recovery_attempts = 0

    def prepare_tool(self, tool_name: str, arguments: dict[str, Any]) -> tuple[str, ...]:
        """在写入型工具执行前捕获所有可能受影响的普通文件。"""

        paths: list[str] = []
        if tool_name in FILE_MUTATION_TOOLS:
            path = arguments.get("path")
            if isinstance(path, str):
                self.change_set.touch(path)
                paths.append(self.change_set.snapshot_store.get(path).path)
        elif tool_name == "delete_directory":
            path = arguments.get("path")
            if isinstance(path, str):
                directory = self.workspace.resolve(path)
                if directory.is_dir() and not directory.is_symlink():
                    for child in sorted(directory.rglob("*")):
                        if child.is_symlink():
                            continue
                        if child.is_file():
                            relative = child.relative_to(self.workspace.root).as_posix()
                            self.change_set.touch(relative)
                            paths.append(relative)
        return tuple(paths)

    def observe(
        self,
        tool_name: str,
        result: ToolResult,
        *,
        plan_failed: bool = False,
    ) -> RecoveryDecision:
        if result.status is ToolStatus.FAILURE:
            self.consecutive_tool_failures += 1
        elif result.status is ToolStatus.SUCCESS:
            self.consecutive_tool_failures = 0

        try:
            summary = self.change_set.summary()
        except ChangeSetError as exc:
            return RecoveryDecision(
                action=RecoveryAction.STOP,
                reason=f"无法安全分析任务变更：{exc}",
                triggers=("变更集异常",),
            )
        recoverable_paths = tuple(change.path for change in summary.changes)
        return self.policy.decide(
            RecoverySignals(
                consecutive_tool_failures=self.consecutive_tool_failures,
                test_failed=(
                    tool_name == "run_tests"
                    and result.status is ToolStatus.FAILURE
                ),
                plan_failed=plan_failed,
                recoverable_paths=recoverable_paths,
                recovery_attempts=self.recovery_attempts,
            )
        )

    def observe_completion(
        self, status: CompletionStatus
    ) -> RecoveryDecision:
        try:
            summary = self.change_set.summary()
        except ChangeSetError as exc:
            return RecoveryDecision(
                action=RecoveryAction.STOP,
                reason=f"无法安全分析任务变更：{exc}",
                triggers=("变更集异常",),
            )
        return self.policy.decide(
            RecoverySignals(
                completion_status=status,
                recoverable_paths=tuple(
                    change.path for change in summary.changes
                ),
                recovery_attempts=self.recovery_attempts,
            )
        )

    def reset_after_recovery(self, rollback_status: RollbackStatus) -> None:
        if rollback_status is not RollbackStatus.RESTORED:
            raise ValueError("只有确认回滚成功后才能重置任务变更会话。")
        self.recovery_attempts += 1
        self.consecutive_tool_failures = 0
        self.change_set = TaskChangeSet(self.workspace)
