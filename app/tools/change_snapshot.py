"""单次任务内的文件原始状态快照。"""

import hashlib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.runtime.workspace import WorkspaceContext

MAX_SNAPSHOT_FILE_SIZE = 1024 * 1024
MAX_SNAPSHOT_FILES = 100
MAX_SNAPSHOT_TOTAL_SIZE = 10 * 1024 * 1024


class SnapshotError(ValueError):
    """路径无法被安全快照时抛出的错误。"""


@dataclass(frozen=True)
class FileSnapshot:
    """文件首次被任务触碰前的不可变状态。"""

    path: str
    existed: bool
    content: bytes | None = None
    mode: int | None = None

    @property
    def size(self) -> int:
        """返回快照保存的原始内容字节数。"""
        return len(self.content) if self.content is not None else 0

    @property
    def content_sha256(self) -> str | None:
        """返回原始内容摘要，原路径不存在时返回空值。"""
        if self.content is None:
            return None
        return hashlib.sha256(self.content).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        """返回不包含原始文件内容的可序列化元数据。"""

        return {
            "path": self.path,
            "existed": self.existed,
            "size": self.size,
            "mode": f"{self.mode:04o}" if self.mode is not None else None,
            "content_sha256": self.content_sha256,
        }


class TaskSnapshotStore:
    """保存任务首次触碰文件时的状态，后续捕获不会覆盖原始值。"""

    def __init__(
        self,
        workspace: WorkspaceContext,
        *,
        max_file_size: int = MAX_SNAPSHOT_FILE_SIZE,
        max_files: int = MAX_SNAPSHOT_FILES,
        max_total_size: int = MAX_SNAPSHOT_TOTAL_SIZE,
    ) -> None:
        """创建带文件数、单文件和总容量限制的任务快照存储。"""
        if max_file_size < 1 or max_files < 1 or max_total_size < 1:
            raise ValueError("快照容量限制必须大于 0。")
        self.workspace = workspace
        self.max_file_size = max_file_size
        self.max_files = max_files
        self.max_total_size = max_total_size
        self._snapshots: dict[str, FileSnapshot] = {}

    @property
    def snapshots(self) -> tuple[FileSnapshot, ...]:
        """按首次捕获顺序返回不可变快照集合。"""
        return tuple(self._snapshots.values())

    @property
    def total_size(self) -> int:
        """返回当前全部快照内容占用的总字节数。"""
        return sum(snapshot.size for snapshot in self._snapshots.values())

    def get(self, path: str) -> FileSnapshot | None:
        """按统一规范路径查询已经捕获的快照。"""
        normalized, _ = self._resolve(path)
        return self._snapshots.get(normalized)

    def capture(self, path: str) -> FileSnapshot:
        """捕获路径原始状态；同一路径只捕获一次。"""

        normalized, target = self._resolve(path)
        existing = self._snapshots.get(normalized)
        if existing is not None:
            return existing
        if len(self._snapshots) >= self.max_files:
            raise SnapshotError(f"快照文件数量超过上限 {self.max_files}。")

        if not target.exists():
            snapshot = FileSnapshot(path=normalized, existed=False)
        else:
            if not target.is_file():
                raise SnapshotError(f"快照目标不是普通文件：{normalized}")
            before = target.stat()
            if before.st_size > self.max_file_size:
                raise SnapshotError(
                    f"文件超过单文件快照上限 {self.max_file_size} 字节：{normalized}"
                )
            content = target.read_bytes()
            after = target.stat()
            if (
                before.st_ino != after.st_ino
                or before.st_size != after.st_size
                or before.st_mtime_ns != after.st_mtime_ns
            ):
                raise SnapshotError(f"读取快照期间文件发生变化：{normalized}")
            if self.total_size + len(content) > self.max_total_size:
                raise SnapshotError(f"快照总大小超过上限 {self.max_total_size} 字节。")
            snapshot = FileSnapshot(
                path=normalized,
                existed=True,
                content=content,
                mode=stat.S_IMODE(after.st_mode),
            )

        self._snapshots[normalized] = snapshot
        return snapshot

    def to_dict(self) -> dict[str, Any]:
        """返回不泄漏原始内容的快照存储元数据。"""
        return {
            "file_count": len(self._snapshots),
            "total_size": self.total_size,
            "snapshots": [snapshot.to_dict() for snapshot in self.snapshots],
        }

    def to_json(self) -> str:
        """将快照存储元数据序列化为稳定 JSON。"""
        return json.dumps(self.to_dict(), ensure_ascii=False)

    def _resolve(self, path: str) -> tuple[str, Path]:
        """按共享工作区规则规范路径，并拒绝符号链接快照。"""
        if not isinstance(path, str) or not path.strip() or path.strip() == ".":
            raise SnapshotError("快照路径必须指向工作区内的具体文件。")
        candidate = Path(path.strip()).expanduser()
        lexical_target = (
            candidate if candidate.is_absolute() else self.workspace.root / candidate
        )
        lexical_target = Path(os.path.abspath(lexical_target))
        try:
            lexical_relative = lexical_target.relative_to(self.workspace.root)
        except ValueError:
            raise SnapshotError("禁止访问工作目录之外的路径。") from None

        current = self.workspace.root
        for part in lexical_relative.parts:
            current /= part
            if current.is_symlink():
                raise SnapshotError(f"不允许快照符号链接路径：{path}")

        try:
            target = self.workspace.resolve(path.strip())
        except ValueError as exc:
            raise SnapshotError(str(exc)) from exc
        normalized = target.relative_to(self.workspace.root).as_posix()

        return normalized, target
