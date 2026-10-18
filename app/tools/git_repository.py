"""Git 仓库识别与基础元数据读取。"""

import json
import shutil
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from app.runtime.workspace import WorkspaceContext

GIT_TIMEOUT = 5


class GitRepositoryStatus(StrEnum):
    """工作区的 Git 可用状态。"""

    AVAILABLE = "available"
    NOT_REPOSITORY = "not_repository"
    GIT_UNAVAILABLE = "git_unavailable"


@dataclass(frozen=True)
class GitRepositoryInfo:
    """一次 Git 仓库识别得到的稳定结果。"""

    status: GitRepositoryStatus
    repository_root: Path | None = None
    branch: str | None = None
    detached_head: bool = False
    reason: str | None = None

    @property
    def available(self) -> bool:
        return self.status is GitRepositoryStatus.AVAILABLE

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "available": self.available,
            "repository_root": (
                str(self.repository_root) if self.repository_root else None
            ),
            "branch": self.branch,
            "detached_head": self.detached_head,
            "reason": self.reason,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


class GitRepositoryInspector:
    """使用固定只读命令识别工作区所属的 Git 仓库。"""

    def inspect(self, workspace: WorkspaceContext) -> GitRepositoryInfo:
        git_path = shutil.which("git")
        if git_path is None:
            return GitRepositoryInfo(
                status=GitRepositoryStatus.GIT_UNAVAILABLE,
                reason="当前环境未找到 Git 可执行文件。",
            )

        root_result = self._run(
            git_path,
            workspace.root,
            "rev-parse",
            "--show-toplevel",
        )
        if root_result.returncode != 0:
            return GitRepositoryInfo(
                status=GitRepositoryStatus.NOT_REPOSITORY,
                reason="工作区不在 Git 仓库中。",
            )

        root_text = root_result.stdout.strip()
        if not root_text:
            return GitRepositoryInfo(
                status=GitRepositoryStatus.NOT_REPOSITORY,
                reason="Git 未返回仓库根目录。",
            )

        repository_root = Path(root_text).resolve()
        branch_result = self._run(
            git_path,
            workspace.root,
            "symbolic-ref",
            "--quiet",
            "--short",
            "HEAD",
        )
        branch = branch_result.stdout.strip() or None
        detached_head = branch_result.returncode != 0

        return GitRepositoryInfo(
            status=GitRepositoryStatus.AVAILABLE,
            repository_root=repository_root,
            branch=branch,
            detached_head=detached_head,
        )

    @staticmethod
    def _run(
        git_path: str,
        cwd: Path,
        *arguments: str,
    ) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                [git_path, *arguments],
                cwd=cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=GIT_TIMEOUT,
                shell=False,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return subprocess.CompletedProcess(
                args=[git_path, *arguments],
                returncode=1,
                stdout="",
                stderr="Git 命令执行失败。",
            )


def inspect_git_repository(workspace: WorkspaceContext) -> GitRepositoryInfo:
    """使用默认检查器识别工作区所属的 Git 仓库。"""

    return GitRepositoryInspector().inspect(workspace)
