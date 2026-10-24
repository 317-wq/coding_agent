"""覆盖意图抽取：验收项、范围、范围外与待确认。"""

import pytest

from app.core.intent import EvidenceKind, extract_intent

REAL_TASK = (
    "实现 calculator.py，提供 add(a, b) 和 divide(a, b) 两个函数；"
    "divide 在 b 为 0 时抛出 ZeroDivisionError。"
    "然后编写 tests/test_calculator.py 覆盖这两个函数，最后运行测试确认通过。"
)


def test_extracts_acceptance_from_explicit_requirements():
    """验证文件、符号与测试要求都转成验收项。"""
    intent = extract_intent(REAL_TASK)
    kinds = [item.kind for item in intent.acceptance]

    assert kinds.count(EvidenceKind.FILE_EXISTS) == 2
    assert EvidenceKind.TEST_PASSED in kinds
    assert EvidenceKind.CONTENT_CONTAINS in kinds
    targets = {
        item.target
        for item in intent.acceptance
        if item.kind is EvidenceKind.FILE_EXISTS
    }
    assert targets == {"calculator.py", "tests/test_calculator.py"}


def test_test_passed_targets_the_test_file():
    """验证测试验收项绑定到显式测试路径。"""
    intent = extract_intent(REAL_TASK)
    item = next(
        item for item in intent.acceptance if item.kind is EvidenceKind.TEST_PASSED
    )

    assert item.target == "tests/test_calculator.py"


def test_extracts_scope_and_out_of_scope():
    """验证范围与明确不做被分别抽取。"""
    intent = extract_intent("检查 calculator.py，修复循环导入。不要修改测试断言。")

    assert intent.scope == ("calculator.py",)
    assert any("测试断言" in item for item in intent.out_of_scope)


def test_writing_tests_does_not_require_running_them():
    """验证“补充 pytest 测试”不会被当成必须运行测试。"""
    intent = extract_intent("创建 calculator.py，并补充 pytest 测试。")

    assert all(item.kind is not EvidenceKind.TEST_PASSED for item in intent.acceptance)


def test_open_questions_only_for_explicit_choices():
    """验证只有显式二选一才生成待确认。"""
    assert extract_intent("实现 A 还是 B？").open_questions
    assert extract_intent("实现 calculator.py").open_questions == ()


def test_empty_task_is_rejected():
    """验证空任务被拒绝。"""
    with pytest.raises(ValueError, match="任务文本不能为空"):
        extract_intent(" ")
