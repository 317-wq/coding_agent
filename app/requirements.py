"""对用户任务中明确的路径、异常和测试框架要求做结束前核对。"""

import re
from dataclasses import dataclass
from pathlib import Path

from app.workspace import WorkspaceContext


@dataclass(frozen=True)
class RequirementCheck:
    passed: bool
    missing: tuple[str, ...] = ()


class TaskRequirementVerifier:
    """只核对可从工作区静态确认的明确要求，不替代模型语义判断。"""

    _PATH_PATTERN = re.compile(r"(?:`([^`]+)`|\b((?:tests?/|app/)[\w./-]+\.py)\b)")
    _EXCEPTION_PATTERN = re.compile(r"\b[A-Z][A-Za-z0-9]*(?:Error|Exception)\b")

    def verify(self, task: str, workspace: WorkspaceContext) -> RequirementCheck:
        missing: list[str] = []
        required_paths: list[str] = []
        for match in self._PATH_PATTERN.finditer(task):
            candidate = match.group(1) or match.group(2)
            if candidate and candidate.endswith(".py") and "/" in candidate:
                required_paths.append(candidate)
        for path in dict.fromkeys(required_paths):
            try:
                if not workspace.resolve(path).is_file():
                    missing.append(f"明确要求的文件不存在：{path}")
            except ValueError:
                missing.append(f"明确要求的文件路径越界：{path}")

        files = self._python_files(workspace.root)
        contents = "\n".join(
            self._read(path) for path in files if path.is_file()
        )
        for exception_name in dict.fromkeys(self._EXCEPTION_PATTERN.findall(task)):
            if exception_name not in contents:
                missing.append(f"未在最终代码中发现明确要求的异常：{exception_name}")

        lowered = task.lower()
        if "pytest" in lowered and "pytest" not in contents.lower():
            missing.append("任务明确要求 pytest，但最终代码中未发现 pytest 使用。")
        if "unittest" in lowered and "unittest" not in contents.lower():
            missing.append("任务明确要求 unittest，但最终代码中未发现 unittest 使用。")
        return RequirementCheck(not missing, tuple(missing))

    @staticmethod
    def _python_files(root: Path) -> tuple[Path, ...]:
        ignored = {".git", ".venv", ".pytest_cache", "__pycache__"}
        return tuple(
            path
            for path in root.rglob("*.py")
            if not any(part in ignored for part in path.parts)
        )

    @staticmethod
    def _read(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
