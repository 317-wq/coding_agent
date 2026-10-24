"""文件变更摘要构建与渲染测试。"""

import pytest

from app.core.change_summary import build_change_summary, render_change_summary
from app.tools.change_set import ChangeSetSummary, FileChange, FileChangeKind


def _changes() -> ChangeSetSummary:
    """创建包含三类实际文件变化的测试变更集。"""
    return ChangeSetSummary(
        touched_count=3,
        unchanged_count=0,
        changes=(
            FileChange("new.py", FileChangeKind.CREATED, None, 1, None, "a", None, 0o644),
            FileChange("old.py", FileChangeKind.MODIFIED, 1, 2, "a", "b", 0o644, 0o644),
            FileChange("gone.py", FileChangeKind.DELETED, 1, None, "a", None, 0o644, None),
        ),
    )


def test_groups_actual_changes_and_unresolved_paths():
    """验证摘要按创建、修改、删除和未恢复分组。"""
    summary = build_change_summary(_changes(), ("old.py", "old.py"))
    assert summary.created == ("new.py",)
    assert summary.modified == ("old.py",)
    assert summary.deleted == ("gone.py",)
    assert summary.unresolved == ("old.py",)
    rendered = render_change_summary(summary)
    assert "创建（1）" in rendered
    assert "未恢复（1）" in rendered
    assert "gone.py" in rendered


@pytest.mark.parametrize("path", ["/tmp/outside.py", "../outside.py", ""])
def test_rejects_paths_outside_workspace(path: str):
    """验证变更摘要不会接受工作区外路径。"""
    with pytest.raises(ValueError):
        build_change_summary(_changes(), (path,))
