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
