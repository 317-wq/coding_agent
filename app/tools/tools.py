"""Agent 内置工具实现：文件读写、搜索、测试和命令执行。"""

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from app.runtime.workspace import WorkspaceContext

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WORKSPACE = (PROJECT_ROOT / "workspace" / "demo").resolve()
WORKSPACE = DEFAULT_WORKSPACE
WORKSPACE_CONTEXT = WorkspaceContext(root=WORKSPACE)

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
            "description": "删除工作区内的指定文件。必须向用户请求确认，只有用户明确输入 y 才能删除。",
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
            "description": "逐项删除工作区内的指定目录。每个文件、子目录和目录自身都必须分别向用户请求确认，只有输入 y 才能删除；禁止递归强制删除。",
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


def set_workspace(context: WorkspaceContext) -> None:
    """设置当前工具系统共享的工作区上下文。"""
    global WORKSPACE, WORKSPACE_CONTEXT
    WORKSPACE_CONTEXT = context
    WORKSPACE = context.root


def _resolve_path(path: str) -> Path:
    """通过共享工作区上下文统一解析并校验工具路径。"""
    return WORKSPACE_CONTEXT.resolve(path)


def list_files(path: str = ".", recursive: bool = True) -> str:
    """列出指定目录中的文件和子目录。"""

    try:
        directory = _resolve_path(path)

        if not directory.is_dir():
            return f"错误：目录不存在：{path}"

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

                relative_path = entry.relative_to(WORKSPACE).as_posix()

                if entry.is_dir():
                    entries.append(f"目录: {relative_path}")

                    if recursive and len(entries) < MAX_LIST_ENTRIES:
                        stack.append(entry)
                elif entry.is_file():
                    entries.append(f"文件: {relative_path}")

                if len(entries) >= MAX_LIST_ENTRIES:
                    break

        if not entries:
            return "目录为空，或没有可列出的文件。"

        result = "\n".join(entries)

        if stack or len(entries) >= MAX_LIST_ENTRIES:
            result += f"\n结果已达到 {MAX_LIST_ENTRIES} 条上限。"

        return result

    except (ValueError, OSError) as exc:
        return f"错误：{exc}"


def read_file(path: str) -> str:
    """读取工作目录中的文本文件。"""

    try:
        file_path = _resolve_path(path)

        if not file_path.is_file():
            return f"错误：文件不存在：{path}"

        if file_path.stat().st_size > MAX_FILE_SIZE:
            return "错误：文件超过 1 MB，拒绝读取。"

        content = file_path.read_text(encoding="utf-8")

        if not content:
            return f"文件存在，但内容为空：{path}"

        return content

    except (ValueError, OSError, UnicodeError) as exc:
        return f"错误：读取文件失败：{exc}"


def search_code(keyword: str, path: str = ".") -> str:
    """通过 rg 搜索关键词，返回匹配路径、行号和内容。"""

    if not keyword or not keyword.strip():
        return "错误：搜索关键词不能为空。"

    rg_path = shutil.which("rg")

    if rg_path is None:
        return "错误：未找到 rg，请先安装 ripgrep。"

    try:
        search_path = _resolve_path(path)

        if not search_path.is_dir():
            return f"错误：搜索目录不存在：{path}"

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
            cwd=WORKSPACE,
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
            return f"没有找到关键词：{keyword}"

        if result.returncode != 0:
            error = result.stderr.strip() or "未知搜索错误"
            return f"错误：rg 搜索失败（退出码 {result.returncode}）：{error}"

        # 将绝对路径转换为工作目录相对路径
        lines = result.stdout.splitlines()
        formatted_lines: list[str] = []

        for line in lines[:MAX_SEARCH_RESULTS]:
            try:
                file_part, line_number, content = line.split(":", 2)
                file_path = Path(file_part)
                relative_path = file_path.relative_to(WORKSPACE)
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

        return output or f"没有找到关键词：{keyword}"

    except subprocess.TimeoutExpired:
        return f"错误：搜索超过 {SEARCH_TIMEOUT} 秒，已终止。"
    except (ValueError, OSError) as exc:
        return f"错误：搜索失败：{exc}"


def create_directory(path: str) -> str:
    """创建目录，支持一次创建多层目录。"""

    try:
        if not path or path == ".":
            return "错误：请指定要创建的目录路径。"

        directory = _resolve_path(path)

        if directory.exists():
            if directory.is_dir():
                return f"错误：目录已经存在：{path}"
            return f"错误：目标路径已被文件占用：{path}"

        directory.mkdir(parents=True, exist_ok=False)

        return f"创建目录成功：{path}"

    except FileExistsError:
        return f"错误：目标路径已经存在：{path}"
    except (ValueError, OSError) as exc:
        return f"错误：创建目录失败：{exc}"


def create_file(path: str, content: str) -> str:
    """创建新文件，不允许覆盖已有文件。"""

    try:
        file_path = _resolve_path(path)

        if file_path.exists() or file_path.is_symlink():
            return f"错误：文件或路径已经存在：{path}"

        if not file_path.parent.is_dir():
            return "错误：父目录不存在，请先调用 create_directory。"

        if len(content.encode("utf-8")) > MAX_FILE_SIZE:
            return "错误：文件内容超过 1 MB，拒绝创建。"

        with file_path.open("x", encoding="utf-8", newline="") as file:
            file.write(content)

        return f"创建文件成功：{path}"

    except FileExistsError:
        return f"错误：文件或路径已经存在：{path}"
    except (ValueError, OSError, UnicodeError) as exc:
        return f"错误：创建文件失败：{exc}"


def edit_file(path: str, old_text: str, new_text: str) -> str:
    """替换已有文件中唯一匹配的文本。"""

    try:
        file_path = _resolve_path(path)

        if not file_path.is_file():
            return f"错误：文件不存在：{path}"

        if file_path.stat().st_size > MAX_FILE_SIZE:
            return "错误：文件超过 1 MB，拒绝修改。"

        content = file_path.read_text(encoding="utf-8")

        if not old_text:
            return "错误：old_text 不能为空。"

        match_count = content.count(old_text)

        if match_count == 0:
            if new_text and new_text in content:
                return f"无需修改，目标内容已存在：{WORKSPACE_CONTEXT.relative_path(path)}"
            return "错误：没有找到旧文本，文件未修改。"

        if match_count > 1:
            return (
                f"错误：旧文本出现 {match_count} 次，"
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
                return "错误：文件已发生变化，取消修改。"

            temp_path.replace(file_path)

        finally:
            if temp_path.exists():
                temp_path.unlink()

        # 核对磁盘上的实际内容
        if file_path.read_text(encoding="utf-8") != updated_content:
            return "错误：修改后核验失败。"

        return f"修改文件成功并已核验：{path}"

    except FileExistsError:
        return "错误：临时文件已存在，请检查后重试。"
    except (ValueError, OSError, UnicodeError) as exc:
        return f"错误：修改文件失败：{exc}"



def run_tests(test_path: str = ".") -> str:
    """运行指定测试文件、测试目录或整个工作区的 pytest。"""
    try:
        target_path = _resolve_path(test_path)
        canonical_target = WORKSPACE_CONTEXT.relative_path(test_path)

        if not target_path.exists():
            return f"错误：测试路径不存在：{test_path}"

        if not target_path.is_file() and not target_path.is_dir():
            return f"错误：测试目标不是文件或目录：{test_path}"

        if target_path.is_file() and target_path.suffix != ".py":
            return f"错误：指定的测试文件必须是 .py 文件：{test_path}"

        command = [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            str(target_path),
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
            cwd=WORKSPACE,
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
        status = "通过" if result.returncode == 0 else "失败"
        error_kind = ""
        if result.returncode != 0:
            lowered = output.lower()
            if (
                "no module named pytest" in lowered
                or "timeout" in lowered
                or "测试执行失败" in lowered
            ):
                error_kind = "测试错误类型：环境错误\n"
            elif (
                "partially initialized module" in lowered
                or "most likely due to a circular import" in lowered
                or "importerror:" in lowered
                or "modulenotfounderror:" in lowered
            ):
                error_kind = "测试错误类型：代码导入错误\n"
            elif (
                "import file mismatch" in lowered
                or "error collecting" in lowered
            ):
                error_kind = "测试错误类型：收集冲突\n"
            elif "nameerror" in lowered or "syntaxerror" in lowered:
                error_kind = "测试错误类型：测试代码\n"
            else:
                error_kind = "测试错误类型：断言失败\n"

        return (
            f"测试目标：{canonical_target}\n"
            f"测试退出码：{result.returncode}\n"
            f"测试结果：{status}\n"
            f"{error_kind}"
            f"{output}"
        )

    except subprocess.TimeoutExpired:
        return f"错误：测试超过 {TEST_TIMEOUT} 秒，已超时终止。"
    except (ValueError, OSError) as exc:
        return f"错误：测试执行失败：{exc}"


def _confirm_delete(path: Path) -> bool:
    """逐项请求用户确认；非交互环境或确认失败时默认拒绝。"""
    try:
        if not sys.stdin.isatty():
            return False

        answer = input(
            f"确认删除 {path}？此操作不可撤销。请输入 y 确认："
        ).strip()

        return answer.lower() == "y"
    except (EOFError, OSError):
        return False


def delete_file(path: str) -> str:
    """删除工作区内的指定文件，必须经过用户确认。"""
    try:
        target = _resolve_path(path)

        if not target.exists() and not target.is_symlink():
            return f"错误：文件不存在：{path}"

        if target.is_symlink():
            return f"错误：不允许删除符号链接：{path}"

        if not target.is_file():
            return f"错误：目标不是普通文件：{path}"

        if not _confirm_delete(target):
            return f"已取消删除：{path}"

        # 确认后再次检查路径，防止确认期间路径指向发生变化。
        verified = _resolve_path(path)
        if verified != target or verified.is_symlink() or not verified.is_file():
            return f"错误：目标路径状态发生变化，已拒绝删除：{path}"

        verified.unlink()
        return f"删除文件成功：{path}"

    except (ValueError, OSError) as exc:
        return f"错误：删除文件失败：{exc}"


def delete_directory(path: str) -> str:
    """逐项确认删除目录内容，最后单独确认删除目录本身。"""
    try:
        target = _resolve_path(path)

        if not target.exists():
            return f"错误：目录不存在：{path}"

        if target.is_symlink():
            return f"错误：不允许删除符号链接：{path}"

        if not target.is_dir():
            return f"错误：目标不是目录：{path}"

        if target == WORKSPACE:
            return "错误：不允许删除整个工作区根目录。"

        results = []

        # 先取得当前目录项快照，按名称排序，方便测试和阅读输出。
        try:
            children = sorted(target.iterdir(), key=lambda item: item.name)
        except OSError as exc:
            return f"错误：无法读取目录：{exc}"

        for child in children:
            relative_path = child.relative_to(WORKSPACE).as_posix()

            # 不跟随符号链接，避免越界访问或删除链接目标。
            if child.is_symlink():
                results.append(
                    f"跳过符号链接：{relative_path}"
                )
                continue

            if child.is_dir():
                # 递归调用同一删除逻辑，子目录本身也会单独确认。
                result = delete_directory(relative_path)
                results.append(result)
            elif child.is_file():
                result = delete_file(relative_path)
                results.append(result)
            else:
                results.append(
                    f"跳过不支持的文件类型：{relative_path}"
                )

        # 所有子项目处理完毕后，才单独确认当前目录。
        if not _confirm_delete(target):
            results.append(f"已取消删除目录：{path}")
            return "\n".join(results)

        # 不使用 shutil.rmtree；仅删除空目录。
        verified = _resolve_path(path)

        if verified != target or verified.is_symlink() or not verified.is_dir():
            results.append(f"错误：目录状态发生变化，已拒绝删除：{path}")
            return "\n".join(results)

        try:
            verified.rmdir()
            results.append(f"删除目录成功：{path}")
        except OSError as exc:
            results.append(
                f"无法删除目录 {path}：目录可能仍有未删除的内容。详情：{exc}"
            )

        return "\n".join(results)

    except (ValueError, OSError) as exc:
        return f"错误：删除目录失败：{exc}"


def execute_tool(name: str, arguments: dict[str, Any]) -> str:
    """根据模型返回的工具名称和参数，执行对应工具。"""

    try:
        if name == "list_files":
            return list_files(**arguments)

        if name == "read_file":
            return read_file(**arguments)

        if name == "search_code":
            return search_code(**arguments)

        if name == "create_directory":
            return create_directory(**arguments)

        if name == "create_file":
            return create_file(**arguments)

        if name == "edit_file":
            return edit_file(**arguments)

        if name == "run_tests":
            return run_tests(**arguments)
        
        if name == "delete_file":
            return delete_file(**arguments)

        if name == "delete_directory":
            return delete_directory(**arguments)

        return f"错误：不支持的工具：{name}"

    except TypeError as exc:
        return f"错误：工具参数不正确：{exc}"
    except Exception as exc:  # noqa: BLE001 - 旧工具分发入口的异常边界
        return f"错误：工具执行异常：{exc}"
