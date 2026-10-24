"""覆盖计划可执行性校验的六条规则。"""

from app.core.intent import AcceptanceItem, EvidenceKind, TaskIntent
from app.core.plan_lint import MAX_STEPS, lint_objectives, phase_of


def _intent(*items: AcceptanceItem, out_of_scope: tuple[str, ...] = ()) -> TaskIntent:
    """构造只用于校验的意图对象。"""
    return TaskIntent(
        objective="目标",
        out_of_scope=out_of_scope,
        acceptance=tuple(items),
    )


def _item(item_id: int, kind: EvidenceKind, target: str | None = None) -> AcceptanceItem:
    """构造一条验收项。"""
    return AcceptanceItem(item_id=item_id, text="验收", kind=kind, target=target)


def test_verbless_fragment_is_merged_into_previous_step():
    """L1：不含动作词的片段并入前一步骤。"""
    result = lint_objectives(
        ["实现 calculator.py", "divide 在 b 为 0 时抛出 ZeroDivisionError"],
        _intent(),
    )

    assert result.objectives == (
        "实现 calculator.py；divide 在 b 为 0 时抛出 ZeroDivisionError",
    )
    assert result.issues[0].rule == "L1"


def test_duplicate_steps_are_merged():
    """L3：重复步骤只保留一条。"""
    result = lint_objectives(["实现功能", "实现功能"], _intent())

    assert result.objectives == ("实现功能",)
    assert result.issues[0].rule == "L3"


def test_missing_test_execution_step_is_added():
    """L4：有测试通过要求但缺少运行步骤时补齐。"""
    result = lint_objectives(
        ["实现功能"], _intent(_item(1, EvidenceKind.TEST_PASSED))
    )

    assert any(phase_of(objective) == "test_run" for objective in result.objectives)
    assert result.issues[-1].rule == "L4"


def test_missing_read_step_is_added():
    """L4：有阅读要求但缺少阅读步骤时补齐。"""
    result = lint_objectives(
        ["实现功能"], _intent(_item(1, EvidenceKind.WORKSPACE_READ))
    )

    assert result.objectives[0].startswith("阅读")


def test_missing_implement_step_is_added():
    """L4：有文件要求但只有测试步骤时补齐实现阶段。"""
    result = lint_objectives(
        ["运行测试"], _intent(_item(1, EvidenceKind.FILE_EXISTS, "a.py"))
    )

    assert any(phase_of(objective) == "implement" for objective in result.objectives)


def test_out_of_scope_steps_are_removed():
    """L6：触碰范围外内容的步骤被移除。"""
    result = lint_objectives(
        ["实现功能", "重构旧模块"],
        _intent(out_of_scope=("重构旧模块",)),
    )

    assert result.objectives == ("实现功能",)
    assert any(issue.rule == "L6" for issue in result.issues)


def test_step_count_is_capped():
    """L5：实现阶段过多时合并，最终不超过上限。"""
    objectives = [f"实现模块{i}" for i in range(7)]

    result = lint_objectives(objectives, _intent())

    assert len(result.objectives) <= MAX_STEPS
    assert any(issue.rule == "L5" for issue in result.issues)
