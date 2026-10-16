import json
import tomllib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from app.workspace import WorkspaceContext

IGNORED_DIRECTORIES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
}

PROJECT_MARKERS = {
    "Cargo.toml": "Rust",
    "go.mod": "Go",
    "package.json": "Node.js",
    "pom.xml": "Java/Maven",
    "pyproject.toml": "Python",
    "requirements.txt": "Python",
    "setup.py": "Python",
}

LANGUAGE_BY_SUFFIX = {
    ".c": "C",
    ".cpp": "C++",
    ".go": "Go",
    ".java": "Java",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".py": "Python",
    ".rs": "Rust",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
}

SOURCE_DIRECTORY_NAMES = {"app", "cmd", "lib", "pkg", "src"}
ENTRY_POINT_NAMES = {"main.py", "manage.py", "app.py", "main.go", "main.rs"}
MANIFEST_NAMES = set(PROJECT_MARKERS) | {
    "build.gradle",
    "docker-compose.yml",
    "Dockerfile",
    "Makefile",
    "uv.lock",
}


@dataclass(frozen=True)
class ProjectSnapshot:
    """一次受限项目扫描得到的基础结构信息。"""

    project_types: tuple[str, ...]
    languages: tuple[str, ...]
    manifests: tuple[str, ...]
    source_directories: tuple[str, ...]
    entry_points: tuple[str, ...]
    test_paths: tuple[str, ...]
    metadata: tuple[str, ...]
    tree: tuple[str, ...]
    truncated: bool

    def to_prompt(self) -> str:
        """转换为适合提供给模型的紧凑文本。"""

        def show(values: tuple[str, ...]) -> str:
            return "、".join(values) if values else "未识别"

        lines = [
            f"项目类型：{show(self.project_types)}",
            f"主要语言：{show(self.languages)}",
            f"清单与配置：{show(self.manifests)}",
            f"源码目录：{show(self.source_directories)}",
            f"入口文件：{show(self.entry_points)}",
            f"测试位置：{show(self.test_paths)}",
            f"项目元数据：{show(self.metadata)}",
            "目录结构：",
            *(self.tree or ("（空工作区）",)),
        ]

        if self.truncated:
            lines.append("扫描结果已达到深度或条目上限。")

        return "\n".join(lines)


class ProjectExplorer:
    """在工作区边界内扫描项目并提取基础信息。"""

    def __init__(
        self,
        workspace: WorkspaceContext,
        max_entries: int = 300,
        max_depth: int = 4,
    ) -> None:
        if max_entries < 1:
            raise ValueError("max_entries 必须大于 0。")
        if max_depth < 0:
            raise ValueError("max_depth 不能小于 0。")

        self.workspace = workspace
        self.max_entries = max_entries
        self.max_depth = max_depth

    def explore(self) -> ProjectSnapshot:
        """扫描目录，并返回确定性排序的项目快照。"""

        paths: list[Path] = []
        tree: list[str] = []
        truncated = False

        def visit(directory: Path, depth: int) -> None:
            nonlocal truncated

            try:
                children = sorted(directory.iterdir(), key=lambda item: item.name)
            except OSError:
                return

            for child in children:
                if len(paths) >= self.max_entries:
                    truncated = True
                    return
                if child.is_symlink():
                    continue
                if child.is_dir() and child.name in IGNORED_DIRECTORIES:
                    continue

                relative = child.relative_to(self.workspace.root)
                paths.append(relative)
                tree.append(f"{'  ' * depth}{relative.as_posix()}{'/' if child.is_dir() else ''}")

                if child.is_dir():
                    if depth < self.max_depth:
                        visit(child, depth + 1)
                    else:
                        truncated = True

        visit(self.workspace.root, 0)

        files = [path for path in paths if (self.workspace.root / path).is_file()]
        directories = [
            path for path in paths if (self.workspace.root / path).is_dir()
        ]
        language_counts = Counter(
            LANGUAGE_BY_SUFFIX[path.suffix.lower()]
            for path in files
            if path.suffix.lower() in LANGUAGE_BY_SUFFIX
        )

        project_types = sorted(
            {
                PROJECT_MARKERS[path.name]
                for path in files
                if path.name in PROJECT_MARKERS
            }
        )
        languages = tuple(
            f"{language} ({count})"
            for language, count in sorted(
                language_counts.items(), key=lambda item: (-item[1], item[0])
            )
        )
        manifests = tuple(
            sorted(path.as_posix() for path in files if path.name in MANIFEST_NAMES)
        )
        source_directories = tuple(
            sorted(
                path.as_posix()
                for path in directories
                if len(path.parts) == 1 and path.name in SOURCE_DIRECTORY_NAMES
            )
        )
        entry_points = tuple(
            sorted(path.as_posix() for path in files if path.name in ENTRY_POINT_NAMES)
        )
        test_paths = tuple(
            sorted(
                path.as_posix()
                for path in paths
                if "tests" in path.parts or path.name.startswith("test_")
            )[:20]
        )

        return ProjectSnapshot(
            project_types=tuple(project_types),
            languages=languages,
            manifests=manifests,
            source_directories=source_directories,
            entry_points=entry_points,
            test_paths=test_paths,
            metadata=self._read_metadata(),
            tree=tuple(tree),
            truncated=truncated,
        )

    def _read_metadata(self) -> tuple[str, ...]:
        """从根目录常用清单中提取少量非敏感项目元数据。"""

        metadata: list[str] = []
        pyproject = self.workspace.root / "pyproject.toml"
        package_json = self.workspace.root / "package.json"

        try:
            if pyproject.is_file() and pyproject.stat().st_size <= 256 * 1024:
                project = tomllib.loads(pyproject.read_text(encoding="utf-8")).get(
                    "project", {}
                )
                if name := project.get("name"):
                    metadata.append(f"Python 项目名={name}")
                if requires_python := project.get("requires-python"):
                    metadata.append(f"Python 版本={requires_python}")
        except (OSError, UnicodeError, tomllib.TOMLDecodeError, AttributeError):
            pass

        try:
            if package_json.is_file() and package_json.stat().st_size <= 256 * 1024:
                package = json.loads(package_json.read_text(encoding="utf-8"))
                if isinstance(package, dict) and package.get("name"):
                    metadata.append(f"Node.js 项目名={package['name']}")
        except (OSError, UnicodeError, json.JSONDecodeError):
            pass

        return tuple(metadata)
