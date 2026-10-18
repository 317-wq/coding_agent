"""为未完成任务生成基于结构化证据的下一步建议。"""

from __future__ import annotations

from collections.abc import Sequence

from app.core.task_result import (
    ChangeSummary,
    TaskStatus,
    TestEvidence,
    TestEvidenceStatus,
)


class TaskSuggestionBuilder:
    """根据任务状态、测试证据和未恢复变更生成可执行建议。"""

    def build(
        self,
        status: TaskStatus,
        stop_reason: str | None,
        changes: ChangeSummary,
        tests: Sequence[TestEvidence],
    ) -> tuple[str, ...]:
        """为阻塞、停止或失败任务生成去重后的下一步建议。"""
        if status is TaskStatus.COMPLETED:
            return ()
        suggestions: list[str] = []
        failed = [item for item in tests if item.status is TestEvidenceStatus.FAILED]
        not_run = [item for item in tests if item.status is TestEvidenceStatus.NOT_RUN]
        if failed:
            suggestions.append("修复失败测试后重新运行指定测试。")
        if not_run:
            suggestions.append("先运行未执行的指定测试并记录退出码。")
        if changes.unresolved:
            suggestions.append("确认是否保留未恢复文件，或授权回滚这些路径。")
        if status is TaskStatus.BLOCKED:
            suggestions.append("处理阻塞原因后重新执行任务。")
        elif status is TaskStatus.FAILED:
            suggestions.append("检查失败原因和 Trace 事件，再决定是否重试。")
        elif status is TaskStatus.STOPPED:
            suggestions.append("补充必要操作或用户授权后继续任务。")
        if stop_reason and not suggestions:
            suggestions.append(f"根据未完成原因处理：{stop_reason}")
        return tuple(dict.fromkeys(suggestions))


def build_task_suggestions(
    status: TaskStatus,
    stop_reason: str | None,
    changes: ChangeSummary,
    tests: Sequence[TestEvidence],
) -> tuple[str, ...]:
    """使用默认建议构建器生成下一步建议。"""
    return TaskSuggestionBuilder().build(status, stop_reason, changes, tests)
