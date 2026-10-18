import json

import pytest

from app.core.context import ContextManager
from app.core.planner import create_task_plan
from app.tools.result import ToolResult


def test_context_keeps_task_plan_and_results_serializable():
    manager = ContextManager("修改代码", plan=create_task_plan("修改代码"))
    manager.update_plan(manager.context.plan, current_step_id=1)
    manager.add_tool_result(ToolResult.success("文件已写入"))
    manager.add_test_result("测试结果：通过")

    context = manager.context
    restored = json.loads(context.to_json())
    assert restored == context.to_dict()
    assert context.task == "修改代码"
    assert context.current_step_id == 1
    assert context.test_results == ("测试结果：通过",)


def test_context_trims_old_successes_but_keeps_failure():
    manager = ContextManager("执行任务", max_entries=2)
    manager.add_tool_result(ToolResult.success("旧成功"))
    manager.add_tool_result(ToolResult.failure("关键失败"))
    manager.add_tool_result(ToolResult.success("新成功"))

    results = manager.context.tool_results
    assert len(results) == 2
    assert any(result.error == "关键失败" for result in results)
    assert manager.context.truncated_count == 1


def test_context_respects_character_limit():
    manager = ContextManager("短任务", max_entries=10, max_chars=300)
    for index in range(5):
        manager.add_test_result(f"测试结果：{'x' * 100}-{index}")

    assert len(manager.context.to_json()) <= 300
    assert manager.context.truncated_count > 0


def test_context_validates_inputs():
    with pytest.raises(ValueError, match="任务文本不能为空"):
        ContextManager(" ")
    with pytest.raises(ValueError, match="max_entries"):
        ContextManager("任务", max_entries=0)
    manager = ContextManager("任务")
    with pytest.raises(TypeError, match="ToolResult"):
        manager.add_tool_result("错误")
    with pytest.raises(ValueError, match="测试结果不能为空"):
        manager.add_test_result(" ")
