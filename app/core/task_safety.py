"""Agent Loop 使用的任务约束、变更会话和重复写入控制。"""

import json
from pathlib import PurePosixPath
from typing import Any

from app.core.completion import CompletionStatus
from app.core.recovery import (
    RecoveryAction,
    RecoveryDecision,
    RecoveryPolicy,
    RecoverySignals,
)
from app.core.test_evidence import test_target_covers
from app.runtime.workspace import WorkspaceContext
from app.tools.change_set import ChangeSetError, TaskChangeSet
from app.tools.result import ToolResult, ToolStatus
from app.tools.rollback import RollbackStatus

FILE_MUTATION_TOOLS = {"create_file", "edit_file", "delete_file"}
MUTATION_TOOLS = FILE_MUTATION_TOOLS | {"create_directory", "delete_directory"}


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
        """为单次任务初始化约束、变更跟踪和恢复状态。"""
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
        self.last_failed_test_key: str | None = None
        self.last_failed_test_generation: int | None = None
        self.last_failed_test_error: str | None = None
        self._mutation_paths_since_test: set[str] = set()
        self._completed_mutations: dict[str, ToolResult] = {}

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
            "安全边界",
            "完成状态",
            "验收",
            "越界",
            "只尝试一次",
            "仅验证",
            "仅检查",
            "验证是否",
            "检查是否",
        )
        return any(marker in task for marker in markers)

    def validate_tool(self, tool_name: str, arguments: dict[str, Any]) -> None:
        """在执行工具前 enforcing 用户任务级安全约束。"""
        if self.mutations_forbidden and tool_name in MUTATION_TOOLS:
            raise TaskConstraintViolation(
                "用户任务明确禁止创建、修改或删除文件，已拒绝该工具调用。"
            )

    def test_rerun_block_reason(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> str | None:
        """在同一失败测试没有实际代码变更时拒绝原样重跑。"""
        if tool_name != "run_tests" or self.last_failed_test_key is None:
            return None
        if self._test_key(arguments) != self.last_failed_test_key:
            return None
        if self.last_failed_test_generation is None:
            return None
        if self.mutation_generation > self.last_failed_test_generation:
            return None
        return (
            "同一测试上次失败后没有检测到新的文件修改，已拒绝无效重跑。"
            "请先根据失败证据修改相关代码；若测试与用户约束冲突或无法安全修复，"
            "请明确结束为未完成并报告真实失败原因。"
        )

    @staticmethod
    def _test_key(arguments: dict[str, Any]) -> str:
        """将测试参数规范化为稳定键，用于识别同一测试目标。"""
        return json.dumps(arguments, ensure_ascii=False, sort_keys=True)

    def record_mutation_applied(self, path: str | None = None) -> None:
        """记录真实落盘修改及其路径，供测试重跑和修复预算判定。"""
        self.mutation_generation += 1
        self._mutation_paths_since_test.add(path or "<unknown>")

    @staticmethod
    def _is_test_artifact(path: str) -> bool:
        """判断修改路径是否属于测试代码而非被测业务实现。"""
        normalized = path.replace("\\", "/")
        parts = PurePosixPath(normalized).parts
        name = parts[-1] if parts else normalized
        return "tests" in parts or name.startswith("test_")

    def _has_implementation_mutation_since_test(self) -> bool:
        """判断上次测试后是否至少修改过一个非测试实现文件。"""
        return any(
            not self._is_test_artifact(path)
            for path in self._mutation_paths_since_test
        )

    def unresolved_test_reason(self) -> str | None:
        """返回最新未解决测试失败的确定性停止原因。"""
        if self.last_failed_test_error is None:
            return None
        lines = [line.strip() for line in self.last_failed_test_error.splitlines()]
        error_kind = next(
            (
                line.removeprefix("测试错误类型：")
                for line in lines
                if line.startswith("测试错误类型：")
            ),
            "未分类错误",
        )
        failed_case = next(
            (line for line in lines if line.startswith("FAILED ")),
            None,
        )
        details = f"{error_kind}；{failed_case}" if failed_case else error_kind
        return (
            f"最新回归测试仍然失败（{details}），任务未完成；"
            "已保留当前有效代码修改和完整测试证据。"
        )

    def completed_mutation(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> ToolResult | None:
        """核对当前文件状态后返回已成功的相同写操作结果。"""
        key = self._mutation_key(tool_name, arguments)
        if key is None or key not in self._completed_mutations:
            return None
        if self._mutation_still_applied(tool_name, arguments):
            return self._completed_mutations[key]
        self._completed_mutations.pop(key, None)
        return None

    def record_completed_mutation(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        result: ToolResult,
    ) -> bool:
        """记录成功写操作，并返回是否代表实际应用了一次修改。"""
        if result.status is not ToolStatus.SUCCESS:
            return False
        key = self._mutation_key(tool_name, arguments)
        if key is not None:
            self._completed_mutations[key] = result
        return key is not None and not (result.result or "").startswith("无需修改")

    @staticmethod
    def _mutation_key(tool_name: str, arguments: dict[str, Any]) -> str | None:
        """为写工具及其规范参数生成稳定的去重键。"""
        if tool_name not in MUTATION_TOOLS:
            return None
        return (
            f"{tool_name}:{json.dumps(arguments, ensure_ascii=False, sort_keys=True)}"
        )

    def _mutation_still_applied(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> bool:
        """检查磁盘当前状态是否仍满足此前成功写操作的目标。"""
        path = arguments.get("path")
        if not isinstance(path, str):
            return False
        try:
            target = self.workspace.resolve(path)
            if tool_name == "create_directory":
                return target.is_dir() and not target.is_symlink()
            if tool_name == "create_file":
                return (
                    target.is_file()
                    and not target.is_symlink()
                    and target.read_text(encoding="utf-8") == arguments.get("content")
                )
            if tool_name == "edit_file":
                if not target.is_file() or target.is_symlink():
                    return False
                content = target.read_text(encoding="utf-8")
                old_text = arguments.get("old_text")
                new_text = arguments.get("new_text")
                return (
                    isinstance(old_text, str)
                    and isinstance(new_text, str)
                    and bool(new_text)
                    and old_text not in content
                    and new_text in content
                )
            if tool_name in {"delete_file", "delete_directory"}:
                return not target.exists() and not target.is_symlink()
        except (OSError, UnicodeError, ValueError):
            return False
        return False

    def prepare_tool(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> tuple[str, ...]:
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
        arguments: dict[str, Any] | None = None,
    ) -> RecoveryDecision:
        """结合工具结果、净变化和计划状态生成恢复决策。"""
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
        if tool_name == "run_tests":
            self.test_runs += 1
            # 首次运行只建立失败基线；之后每次“修改代码后重跑”都算一次
            # 自动修复尝试，无论重跑最终通过还是失败。
            if (
                self.last_test_generation is not None
                and self.mutation_generation > self.last_test_generation
                and self.last_failed_test_error is not None
                and self._has_implementation_mutation_since_test()
            ):
                self.test_repair_attempts += 1
            self.last_test_generation = self.mutation_generation
            self._mutation_paths_since_test.clear()

            test_key = self._test_key(arguments or {})
            if result.status is ToolStatus.FAILURE:
                self.last_failed_test_key = test_key
                self.last_failed_test_generation = self.mutation_generation
                self.last_failed_test_error = result.error or "测试执行失败"
            elif (
                result.status is ToolStatus.SUCCESS
                and self.last_failed_test_key is not None
                and test_target_covers(
                    self._test_target(arguments or {}),
                    self._test_target_from_key(self.last_failed_test_key),
                )
            ):
                self.last_failed_test_key = None
                self.last_failed_test_generation = None
                self.last_failed_test_error = None

        if tool_name == "run_tests" and result.status is ToolStatus.FAILURE:
            message = result.error or ""
            if "测试错误类型：收集冲突" in message:
                test_error_kind = "collection"
            elif "测试错误类型：环境错误" in message:
                test_error_kind = "environment"
            elif "测试错误类型：代码导入错误" in message:
                test_error_kind = "import"
            elif "测试错误类型：测试代码" in message:
                test_error_kind = "test_code"
            else:
                test_failed = True
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

    @staticmethod
    def _test_target(arguments: dict[str, Any]) -> str:
        """从测试工具参数提取用于父子覆盖判断的目标。"""
        target = arguments.get("test_path", ".")
        return target if isinstance(target, str) else "."

    @staticmethod
    def _test_target_from_key(key: str) -> str:
        """从规范化测试键恢复测试目标。"""
        try:
            arguments = json.loads(key)
        except (json.JSONDecodeError, TypeError):
            return "."
        return TaskSafetySession._test_target(arguments)

    def observe_completion(self, status: CompletionStatus) -> RecoveryDecision:
        """结合完成判定与当前净变化生成任务结束阶段的恢复决策。"""
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
                recoverable_paths=tuple(change.path for change in summary.changes),
                recovery_attempts=self.recovery_attempts,
            )
        )

    def reset_after_recovery(self, rollback_status: RollbackStatus) -> None:
        """仅在回滚成功后清空本轮变更状态并开始新的恢复轮次。"""
        if rollback_status is not RollbackStatus.RESTORED:
            raise ValueError("只有确认回滚成功后才能重置任务变更会话。")
        self.recovery_attempts += 1
        self.consecutive_tool_failures = 0
        self.change_set = TaskChangeSet(self.workspace)
        self._mutation_paths_since_test.clear()
        self._completed_mutations.clear()
