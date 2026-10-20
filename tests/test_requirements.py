from pathlib import Path

from app.core.requirements import TaskRequirementVerifier
from app.runtime.workspace import WorkspaceContext


def test_verifies_explicit_path_exception_and_pytest_requirements(tmp_path: Path):
    target = tmp_path / "tests" / "test_calculator.py"
    target.parent.mkdir()
    target.write_text(
        "import pytest\n\ndef test_divide():\n    raise ZeroDivisionError\n",
        encoding="utf-8",
    )

    result = TaskRequirementVerifier().verify(
        "使用 tests/test_calculator.py 和 pytest，除数为零时抛出 ZeroDivisionError",
        WorkspaceContext.from_path(tmp_path),
    )

    assert result.passed is True
    assert result.missing == ()


def test_reports_mismatched_explicit_requirements(tmp_path: Path):
    (tmp_path / "test_calculator.py").write_text(
        "import unittest\nclass TestCase(unittest.TestCase): pass\n",
        encoding="utf-8",
    )

    result = TaskRequirementVerifier().verify(
        "使用 tests/test_calculator.py 和 pytest，除数为零时抛出 ZeroDivisionError",
        WorkspaceContext.from_path(tmp_path),
    )

    assert result.passed is False
    assert any("文件不存在" in item for item in result.missing)
    assert any("ZeroDivisionError" in item for item in result.missing)
    assert any("pytest" in item for item in result.missing)


def test_preserves_full_nested_unquoted_python_path(tmp_path: Path):
    """验证未加反引号的嵌套测试路径不会被截断为 tests/ 后缀。"""
    target = tmp_path / "todo_cli" / "tests" / "test_todo_cli.py"
    target.parent.mkdir(parents=True)
    target.write_text("def test_cli(): pass\n", encoding="utf-8")

    result = TaskRequirementVerifier().verify(
        "运行 todo_cli/tests/test_todo_cli.py 并报告结果",
        WorkspaceContext.from_path(tmp_path),
    )

    assert result.passed is True
    assert result.missing == ()


def test_checks_bare_module_names_and_argparse(tmp_path: Path):
    """验证模块清单中的裸文件名和指定 CLI 框架都参与最终核验。"""
    package = tmp_path / "todo_cli"
    package.mkdir()
    (package / "models.py").write_text("class Todo: pass\n", encoding="utf-8")
    (package / "cli.py").write_text("from click import group\n", encoding="utf-8")
    task = """使用 argparse 实现 CLI。\n- `models.py`：模型。\n- `storage.py`：存储。"""

    result = TaskRequirementVerifier().verify(
        task, WorkspaceContext.from_path(tmp_path)
    )

    assert result.passed is False
    assert "明确要求的文件不存在：storage.py" in result.missing
    assert any("argparse" in item for item in result.missing)


def test_accepts_argparse_in_business_source(tmp_path: Path):
    """验证业务模块真实导入 argparse 时满足框架约束。"""
    package = tmp_path / "todo_cli"
    package.mkdir()
    (package / "cli.py").write_text("import argparse\n", encoding="utf-8")

    result = TaskRequirementVerifier().verify(
        "使用 argparse 实现 CLI。\n- `cli.py`：入口。",
        WorkspaceContext.from_path(tmp_path),
    )

    assert result.passed is True
