"""面向模型的受限只读 Git 工具。"""

import shutil
import subprocess
from pathlib import Path

from app.runtime.workspace import WorkspaceContext
from app.tools.git_repository import GIT_TIMEOUT, inspect_git_repository

MAX_GIT_OUTPUT = 12_000

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "git_status",
            "description": "读取工作区内的 Git 分支和文件状态，不执行任何写入操作。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "工作区内的相对路径，默认为整个工作区。",
                    }
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_diff",
            "description": "读取工作区内未暂存或已暂存的 Git 差异，不执行任何写入操作。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "工作区内的相对路径，默认为整个工作区。",
                    },
                    "staged": {
                        "type": "boolean",
                        "description": "为 true 时读取已暂存差异，默认为 false。",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    },
]


class GitReadTools:
    """仅执行预定义 status 和 diff 命令的 Git 查询器。"""

    def __init__(self, workspace: WorkspaceContext) -> None:
        self.workspace = workspace

    def status(self, path: str = ".") -> str:
        prepared = self._prepare_path(path)
        if isinstance(prepared, str):
            return prepared
        git_path, repository_root, pathspec = prepared
        return self._run(
            git_path,
            repository_root,
            "status",
            "--short",
            "--branch",
            "--untracked-files=all",
            "--",
            pathspec,
            empty_message="工作区内没有 Git 状态变更。",
        )

    def diff(self, path: str = ".", staged: bool = False) -> str:
        prepared = self._prepare_path(path)
        if isinstance(prepared, str):
            return prepared
        git_path, repository_root, pathspec = prepared
        arguments = ["diff", "--no-ext-diff", "--unified=3"]
        if staged:
            arguments.append("--cached")
        arguments.extend(("--", pathspec))
        return self._run(
            git_path,
            repository_root,
            *arguments,
            empty_message="工作区内没有对应的 Git 差异。",
        )

    def _prepare_path(self, path: str) -> tuple[str, Path, str] | str:
        if not isinstance(path, str) or not path.strip():
            return "错误：Git 查询路径不能为空。"
        try:
            target = self.workspace.resolve(path.strip())
        except ValueError as exc:
            return f"错误：Git 查询路径无效：{exc}"

        repository = inspect_git_repository(self.workspace)
        if not repository.available or repository.repository_root is None:
            return f"错误：{repository.reason or 'Git 仓库不可用。'}"

        git_path = shutil.which("git")
        if git_path is None:
            return "错误：当前环境未找到 Git 可执行文件。"

        try:
            pathspec = target.relative_to(repository.repository_root).as_posix()
        except ValueError:
            return "错误：工作区路径不在已识别的 Git 仓库中。"
        return git_path, repository.repository_root, pathspec

    @staticmethod
    def _run(
        git_path: str,
        repository_root: Path,
        *arguments: str,
        empty_message: str,
    ) -> str:
        try:
            result = subprocess.run(
                [git_path, *arguments],
                cwd=repository_root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=GIT_TIMEOUT,
                shell=False,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return f"错误：Git 查询超过 {GIT_TIMEOUT} 秒，已终止。"
        except OSError as exc:
            return f"错误：Git 查询执行失败：{exc}"

        if result.returncode != 0:
            error = result.stderr.strip() or "未知 Git 查询错误"
            return f"错误：Git 查询失败（退出码 {result.returncode}）：{error}"
        output = result.stdout.strip()
        if not output:
            return empty_message
        if len(output) > MAX_GIT_OUTPUT:
            return output[:MAX_GIT_OUTPUT] + "\nGit 输出过长，已截断。"
        return output


def git_status(path: str = ".") -> str:
    """读取当前受控工作区的 Git 状态。"""

    from app import tools

    return GitReadTools(tools.WORKSPACE_CONTEXT).status(path)


def git_diff(path: str = ".", staged: bool = False) -> str:
    """读取当前受控工作区的 Git 差异。"""

    from app import tools

    return GitReadTools(tools.WORKSPACE_CONTEXT).diff(path, staged)
