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


def test_run_tests_distinguishes_missing_tests_from_assertion_failure(tmp_path: Path):
    """验证空测试目录不会被误报为断言失败。"""
    tools.set_workspace(WorkspaceContext.from_path(tmp_path))

    result = tools.run_tests(".")

    assert "测试退出码：5" in result
    assert "测试错误类型：未发现测试" in result
    assert "测试错误类型：断言失败" not in result


def test_run_tests_marks_circular_import_as_repairable_code_error(tmp_path: Path):
    """验证循环导入不会被误判为不可修复的测试环境问题。"""
    (tmp_path / "module_a.py").write_text(
        "from module_b import VALUE_B\nVALUE_A = 1\n",
        encoding="utf-8",
    )
    (tmp_path / "module_b.py").write_text(
        "from module_a import VALUE_A\nVALUE_B = 2\n",
        encoding="utf-8",
    )
    (tmp_path / "test_cycle.py").write_text(
        "from module_a import VALUE_A\n\ndef test_value():\n    assert VALUE_A == 1\n",
        encoding="utf-8",
    )
    tools.set_workspace(WorkspaceContext.from_path(tmp_path))

    result = tools.run_tests("test_cycle.py")

    assert "测试结果：失败" in result
    assert "测试错误类型：代码导入错误" in result
    assert "partially initialized module" in result


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


def test_run_tests_accepts_pytest_node_id(tmp_path: Path):
    """验证测试工具保留文件路径并支持精确到用例的 pytest node id。"""
    tools.set_workspace(WorkspaceContext.from_path(tmp_path))
    target = tmp_path / "test_sample.py"
    target.write_text(
        "class TestSample:\n    def test_ok(self):\n        assert True\n",
        encoding="utf-8",
    )

    result = tools.run_tests("test_sample.py::TestSample::test_ok")

    assert "测试目标：test_sample.py::TestSample::test_ok" in result
    assert "测试退出码：0" in result


def test_edit_file_is_idempotent_when_target_content_already_exists(tmp_path: Path):
    """验证重复执行相同文本替换不会返回失败。"""
    tools.set_workspace(WorkspaceContext.from_path(tmp_path))
    target = tmp_path / "completion_probe.py"
    target.write_text("def probe():\n    return 42\n", encoding="utf-8")

    first = tools.edit_file(
        "completion_probe.py", "    return 42", "    return 43"
    )
    second = tools.edit_file(
        "completion_probe.py", "    return 42", "    return 43"
    )

    assert "修改文件成功" in first
    assert "无需修改" in second
    assert target.read_text(encoding="utf-8").count("return 43") == 1


def test_create_file_reports_verified_disk_content(tmp_path: Path):
    """验证创建工具只有在磁盘内容与请求一致时才报告已核验成功。"""
    tools.set_workspace(WorkspaceContext.from_path(tmp_path))

    result = tools.create_file("module.py", "VALUE = 1\n")

    assert result == "创建文件成功并已核验：module.py"
    assert (tmp_path / "module.py").read_text(encoding="utf-8") == "VALUE = 1\n"
