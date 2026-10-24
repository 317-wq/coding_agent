"""`run_tests` 的结构化结果。

判定端与报告端只读取这里的字段，不再对测试输出文本做正则解析。文本输出仍然
保留，用于回传给模型和在日志中展示。
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class TestRunStatus(StrEnum):
    """一次测试执行的结论。"""

    __test__ = False  # 名称以 Test 开头，告知 pytest 不要当作测试类收集

    PASSED = "passed"
    FAILED = "failed"


@dataclass(frozen=True)
class TestRunResult:
    """一次 `run_tests` 执行的结构化结果。"""

    __test__ = False  # 名称以 Test 开头，告知 pytest 不要当作测试类收集

    target: str
    exit_code: int | None
    status: TestRunStatus
    error_kind: str | None = None
    output: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.target, str) or not self.target.strip():
            raise ValueError("测试目标不能为空。")
        if self.exit_code is not None and not isinstance(self.exit_code, int):
            raise TypeError("测试退出码必须是整数或 None。")
        if self.status is TestRunStatus.PASSED and self.exit_code not in (0, None):
            raise ValueError("判定通过的测试退出码必须为 0。")

    def to_dict(self) -> dict[str, Any]:
        """返回可序列化的结构化结果，供 Trace 事件携带。"""
        return {
            "target": self.target,
            "exit_code": self.exit_code,
            "status": self.status.value,
            "error_kind": self.error_kind,
            "output": self.output,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "TestRunResult":
        """从 Trace 事件载荷恢复结构化结果。"""
        return cls(
            target=str(payload["target"]),
            exit_code=payload.get("exit_code"),
            status=TestRunStatus(payload["status"]),
            error_kind=payload.get("error_kind"),
            output=str(payload.get("output") or ""),
        )
