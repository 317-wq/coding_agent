"""基于任务快照计算当前文件变更集。"""

import hashlib
import json
import stat
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from app.runtime.workspace import WorkspaceContext
from app.tools.change_snapshot import FileSnapshot, TaskSnapshotStore


class ChangeSetError(ValueError):
    """当前路径无法安全生成变更摘要。"""


class FileChangeKind(StrEnum):
    CREATED = "created"
    MODIFIED = "modified"
    DELETED = "deleted"


@dataclass(frozen=True)
class FileChange:
    """一个文件在任务开始状态与当前状态之间的差异。"""

    path: str
    kind: FileChangeKind
    before_size: int | None
    after_size: int | None
    before_sha256: str | None
    after_sha256: str | None
    before_mode: int | None
    after_mode: int | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "kind": self.kind.value,
            "before_size": self.before_size,
            "after_size": self.after_size,
            "before_sha256": self.before_sha256,
            "after_sha256": self.after_sha256,
            "before_mode": self._format_mode(self.before_mode),
            "after_mode": self._format_mode(self.after_mode),
        }

    @staticmethod
    def _format_mode(mode: int | None) -> str | None:
        return f"{mode:04o}" if mode is not None else None


@dataclass(frozen=True)
class ChangeSetSummary:
    """任务当前变更的稳定摘要。"""

    touched_count: int
    unchanged_count: int
    changes: tuple[FileChange, ...]

    @property
    def created_paths(self) -> tuple[str, ...]:
        return self._paths(FileChangeKind.CREATED)

    @property
    def modified_paths(self) -> tuple[str, ...]:
        return self._paths(FileChangeKind.MODIFIED)

    @property
    def deleted_paths(self) -> tuple[str, ...]:
        return self._paths(FileChangeKind.DELETED)

    def _paths(self, kind: FileChangeKind) -> tuple[str, ...]:
        return tuple(change.path for change in self.changes if change.kind is kind)

    def to_dict(self) -> dict[str, Any]:
        return {
            "touched_count": self.touched_count,
            "changed_count": len(self.changes),
            "unchanged_count": self.unchanged_count,
            "created_paths": list(self.created_paths),
            "modified_paths": list(self.modified_paths),
            "deleted_paths": list(self.deleted_paths),
            "changes": [change.to_dict() for change in self.changes],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


class TaskChangeSet:
    """记录任务触碰路径，并从首次快照生成实时变更摘要。"""

    def __init__(
        self,
        workspace: WorkspaceContext,
        snapshot_store: TaskSnapshotStore | None = None,
    ) -> None:
        self.workspace = workspace
        self.snapshot_store = snapshot_store or TaskSnapshotStore(workspace)
        if self.snapshot_store.workspace.root != workspace.root:
            raise ValueError("变更集与快照存储必须使用同一工作区。")

    def touch(self, path: str) -> FileSnapshot:
        """在路径发生变更前记录其首次状态。"""

        return self.snapshot_store.capture(path)

    def summary(self) -> ChangeSetSummary:
        changes: list[FileChange] = []
        unchanged_count = 0
        for snapshot in self.snapshot_store.snapshots:
            change = self._compare(snapshot)
            if change is None:
                unchanged_count += 1
            else:
                changes.append(change)
        return ChangeSetSummary(
            touched_count=len(self.snapshot_store.snapshots),
            unchanged_count=unchanged_count,
            changes=tuple(changes),
        )

    def _compare(self, snapshot: FileSnapshot) -> FileChange | None:
        target = self.workspace.root / snapshot.path
        if target.is_symlink():
            raise ChangeSetError(f"触碰路径已变为符号链接：{snapshot.path}")
        if not target.exists():
            if not snapshot.existed:
                return None
            return self._change(snapshot, FileChangeKind.DELETED)
        if not target.is_file():
            raise ChangeSetError(f"触碰路径当前不是普通文件：{snapshot.path}")

        size, digest, mode = self._current_metadata(target, snapshot.path)
        if not snapshot.existed:
            return self._change(
                snapshot,
                FileChangeKind.CREATED,
                after_size=size,
                after_sha256=digest,
                after_mode=mode,
            )
        if digest == snapshot.content_sha256 and mode == snapshot.mode:
            return None
        return self._change(
            snapshot,
            FileChangeKind.MODIFIED,
            after_size=size,
            after_sha256=digest,
            after_mode=mode,
        )

    def _current_metadata(self, target: Path, path: str) -> tuple[int, str, int]:
        before = target.stat()
        if before.st_size > self.snapshot_store.max_file_size:
            raise ChangeSetError(
                f"当前文件超过变更摘要上限 {self.snapshot_store.max_file_size} 字节：{path}"
            )
        content = target.read_bytes()
        after = target.stat()
        if (
            before.st_ino != after.st_ino
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
        ):
            raise ChangeSetError(f"生成变更摘要期间文件发生变化：{path}")
        return (
            len(content),
            hashlib.sha256(content).hexdigest(),
            stat.S_IMODE(after.st_mode),
        )

    @staticmethod
    def _change(
        snapshot: FileSnapshot,
        kind: FileChangeKind,
        *,
        after_size: int | None = None,
        after_sha256: str | None = None,
        after_mode: int | None = None,
    ) -> FileChange:
        return FileChange(
            path=snapshot.path,
            kind=kind,
            before_size=snapshot.size if snapshot.existed else None,
            after_size=after_size,
            before_sha256=snapshot.content_sha256,
            after_sha256=after_sha256,
            before_mode=snapshot.mode,
            after_mode=after_mode,
        )
