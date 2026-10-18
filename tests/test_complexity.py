from pathlib import Path

import pytest

from app.core.complexity import (
    ComplexityDecision,
    TaskComplexity,
    TaskComplexityJudge,
    judge_task_complexity,
)
from app.runtime.explorer import ProjectExplorer
from app.runtime.workspace import WorkspaceContext


def test_simple_task_does_not_require_planning():
    decision = judge_task_complexity("读取 app.runtime.config.py")

    assert isinstance(decision, ComplexityDecision)
    assert decision.complexity is TaskComplexity.SIMPLE
    assert decision.requires_planning is False
    assert decision.to_dict()["complexity"] == "simple"


@pytest.mark.parametrize(
    "task",
    [
        "先读取配置，然后修改模型调用并运行测试",
        "重构整个项目的工具系统，兼容旧接口并补充测试",
        "inspect files and then refactor code and run tests",
    ],
)
def test_multi_step_tasks_require_planning(task):
    decision = judge_task_complexity(task)

    assert decision.complexity is TaskComplexity.COMPLEX
    assert decision.requires_planning is True
    assert decision.reasons


def test_large_multilanguage_snapshot_can_raise_complexity(tmp_path: Path):
    (tmp_path / "app.py").write_text("", encoding="utf-8")
    (tmp_path / "main.go").write_text("", encoding="utf-8")
    for index in range(81):
        (tmp_path / f"file_{index}.txt").write_text("", encoding="utf-8")

    snapshot = ProjectExplorer(WorkspaceContext.from_path(tmp_path)).explore()
    decision = TaskComplexityJudge().judge("查看项目", snapshot)

    assert decision.complexity is TaskComplexity.COMPLEX
    assert "项目结构较大" in decision.reasons
    assert "项目包含多种主要语言" in decision.reasons


@pytest.mark.parametrize("task", ["", "   ", None])
def test_empty_task_is_rejected(task):
    with pytest.raises(ValueError, match="任务文本不能为空"):
        judge_task_complexity(task)


def test_custom_threshold_can_be_used():
    judge = TaskComplexityJudge(complex_score=4)

    decision = judge.judge("先读取配置，然后修改文件")

    assert decision.complexity is TaskComplexity.SIMPLE
