"""测试用的结构化工具结果构造函数。"""

from app.tools.result import ToolResult
from app.tools.test_result import TestRunResult, TestRunStatus


def passed_test(target: str = "tests") -> ToolResult:
    """构造一次带结构化载荷的通过测试结果。"""
    run = TestRunResult(
        target=target,
        exit_code=0,
        status=TestRunStatus.PASSED,
        output="1 passed",
    )
    return ToolResult.success(
        f"测试目标：{target}\n测试退出码：0\n测试结果：通过\n1 passed",
        data=run,
    )
