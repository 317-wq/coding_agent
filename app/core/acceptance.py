"""按验收项类型核验任务是否满足用户的明确要求。

每类证据只读它该读的事实：验收项不会因为模型说“已完成”而通过。
"""

from collections.abc import Sequence
from dataclasses import dataclass

from app.core.intent import AcceptanceItem, EvidenceKind, TaskIntent
from app.core.task_result import TestEvidence, TestEvidenceStatus
from app.core.test_evidence import latest_test_evidence, test_target_covers
from app.runtime.workspace import WorkspaceContext
from app.tools.change_set import ChangeSetSummary

IGNORED_DIRS = {".git", ".venv", ".pytest_cache", "__pycache__"}
MAX_SOURCE_SIZE = 1024 * 1024


@dataclass(frozen=True)
class AcceptanceResult:
    """一条验收项的核验结果。"""

    item: AcceptanceItem
    passed: bool
    detail: str = ""


class AcceptanceVerifier:
    """把验收项映射到变更集、测试证据与工作区内容。"""

    def verify(
        self,
        intent: TaskIntent,
        workspace: WorkspaceContext,
        changes: ChangeSetSummary,
        tests: Sequence[TestEvidence] = (),
        *,
        workspace_read: bool = False,
    ) -> tuple[AcceptanceResult, ...]:
        """按声明顺序核验所有必需验收项。"""
        evidence = latest_test_evidence(tests)
        return tuple(
            self._verify_item(item, workspace, changes, evidence, workspace_read)
            for item in intent.acceptance
            if item.required
        )

    def _verify_item(
        self,
        item: AcceptanceItem,
        workspace: WorkspaceContext,
        changes: ChangeSetSummary,
        tests: Sequence[TestEvidence],
        workspace_read: bool,
    ) -> AcceptanceResult:
        """按证据类型核验单条验收项。"""
        if item.kind is EvidenceKind.FILE_EXISTS:
            target = item.target
            if not target:
                return AcceptanceResult(item, False, "缺少目标路径")
            exists = (workspace.root / target).is_file()
            return AcceptanceResult(
                item, exists, f"{target} {'存在' if exists else '不存在'}"
            )

        if item.kind is EvidenceKind.FILE_CHANGED:
            target = item.target
            if target:
                paths = set(changes.created_paths) | set(changes.modified_paths)
                passed = target in paths
                return AcceptanceResult(
                    item, passed, f"{target} {'有变更' if passed else '没有变更'}"
                )
            passed = changes.net_changed_count > 0
            return AcceptanceResult(
                item, passed, f"净变化 {changes.net_changed_count} 个"
            )

        if item.kind is EvidenceKind.TEST_PASSED:
            if not tests:
                return AcceptanceResult(item, False, "没有测试证据")
            if item.target:
                matched = [
                    evidence
                    for evidence in tests
                    if evidence.target == item.target
                    or test_target_covers(evidence.target, item.target)
                ]
                if not matched:
                    return AcceptanceResult(
                        item, False, f"没有 {item.target} 的测试证据"
                    )
                passed = all(
                    evidence.status is TestEvidenceStatus.PASSED
                    for evidence in matched
                )
                return AcceptanceResult(
                    item,
                    passed,
                    f"{item.target} {'通过' if passed else '未通过'}",
                )
            passed = all(
                evidence.status is TestEvidenceStatus.PASSED for evidence in tests
            )
            return AcceptanceResult(
                item, passed, "全部测试通过" if passed else "存在未通过测试"
            )

        if item.kind is EvidenceKind.CONTENT_CONTAINS:
            symbol = (item.target or "").strip()
            if not symbol:
                return AcceptanceResult(item, False, "缺少目标符号")
            found = any(symbol in text for text in self._source_texts(workspace))
            return AcceptanceResult(
                item, found, f"源码{'包含' if found else '缺少'} {symbol}"
            )

        if item.kind is EvidenceKind.WORKSPACE_READ:
            return AcceptanceResult(
                item,
                workspace_read,
                "已读取工作区" if workspace_read else "没有成功的读取记录",
            )

        return AcceptanceResult(item, False, "未知的验收类型")

    @staticmethod
    def _source_texts(workspace: WorkspaceContext):
        """遍历工作区内的 Python 源码文本，跳过缓存与虚拟环境。"""
        for path in workspace.root.rglob("*.py"):
            relative = path.relative_to(workspace.root)
            if any(part in IGNORED_DIRS for part in relative.parts):
                continue
            try:
                if path.stat().st_size > MAX_SOURCE_SIZE:
                    continue
                yield path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
