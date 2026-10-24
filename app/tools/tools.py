"""Agent 内置工具实现：文件读写、搜索、测试和命令执行。"""

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from app.runtime.workspace import WorkspaceContext
from app.tools.result import ToolResult, ToolStatus
from app.tools.test_result import TestRunResult, TestRunStatus

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WORKSPACE = (PROJECT_ROOT / "workspace" / "demo").resolve()

MAX_FILE_SIZE = 1024 * 1024
TEST_TIMEOUT = 30
SEARCH_TIMEOUT = 10
MAX_LIST_ENTRIES = 500
MAX_SEARCH_RESULTS = 50
MAX_SEARCH_OUTPUT = 12000

IGNORED_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    "build",
    "dist",
}


TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "列出工作目录中的文件和目录。path 必须使用工作区相对路径，根目录使用 '.'。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "recursive": {"type": "boolean"},
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取工作目录中的文本文件。",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_code",
            "description": (
                "使用 rg 在工作目录中搜索关键词，返回相对路径、"
                "行号和匹配内容。可以指定搜索子目录。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "要搜索的非空普通文本关键词。",
                    },
                    "path": {
                        "type": "string",
                        "description": "搜索目录，相对于工作目录，默认为 .。",
                    },
                },
                "required": ["keyword"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_directory",
            "description": "在工作目录中创建新目录，可以创建多层目录。",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_file",
            "description": "创建新文件，不允许覆盖已有文件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "替换文件中唯一匹配的文本。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_text": {"type": "string"},
                    "new_text": {"type": "string"},
                },
                "required": ["path", "old_text", "new_text"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_tests",
            "description": "使用 pytest 运行测试。\
            test_path 可以是单个测试文件、测试目录或 '.'（整个工作区），\
            不传参数时默认运行整个工作区。",
            "parameters": {
                "type": "object",
                "properties": {"test_path": {"type": "string"}},
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_file",
            "description": "删除工作区内的指定文件，属于高风险操作，执行前必须经过用户授权。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"}
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_directory",
            "description": "删除工作区内的指定目录，属于高风险操作，执行前会列出将被删除的文件清单并请求一次授权；不跟随符号链接，禁止递归强制删除。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"}
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    },
]


class WorkspaceTools:
    """绑定单个受控工作区的内置工具集合。

    所有工具方法都通过实例持有的 WorkspaceContext 解析路径，不再依赖模块级
    可变全局，因此同一进程可以并行运行多个工作区而互不影响。
    """

    def __init__(self, workspace: WorkspaceContext) -> None:
        """绑定一个受控工作区上下文。"""
        self.workspace = workspace

    def _resolve_path(self, path: str) -> Path:
        """通过共享工作区上下文统一解析并校验工具路径。"""
        return self.workspace.resolve(path)


    def list_files(self, path: str = ".", recursive: bool = True) -> ToolResult:
        """列出指定目录中的文件和子目录。"""

        try:
            directory = self._resolve_path(path)

            if not directory.is_dir():
                return ToolResult.failure(f"目录不存在：{path}")

            entries: list[str] = []
            stack = [directory]

            while stack and len(entries) < MAX_LIST_ENTRIES:
                current = stack.pop()

                children = sorted(
                    current.iterdir(),
                    key=lambda item: item.name,
                    reverse=True,
                )

                for entry in children:
                    if entry.name in IGNORED_DIRS or entry.is_symlink():
                        continue

                    relative_path = entry.relative_to(self.workspace.root).as_posix()

                    if entry.is_dir():
                        entries.append(f"目录: {relative_path}")

                        if recursive and len(entries) < MAX_LIST_ENTRIES:
                            stack.append(entry)
                    elif entry.is_file():
                        entries.append(f"文件: {relative_path}")

                    if len(entries) >= MAX_LIST_ENTRIES:
                        break

            if not entries:
                return ToolResult.success("目录为空，或没有可列出的文件。")

            result = "\n".join(entries)

            if stack or len(entries) >= MAX_LIST_ENTRIES:
                result += f"\n结果已达到 {MAX_LIST_ENTRIES} 条上限。"

            return ToolResult.success(result)

        except (ValueError, OSError) as exc:
            return ToolResult.failure(f"{exc}")


    def read_file(self, path: str) -> ToolResult:
        """读取工作目录中的文本文件。"""

        try:
            file_path = self._resolve_path(path)

            if not file_path.is_file():
                return ToolResult.failure(f"文件不存在：{path}")

            if file_path.stat().st_size > MAX_FILE_SIZE:
                return ToolResult.failure("文件超过 1 MB，拒绝读取。")

            content = file_path.read_text(encoding="utf-8")

            if not content:
                return ToolResult.success(f"文件存在，但内容为空：{path}")

            return ToolResult.success(content)

        except (ValueError, OSError, UnicodeError) as exc:
            return ToolResult.failure(f"读取文件失败：{exc}")


    def search_code(self, keyword: str, path: str = ".") -> ToolResult:
        """通过 rg 搜索关键词，返回匹配路径、行号和内容。"""

        if not keyword or not keyword.strip():
            return ToolResult.failure("搜索关键词不能为空。")

        rg_path = shutil.which("rg")

        if rg_path is None:
            return ToolResult.failure("未找到 rg，请先安装 ripgrep。")

        try:
            search_path = self._resolve_path(path)

            if not search_path.is_dir():
                return ToolResult.failure(f"搜索目录不存在：{path}")

            # 显式排除无关目录，不依赖 .gitignore 是否存在
            command = [
                rg_path,
                "--line-number",
                "--with-filename",
                "--color",
                "never",
                "--fixed-strings",
                "--max-count",
                str(MAX_SEARCH_RESULTS),
                "--max-filesize",
                str(MAX_FILE_SIZE),
                "--glob",
                "!**/.git/**",
                "--glob",
                "!**/.venv/**",
                "--glob",
                "!**/__pycache__/**",
                "--glob",
                "!**/.pytest_cache/**",
                "--glob",
                "!**/.mypy_cache/**",
                "--glob",
                "!**/.ruff_cache/**",
                "--glob",
                "!**/node_modules/**",
                "--glob",
                "!**/build/**",
                "--glob",
                "!**/dist/**",
                "--glob",
                "!**/.git",
                "--glob",
                "!**/.venv",
                "--glob",
                "!**/__pycache__",
                "--glob",
                "!**/.pytest_cache",
                "--glob",
                "!**/.mypy_cache",
                "--glob",
                "!**/.ruff_cache",
                "--glob",
                "!**/node_modules",
                "--glob",
                "!**/build",
                "--glob",
                "!**/dist",
                "--",
                keyword,
                str(search_path),
            ]

            result = subprocess.run(
                command,
                cwd=self.workspace.root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=SEARCH_TIMEOUT,
                shell=False,
                check=False,
            )

            # rg: 0 = 找到匹配，1 = 没有匹配，2 = 搜索错误
            if result.returncode == 1:
                return ToolResult.success(f"没有找到关键词：{keyword}")

            if result.returncode != 0:
                error = result.stderr.strip() or "未知搜索错误"
                return ToolResult.failure(f"rg 搜索失败（退出码 {result.returncode}）：{error}")

            # 将绝对路径转换为工作目录相对路径
            lines = result.stdout.splitlines()
            formatted_lines: list[str] = []

            for line in lines[:MAX_SEARCH_RESULTS]:
                try:
                    file_part, line_number, content = line.split(":", 2)
                    file_path = Path(file_part)
                    relative_path = file_path.relative_to(self.workspace.root)
                    formatted_lines.append(
                        f"{relative_path}:{line_number}: {content}"
                    )
                except ValueError:
                    # 遇到无法解析的输出时保留原始行，避免静默丢失
                    formatted_lines.append(line)

            output = "\n".join(formatted_lines)

            if len(lines) > MAX_SEARCH_RESULTS:
                output += f"\n结果已截断，最多显示 {MAX_SEARCH_RESULTS} 条。"

            if len(output) > MAX_SEARCH_OUTPUT:
                output = output[:MAX_SEARCH_OUTPUT]
                output += "\n输出过长，已截断。"

            return ToolResult.success(output or f"没有找到关键词：{keyword}")

        except subprocess.TimeoutExpired:
            return ToolResult.failure(f"搜索超过 {SEARCH_TIMEOUT} 秒，已终止。")
        except (ValueError, OSError) as exc:
            return ToolResult.failure(f"搜索失败：{exc}")


    def create_directory(self, path: str) -> ToolResult:
        """创建目录，支持一次创建多层目录。"""

        try:
            if not path or path == ".":
                return ToolResult.failure("请指定要创建的目录路径。")

            directory = self._resolve_path(path)

            if directory.exists():
                if directory.is_dir():
                    return ToolResult.failure(f"目录已经存在：{path}")
                return ToolResult.failure(f"目标路径已被文件占用：{path}")

            directory.mkdir(parents=True, exist_ok=False)

            return ToolResult.success(f"创建目录成功：{path}")

        except FileExistsError:
            return ToolResult.failure(f"目标路径已经存在：{path}")
        except (ValueError, OSError) as exc:
            return ToolResult.failure(f"创建目录失败：{exc}")


    def create_file(self, path: str, content: str) -> ToolResult:
        """创建新文件，不允许覆盖已有文件。"""

        try:
            file_path = self._resolve_path(path)

            if file_path.exists() or file_path.is_symlink():
                return ToolResult.failure(f"文件或路径已经存在：{path}")

            if not file_path.parent.is_dir():
                return ToolResult.failure("父目录不存在，请先调用 create_directory。")

            if len(content.encode("utf-8")) > MAX_FILE_SIZE:
                return ToolResult.failure("文件内容超过 1 MB，拒绝创建。")

            with file_path.open("x", encoding="utf-8", newline="") as file:
                file.write(content)

            if file_path.read_text(encoding="utf-8") != content:
                return ToolResult.failure("创建后核验失败。")

            return ToolResult.success(f"创建文件成功并已核验：{path}")

        except FileExistsError:
            return ToolResult.failure(f"文件或路径已经存在：{path}")
        except (ValueError, OSError, UnicodeError) as exc:
            return ToolResult.failure(f"创建文件失败：{exc}")


    def edit_file(self, path: str, old_text: str, new_text: str) -> ToolResult:
        """替换已有文件中唯一匹配的文本。"""

        try:
            file_path = self._resolve_path(path)

            if not file_path.is_file():
                return ToolResult.failure(f"文件不存在：{path}")

            if file_path.stat().st_size > MAX_FILE_SIZE:
                return ToolResult.failure("文件超过 1 MB，拒绝修改。")

            content = file_path.read_text(encoding="utf-8")

            if not old_text:
                return ToolResult.failure("old_text 不能为空。")

            match_count = content.count(old_text)

            if match_count == 0:
                if new_text and new_text in content:
                    return ToolResult.success(f"无需修改，目标内容已存在：{self.workspace.relative_path(path)}")
                return ToolResult.failure("没有找到旧文本，文件未修改。")

            if match_count > 1:
                return ToolResult.failure(
                    f"旧文本出现 {match_count} 次，"
                    "无法唯一定位，文件未修改。"
                )

            updated_content = content.replace(old_text, new_text, 1)
            temp_path = file_path.with_name(f".{file_path.name}.tmp")

            try:
                with temp_path.open("x", encoding="utf-8", newline="") as file:
                    file.write(updated_content)
                    file.flush()
                    os.fsync(file.fileno())

                if file_path.read_text(encoding="utf-8") != content:
                    return ToolResult.failure("文件已发生变化，取消修改。")

                temp_path.replace(file_path)

            finally:
                if temp_path.exists():
                    temp_path.unlink()

            # 核对磁盘上的实际内容
            if file_path.read_text(encoding="utf-8") != updated_content:
                return ToolResult.failure("修改后核验失败。")

            return ToolResult.success(f"修改文件成功并已核验：{path}")

        except FileExistsError:
            return ToolResult.failure("临时文件已存在，请检查后重试。")
        except (ValueError, OSError, UnicodeError) as exc:
            return ToolResult.failure(f"修改文件失败：{exc}")



    def run_tests(self, test_path: str = ".") -> ToolResult:
        """运行指定测试文件、目录、pytest node id 或整个工作区。

        返回带结构化 ``data`` 的工具结果：文本供模型和日志使用，字段供完成判定、
        报告与 Trace 使用，避免下游各自解析文本。
        """
        try:
            path_part, separator, node_part = test_path.partition("::")
            if not path_part:
                return ToolResult.failure(f"测试路径不存在：{test_path}")
            target_path = self._resolve_path(path_part)
            canonical_path = self.workspace.relative_path(path_part)
            canonical_target = (
                f"{canonical_path}::{node_part}" if separator else canonical_path
            )

            if not target_path.exists():
                return ToolResult.failure(f"测试路径不存在：{test_path}")

            if not target_path.is_file() and not target_path.is_dir():
                return ToolResult.failure(f"测试目标不是文件或目录：{test_path}")

            if target_path.is_file() and target_path.suffix != ".py":
                return ToolResult.failure(f"指定的测试文件必须是 .py 文件：{test_path}")

            command = [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                f"{target_path}::{node_part}" if separator else str(target_path),
            ]

            child_env = os.environ.copy()

            # 不向测试子进程传递模型 API 密钥等敏感凭证。
            for key in list(child_env):
                if any(
                    token in key.upper()
                    for token in (
                        "API_KEY",
                        "API_TOKEN",
                        "ACCESS_TOKEN",
                        "SECRET",
                    )
                ):
                    child_env.pop(key, None)

            result = subprocess.run(
                command,
                cwd=self.workspace.root,
                env=child_env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=TEST_TIMEOUT,
                shell=False,
                check=False,
            )

            output = (result.stdout + "\n" + result.stderr).strip()
            passed = result.returncode == 0
            error_kind: str | None = None
            if not passed:
                lowered = output.lower()
                if result.returncode == 5 or "no tests ran" in lowered:
                    error_kind = "未发现测试"
                elif (
                    "no module named pytest" in lowered
                    or "timeout" in lowered
                    or "测试执行失败" in lowered
                ):
                    error_kind = "环境错误"
                elif (
                    "partially initialized module" in lowered
                    or "most likely due to a circular import" in lowered
                    or "importerror:" in lowered
                    or "modulenotfounderror:" in lowered
                ):
                    error_kind = "代码导入错误"
                elif (
                    "import file mismatch" in lowered
                    or "error collecting" in lowered
                ):
                    error_kind = "收集冲突"
                elif "nameerror" in lowered or "syntaxerror" in lowered:
                    error_kind = "测试代码"
                else:
                    error_kind = "断言失败"

            test_run = TestRunResult(
                target=canonical_target,
                exit_code=result.returncode,
                status=TestRunStatus.PASSED if passed else TestRunStatus.FAILED,
                error_kind=error_kind,
                output=output,
            )
            text = (
                f"测试目标：{canonical_target}\n"
                f"测试退出码：{result.returncode}\n"
                f"测试结果：{'通过' if passed else '失败'}\n"
                + (f"测试错误类型：{error_kind}\n" if error_kind else "")
                + output
            )
            if passed:
                return ToolResult.success(text, data=test_run)
            return ToolResult.failure(text, data=test_run)

        except subprocess.TimeoutExpired:
            return ToolResult.failure(f"测试超过 {TEST_TIMEOUT} 秒，已超时终止。")
        except (ValueError, OSError) as exc:
            return ToolResult.failure(f"测试执行失败：{exc}")


    def delete_file(self, path: str) -> ToolResult:
        """删除工作区内的指定文件，必须经过用户确认。"""
        try:
            target = self._resolve_path(path)

            if not target.exists() and not target.is_symlink():
                return ToolResult.failure(f"文件不存在：{path}")

            if target.is_symlink():
                return ToolResult.failure(f"不允许删除符号链接：{path}")

            if not target.is_file():
                return ToolResult.failure(f"目标不是普通文件：{path}")

            # 授权后再次检查路径，防止授权期间路径指向发生变化。
            verified = self._resolve_path(path)
            if verified != target or verified.is_symlink() or not verified.is_file():
                return ToolResult.failure(f"目标路径状态发生变化，已拒绝删除：{path}")

            verified.unlink()
            return ToolResult.success(f"删除文件成功：{path}")

        except (ValueError, OSError) as exc:
            return ToolResult.failure(f"删除文件失败：{exc}")


    def describe_directory_deletion(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """生成目录删除的授权清单，列出将被删除的普通文件路径。

        入参是工具调用参数；该清单只用于授权展示，实际删除会重新枚举目录，
        避免使用过期快照。
        """
        path = arguments.get("path")
        if not isinstance(path, str) or not path.strip():
            raise ValueError("路径不能为空。")

        target = self._resolve_path(path)
        if target.is_symlink() or not target.is_dir():
            return {"path": path, "file_count": 0, "files": []}
        files = [
            child.relative_to(self.workspace.root).as_posix()
            for child in sorted(target.rglob("*"))
            if child.is_file() and not child.is_symlink()
        ]
        return {
            "path": path,
            "file_count": len(files),
            "files": files[:MAX_LIST_ENTRIES],
        }


    def delete_directory(self, path: str) -> ToolResult:
        """删除目录内容并移除空目录；授权由工具注册表在调用前统一完成。"""
        try:
            target = self._resolve_path(path)

            if not target.exists():
                return ToolResult.failure(f"目录不存在：{path}")

            if target.is_symlink():
                return ToolResult.failure(f"不允许删除符号链接：{path}")

            if not target.is_dir():
                return ToolResult.failure(f"目标不是目录：{path}")

            if target == self.workspace.root:
                return ToolResult.failure("不允许删除整个工作区根目录。")

            results: list[str] = []
            failed = False

            # 先取得当前目录项快照，按名称排序，方便测试和阅读输出。
            try:
                children = sorted(target.iterdir(), key=lambda item: item.name)
            except OSError as exc:
                return ToolResult.failure(f"无法读取目录：{exc}")

            for child in children:
                relative_path = child.relative_to(self.workspace.root).as_posix()

                # 不跟随符号链接，避免越界访问或删除链接目标。
                if child.is_symlink():
                    results.append(f"跳过符号链接：{relative_path}")
                    continue

                if child.is_dir():
                    # 递归调用同一删除逻辑，子目录在清空后一并移除。
                    outcome = self.delete_directory(relative_path)
                elif child.is_file():
                    outcome = self.delete_file(relative_path)
                else:
                    results.append(f"跳过不支持的文件类型：{relative_path}")
                    continue

                results.append(outcome.result or outcome.error or "")
                if outcome.status is not ToolStatus.SUCCESS:
                    failed = True

            # 授权已在注册表层完成；不使用 shutil.rmtree，仅删除空目录。
            verified = self._resolve_path(path)

            if verified != target or verified.is_symlink() or not verified.is_dir():
                return ToolResult.failure(
                    "\n".join([*results, f"目录状态发生变化，已拒绝删除：{path}"])
                )

            try:
                verified.rmdir()
                results.append(f"删除目录成功：{path}")
            except OSError as exc:
                failed = True
                results.append(
                    f"无法删除目录 {path}：目录可能仍有未删除的内容。详情：{exc}"
                )

            joined = "\n".join(results)
            if failed:
                return ToolResult.failure(joined)
            return ToolResult.success(joined)

        except (ValueError, OSError) as exc:
            return ToolResult.failure(f"删除目录失败：{exc}")
