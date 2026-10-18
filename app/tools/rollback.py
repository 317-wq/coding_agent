"""经明确授权恢复单次任务触碰的文件。"""

import json
import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from app.tools.authorization import ConsoleAuthorizer, ToolAuthorizer
from app.tools.change_set import ChangeSetError, TaskChangeSet
from app.tools.change_snapshot import FileSnapshot


class RollbackStatus(StrEnum):
    RESTORED = "restored"
    CANCELLED = "cancelled"
    CONFLICT = "conflict"
    FAILED = "failed"
    NO_CHANGES = "no_changes"


@dataclass(frozen=True)
class RollbackResult:
    """一次授权回滚的结构化结果。"""

    status: RollbackStatus
    reason: str
    restored_paths: tuple[str, ...] = ()
    pending_paths: tuple[str, ...] = ()

    @property
    def successful(self) -> bool:
        return self.status in {RollbackStatus.RESTORED, RollbackStatus.NO_CHANGES}

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "successful": self.successful,
            "reason": self.reason,
            "restored_paths": list(self.restored_paths),
            "pending_paths": list(self.pending_paths),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


class AuthorizedRollback:
    """授权后逐文件恢复快照，并在授权前后检测状态冲突。"""

    def __init__(
        self,
        change_set: TaskChangeSet,
        authorizer: ToolAuthorizer | None = None,
    ) -> None:
        self.change_set = change_set
        self.authorizer = authorizer or ConsoleAuthorizer()

    def execute(self) -> RollbackResult:
        try:
            expected = self.change_set.summary()
        except ChangeSetError as exc:
            return RollbackResult(RollbackStatus.CONFLICT, str(exc))
        paths = tuple(change.path for change in expected.changes)
        if not paths:
            return RollbackResult(
                RollbackStatus.NO_CHANGES,
                "本次任务没有需要恢复的文件变更。",
            )

        try:
            decision = self.authorizer.authorize(
                "rollback_task_changes",
                {"paths": list(paths), "changed_count": len(paths)},
            )
        except Exception as exc:  # noqa: BLE001 - authorization boundary
            return RollbackResult(
                RollbackStatus.CANCELLED,
                f"回滚授权检查失败：{exc}",
                pending_paths=paths,
            )
        if not decision.approved:
            return RollbackResult(
                RollbackStatus.CANCELLED,
                f"回滚未获授权：{decision.reason}",
                pending_paths=paths,
            )

        try:
            current = self.change_set.summary()
        except ChangeSetError as exc:
            return RollbackResult(
                RollbackStatus.CONFLICT,
                f"授权后路径状态不安全：{exc}",
                pending_paths=paths,
            )
        if current != expected:
            return RollbackResult(
                RollbackStatus.CONFLICT,
                "授权期间文件状态发生变化，已拒绝覆盖。",
                pending_paths=paths,
            )

        restored: list[str] = []
        snapshots = self.change_set.snapshot_store
        try:
            for path in paths:
                snapshot = snapshots.get(path)
                if snapshot is None:
                    raise OSError(f"缺少路径快照：{path}")
                self._restore(snapshot)
                restored.append(path)
        except OSError as exc:
            pending = tuple(path for path in paths if path not in restored)
            return RollbackResult(
                RollbackStatus.FAILED,
                f"文件恢复失败：{exc}",
                tuple(restored),
                pending,
            )

        try:
            remaining = self.change_set.summary()
        except ChangeSetError as exc:
            return RollbackResult(
                RollbackStatus.FAILED,
                f"回滚后核验失败：{exc}",
                tuple(restored),
            )
        if remaining.changes:
            return RollbackResult(
                RollbackStatus.FAILED,
                "回滚后仍存在未恢复的任务变更。",
                tuple(restored),
                tuple(change.path for change in remaining.changes),
            )
        return RollbackResult(
            RollbackStatus.RESTORED,
            "本次任务触碰的文件已恢复到首次变更前状态。",
            tuple(restored),
        )

    def _restore(self, snapshot: FileSnapshot) -> None:
        target = self._safe_target(snapshot.path)
        if not snapshot.existed:
            if target.is_symlink() or not target.is_file():
                raise OSError(f"待删除的新建路径不是普通文件：{snapshot.path}")
            target.unlink()
            return

        if snapshot.content is None or snapshot.mode is None:
            raise OSError(f"已有文件快照不完整：{snapshot.path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target = self._safe_target(snapshot.path)
        temp = target.with_name(f".{target.name}.rollback.tmp")
        try:
            with temp.open("xb") as file:
                file.write(snapshot.content)
                file.flush()
                os.fsync(file.fileno())
            temp.chmod(snapshot.mode)
            temp.replace(target)
        finally:
            if temp.exists():
                temp.unlink()

    def _safe_target(self, path: str) -> Path:
        target = self.change_set.workspace.root
        for part in Path(path).parts:
            target /= part
            if target.is_symlink():
                raise OSError(f"恢复路径包含符号链接：{path}")
            if (
                target.exists()
                and target != self.change_set.workspace.root
                and target != self.change_set.workspace.root / path
                and not target.is_dir()
            ):
                raise OSError(f"恢复路径的父级不是目录：{path}")
        return target
