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
        """将仓库识别结果序列化为 JSON。"""
        return json.dumps(self.to_dict(), ensure_ascii=False)


@dataclass(frozen=True)
class GitWorkingTreeState:
    """最终核验阶段读取到的工作区 Git 状态。"""

    status: GitRepositoryStatus
    dirty: bool | None = None
    changed_count: int | None = None
    reason: str | None = None

    @property
    def available(self) -> bool:
        """返回工作区是否可以读取 Git 状态。"""
        return self.status is GitRepositoryStatus.AVAILABLE

    def to_dict(self) -> dict[str, Any]:
        """返回不包含仓库外路径的可序列化状态。"""
        return {
            "status": self.status.value,
            "available": self.available,
            "dirty": self.dirty,
            "changed_count": self.changed_count,
            "reason": self.reason,
        }


class GitRepositoryInspector:
    """使用固定只读命令识别工作区所属的 Git 仓库。"""

    def inspect(self, workspace: WorkspaceContext) -> GitRepositoryInfo:
        """识别工作区所在仓库及当前分支。"""
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

    def inspect_worktree(self, workspace: WorkspaceContext) -> GitWorkingTreeState:
        """用只读 Git 命令统计工作区最终脏状态和变更条目数。"""
        repository = self.inspect(workspace)
        if not repository.available:
            return GitWorkingTreeState(
                status=repository.status,
                reason=repository.reason,
            )
        git_path = shutil.which("git")
        if git_path is None:
            return GitWorkingTreeState(
                status=GitRepositoryStatus.GIT_UNAVAILABLE,
                reason="当前环境未找到 Git 可执行文件。",
            )
        result = self._run(
            git_path,
            workspace.root,
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            "--",
            ".",
        )
        if result.returncode != 0:
            return GitWorkingTreeState(
                status=GitRepositoryStatus.GIT_UNAVAILABLE,
                reason="无法读取工作区 Git 状态。",
            )
        changed_count = self._porcelain_entry_count(result.stdout)
        return GitWorkingTreeState(
            status=GitRepositoryStatus.AVAILABLE,
            dirty=changed_count > 0,
            changed_count=changed_count,
        )

    @staticmethod
    def _porcelain_entry_count(output: str) -> int:
        """统计 NUL 分隔 porcelain 输出中的变更记录并正确跳过重命名源路径。"""
        entries = [entry for entry in output.split("\0") if entry]
        count = 0
        index = 0
        while index < len(entries):
            entry = entries[index]
            count += 1
            renamed = len(entry) >= 2 and (
                entry[0] in {"R", "C"} or entry[1] in {"R", "C"}
            )
            index += 2 if renamed else 1
        return count

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


def inspect_git_worktree(workspace: WorkspaceContext) -> GitWorkingTreeState:
    """使用默认检查器读取最终工作区 Git 状态。"""
    return GitRepositoryInspector().inspect_worktree(workspace)
