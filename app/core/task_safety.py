"""Agent Loop 使用的任务变更会话与恢复信号收集。"""

from typing import Any

from app.core.completion import CompletionStatus
from app.core.recovery import (
    RecoveryAction,
    RecoveryDecision,
    RecoveryPolicy,
    RecoverySignals,
)
from app.runtime.workspace import WorkspaceContext
from app.tools.change_set import ChangeSetError, TaskChangeSet
from app.tools.result import ToolResult, ToolStatus
from app.tools.rollback import RollbackStatus

FILE_MUTATION_TOOLS = {"create_file", "edit_file", "delete_file"}


class TaskConstraintViolation(PermissionError):
    """用户任务声明的操作约束被工具调用违反。"""


class TaskSafetySession:
    """连接写入前快照、变更集与失败恢复策略。"""

    def __init__(
        self,
        workspace: WorkspaceContext,
        policy: RecoveryPolicy | None = None,
        task: str = "",
    ) -> None:
        self.workspace = workspace
        self.task = task
        self.mutations_forbidden = self._detect_mutation_prohibition(task)
        self.validation_only = self._detect_validation_task(task)
        self.policy = policy or RecoveryPolicy()
        self.change_set = TaskChangeSet(workspace)
        self.consecutive_tool_failures = 0
        self.recovery_attempts = 0
        self.test_repair_attempts = 0
        self.test_runs = 0
        self.mutation_generation = 0
        self.last_test_generation: int | None = None

    @staticmethod
    def _detect_mutation_prohibition(task: str) -> bool:
        """识别用户明确禁止创建、修改或删除文件的任务约束。"""
        lowered = task.lower().replace(" ", "")
        markers = (
            "禁止创建、修改、删除文件",
            "禁止创建修改删除文件",
            "不要创建、修改、删除文件",
            "不要创建修改删除文件",
            "不得创建、修改、删除文件",
            "只读",
            "仅允许读取",
        )
        return any(marker in lowered for marker in markers)

    @staticmethod
    def _detect_validation_task(task: str) -> bool:
        """识别只需验证一次结果、失败后不应继续探索的任务。"""
        markers = (
            "安全边界", "完成状态", "验收", "越界", "只尝试一次",
            "仅验证", "仅检查", "验证是否", "检查是否",
        )
        return any(marker in task for marker in markers)

    def validate_tool(self, tool_name: str, arguments: dict[str, Any]) -> None:
        """在执行工具前 enforcing 用户任务级安全约束。"""
        if self.mutations_forbidden and tool_name in FILE_MUTATION_TOOLS | {"delete_directory"}:
            raise TaskConstraintViolation(
                "用户任务明确禁止创建、修改或删除文件，已拒绝该工具调用。"
            )

    def prepare_tool(self, tool_name: str, arguments: dict[str, Any]) -> tuple[str, ...]:
        """在写入型工具执行前捕获所有可能受影响的普通文件。"""

        paths: list[str] = []
        if tool_name in FILE_MUTATION_TOOLS:
            self.mutation_generation += 1
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
        test_error_kind = None
        test_failed = False
        if tool_name == "run_tests" and result.status is ToolStatus.FAILURE:
            self.test_runs += 1
            message = result.error or ""
            if "测试错误类型：收集冲突" in message:
                test_error_kind = "collection"
            elif "测试错误类型：环境错误" in message:
                test_error_kind = "environment"
            elif "测试错误类型：测试代码" in message:
                test_error_kind = "test_code"
            else:
                test_failed = True
            # 第一次运行测试只是建立基线，不算自动修复；只有修改后再次运行
            # 才增加修复尝试次数。
            if (
                self.last_test_generation is not None
                and self.mutation_generation > self.last_test_generation
            ):
                self.test_repair_attempts += 1
            self.last_test_generation = self.mutation_generation
        return self.policy.decide(
            RecoverySignals(
                consecutive_tool_failures=self.consecutive_tool_failures,
                test_failed=test_failed,
                test_error_kind=test_error_kind,
                test_repair_attempts=self.test_repair_attempts,
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
