import json
from dataclasses import dataclass
from enum import StrEnum


class ToolStatus(StrEnum):
    """一次工具执行的统一结束状态。"""

    SUCCESS = "success"
    FAILURE = "failure"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class ToolResult:
    """工具执行后返回给调用方的结构化结果。"""

    status: ToolStatus
    result: str | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        if self.status is ToolStatus.SUCCESS and self.error is not None:
            raise ValueError("成功结果不能包含错误信息。")
        if self.status is ToolStatus.FAILURE and not self.error:
            raise ValueError("失败结果必须包含错误信息。")
        if self.status is ToolStatus.CANCELLED and self.error is not None:
            raise ValueError("取消结果不能包含错误信息。")

    @classmethod
    def success(cls, result: str = "") -> "ToolResult":
        return cls(status=ToolStatus.SUCCESS, result=result)

    @classmethod
    def failure(cls, error: str) -> "ToolResult":
        return cls(
            status=ToolStatus.FAILURE,
            error=error or "工具执行失败，但未提供错误信息。",
        )

    @classmethod
    def cancelled(cls, result: str = "操作已取消。") -> "ToolResult":
        return cls(status=ToolStatus.CANCELLED, result=result)

    def to_dict(self) -> dict[str, str | None]:
        """返回字段稳定、可序列化的字典。"""

        return {
            "status": self.status.value,
            "result": self.result,
            "error": self.error,
        }

    def to_json(self) -> str:
        """序列化为可回传模型的 JSON 文本。"""

        return json.dumps(self.to_dict(), ensure_ascii=False)
