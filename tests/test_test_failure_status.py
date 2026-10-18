from pathlib import Path

import pytest

from app import tools
from app.registry import ToolRegistry, ToolSpec
from app.result import ToolStatus
from app.workspace import WorkspaceContext


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
    assert "测试结果：失败" in (result.error or "")
