"""将实际文件变更整理为结构化分组和简洁中文摘要。"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import PurePosixPath

from app.core.task_result import ChangeSummary
from app.tools.change_set import ChangeSetSummary


class ChangeSummaryBuilder:
    """根据变更集生成工作区相对路径的变更摘要。"""

    def build(
        self,
        changes: ChangeSetSummary,
        unresolved: Iterable[str] = (),
    ) -> ChangeSummary:
        """按创建、修改、删除和未恢复四类输出实际变更路径。"""
        if not isinstance(changes, ChangeSetSummary):
            raise TypeError("变更摘要必须是 ChangeSetSummary。")
        return ChangeSummary(
            created=self._paths(changes.created_paths),
            modified=self._paths(changes.modified_paths),
            deleted=self._paths(changes.deleted_paths),
            unresolved=self._paths(unresolved),
        )

    @staticmethod
    def _paths(paths: Iterable[str]) -> tuple[str, ...]:
        """去重并校验路径，确保结果不会泄漏工作区外绝对路径。"""
        result: list[str] = []
        for path in paths:
            if not isinstance(path, str) or not path.strip():
                raise ValueError("变更路径必须是非空字符串。")
            normalized = path.replace("\\", "/").strip()
            parsed = PurePosixPath(normalized)
            if parsed.is_absolute() or ".." in parsed.parts:
                raise ValueError("变更路径必须是工作区相对路径。")
            if normalized not in result:
                result.append(normalized)
        return tuple(result)


def render_change_summary(summary: ChangeSummary) -> str:
    """将结构化变更摘要渲染为按类别分组的中文文本。"""
    if not isinstance(summary, ChangeSummary):
        raise TypeError("变更摘要必须是 ChangeSummary。")
    groups = (
        ("创建", summary.created),
        ("修改", summary.modified),
        ("删除", summary.deleted),
        ("未恢复", summary.unresolved),
    )
    lines = ["文件变更："]
    for label, paths in groups:
        lines.append(f"{label}（{len(paths)}）")
        lines.extend(f"  - {path}" for path in paths)
    return "\n".join(lines)


def build_change_summary(
    changes: ChangeSetSummary,
    unresolved: Iterable[str] = (),
) -> ChangeSummary:
    """使用默认构建器生成结构化文件变更摘要。"""
    return ChangeSummaryBuilder().build(changes, unresolved)
