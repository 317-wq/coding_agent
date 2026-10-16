import os
import subprocess
import sys
from pathlib import Path
from typing import Any


from app.workspace import WorkspaceContext


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_WORKSPACE = (PROJECT_ROOT / "workspace" / "demo").resolve()
WORKSPACE = DEFAULT_WORKSPACE
WORKSPACE_CONTEXT = WorkspaceContext(root=WORKSPACE)

MAX_FILE_SIZE = 1024 * 1024
TEST_TIMEOUT = 30
MAX_LIST_ENTRIES = 500

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
            "description": (
                "列出工作目录中的文件和目录。默认递归遍历所有子目录，"
                "可以通过 recursive=false 只查看当前目录。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "相对于工作目录的目录路径，默认为 .",
                    },
                    "recursive": {
                        "type": "boolean",
                        "description": "是否递归列出子目录，默认为 true。",
                    },
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
            "description": "读取工作目录中的文本文件，包括空文件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_directory",
            "description": (
                "在工作目录中创建新目录。如果中间父目录不存在，"
                "会一并创建。不能覆盖已有的普通文件。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "相对于工作目录的目录路径。",
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_file",
            "description": (
                "创建新文件并写入完整内容。目标文件必须不存在，"
                "父目录必须已经存在。不能覆盖已有文件。"
            ),
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
            "description": (
                "修改已有文件中的文本。旧文本必须恰好出现一次。"
            ),
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
            "description": "使用 pytest 执行工作目录中的测试文件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "test_path": {
                        "type": "string",
                        "description": "测试文件路径，默认为 test_main.py。",
                    },
                },
                "required": [],
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
    """解析路径，并拒绝访问工作目录之外的位置。"""

    return WORKSPACE_CONTEXT.resolve(path)


def list_files(path: str = ".", recursive: bool = True) -> str:
    """列出指定目录中的文件和子目录，可选择递归遍历。"""

    try:
        directory = _resolve_path(path)

        if not directory.is_dir():
            return f"错误：目录不存在：{path}"

        entries: list[str] = []
        stack = [directory]

        while stack and len(entries) < MAX_LIST_ENTRIES:
            current = stack.pop()

            # 先排序，保证结果顺序稳定
            children = sorted(
                current.iterdir(),
                key=lambda item: item.name,
                reverse=True,
            )

            for entry in children:
                if entry.name in IGNORED_DIRS:
                    continue

                # 不跟随符号链接，避免遍历到工作区外部
                if entry.is_symlink():
                    continue

                relative_path = entry.relative_to(WORKSPACE)

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
            result += (
                f"\n结果已达到 {MAX_LIST_ENTRIES} 条上限，"
                "如需查看具体子目录，请缩小查询范围。"
            )

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

        # parents=True：自动创建中间不存在的父目录
        # exist_ok=False：目标已存在时不静默成功
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

        # x 模式保证不会覆盖已有文件
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

        return f"修改文件成功：{path}"

    except FileExistsError:
        return "错误：临时文件已存在，请检查后重试。"
    except (ValueError, OSError, UnicodeError) as exc:
        return f"错误：修改文件失败：{exc}"


def run_tests(test_path: str = "test_main.py") -> str:
    """通过固定命令运行 pytest。"""

    try:
        file_path = _resolve_path(test_path)

        if not file_path.is_file():
            return f"错误：测试文件不存在：{test_path}"

        command = [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            str(file_path),
        ]

        child_env = os.environ.copy()

        for key in list(child_env):
            if any(
                token in key.upper()
                for token in ("API_KEY", "API_TOKEN", "ACCESS_TOKEN", "SECRET")
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

        return (
            f"测试退出码：{result.returncode}\n"
            f"测试结果：{status}\n"
            f"{output}"
        )

    except subprocess.TimeoutExpired:
        return f"错误：测试超过 {TEST_TIMEOUT} 秒，已超时终止。"
    except (ValueError, OSError) as exc:
        return f"错误：测试执行失败：{exc}"


def execute_tool(name: str, arguments: dict[str, Any]) -> str:
    """根据模型返回的工具名称和参数，执行对应工具。"""

    try:
        if name == "list_files":
            return list_files(**arguments)

        if name == "read_file":
            return read_file(**arguments)

        if name == "create_directory":
            return create_directory(**arguments)

        if name == "create_file":
            return create_file(**arguments)

        if name == "edit_file":
            return edit_file(**arguments)

        if name == "run_tests":
            return run_tests(**arguments)

        return f"错误：不支持的工具：{name}"

    except TypeError as exc:
        return f"错误：工具参数不正确：{exc}"
    except Exception as exc:
        return f"错误：工具执行异常：{exc}"