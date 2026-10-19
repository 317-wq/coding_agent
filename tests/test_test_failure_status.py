from pathlib import Path

import pytest

from app import tools
from app.runtime.workspace import WorkspaceContext
from app.tools.registry import ToolRegistry, ToolSpec
from app.tools.result import ToolStatus


@pytest.fixture(autouse=True)
def restore_workspace():
    yield
    tools.set_workspace(WorkspaceContext.from_path(tools.DEFAULT_WORKSPACE))


def test_run_tests_failure_becomes_structured_failure(tmp_path: Path):
    test_file = tmp_path / "test_failure.py"
    test_file.write_text(
        "def test_failure():\n    assert False\n", encoding="utf-8"
    )
    tools.set_workspace(WorkspaceContext.from_path(tmp_path))
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="run_tests",
            description="运行测试",
            parameters={
                "type": "object",
                "properties": {"test_path": {"type": "string"}},
                "required": [],
                "additionalProperties": False,
            },
            executor=tools.run_tests,
        )
    )

    result = registry.execute("run_tests", {"test_path": "test_failure.py"})

    assert result.status is ToolStatus.FAILURE


def test_run_tests_marks_pytest_collection_conflict(tmp_path: Path):
    for folder in (tmp_path / "a", tmp_path / "b"):
        folder.mkdir()
        (folder / "test_same.py").write_text(
            "def test_ok():\n    assert True\n", encoding="utf-8"
        )
    tools.set_workspace(WorkspaceContext.from_path(tmp_path))
    result = tools.run_tests(".")
    assert "测试错误类型：收集冲突" in result
    assert "测试结果：失败" in result


def test_name_error_in_test_code_is_classified_for_repair(tmp_path: Path):
    test_file = tmp_path / "test_calculator.py"
    test_file.write_text(
        "def test_approx():\n    assert 1 == pytest.approx(1)\n",
        encoding="utf-8",
    )
    tools.set_workspace(WorkspaceContext.from_path(tmp_path))

    result = tools.run_tests("test_calculator.py")

    assert "测试错误类型：测试代码" in result


def test_list_files_accepts_workspace_absolute_path(tmp_path: Path):
    tools.set_workspace(WorkspaceContext.from_path(tmp_path))
    (tmp_path / "calculator.py").write_text("VALUE = 1\n", encoding="utf-8")

    result = tools.list_files(str(tmp_path))

    assert "calculator.py" in result
    assert not result.startswith("错误：")


def test_list_and_run_tests_preserve_exact_python_filename(tmp_path: Path):
    """验证列举和测试工具使用同一个带扩展名的规范相对路径。"""
    tools.set_workspace(WorkspaceContext.from_path(tmp_path))
    tests = tmp_path / "tests"
    tests.mkdir()
    target = tests / "test_completion_probe.py"
    target.write_text("def test_ok():\n    assert True\n", encoding="utf-8")

    listing = tools.list_files("tests")
    result = tools.run_tests(str(target))

    assert "文件: tests/test_completion_probe.py" in listing
    assert "测试目标：tests/test_completion_probe.py" in result
    assert "测试结果：通过" in result
