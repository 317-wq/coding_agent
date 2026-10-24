"""工具规格、注册表及默认工具集合的构建逻辑。"""

from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.runtime.workspace import WorkspaceContext
from app.tools import git_tools, tools
from app.tools.authorization import ConsoleAuthorizer, ToolAuthorizer
from app.tools.git_tools import GitReadTools
from app.tools.result import ToolResult
from app.tools.validation import ToolArgumentValidationError, validate_tool_arguments

ToolExecutor = Callable[..., str | ToolResult]
AuthorizationDescriber = Callable[[dict[str, Any]], dict[str, Any]]
"""高风险工具的可选说明生成器：把执行参数扩展为授权时展示的清单。"""


class RiskLevel(StrEnum):
    """工具执行前需要考虑的风险等级。

    只保留 LOW 与 HIGH：此前的 MEDIUM 没有任何行为分支，属于装饰性枚举。
    """

    LOW = "low"
    HIGH = "high"


@dataclass(frozen=True)
class ToolSpec:
    """一个工具的完整注册信息。"""

    name: str
    description: str
    parameters: Mapping[str, Any]
    executor: ToolExecutor
    risk_level: RiskLevel = RiskLevel.LOW
    describe_authorization: AuthorizationDescriber | None = None

    def to_model_definition(self) -> dict[str, Any]:
        """生成 OpenAI 兼容的函数工具定义。"""

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": deepcopy(dict(self.parameters)),
            },
        }


class ToolRegistry:
    """集中保存工具元数据，并提供统一查找和执行入口。"""

    def __init__(self, authorizer: ToolAuthorizer | None = None) -> None:
        self._tools: dict[str, ToolSpec] = {}
        self.authorizer = authorizer or ConsoleAuthorizer()

    def register(self, spec: ToolSpec) -> None:
        """注册一个工具；拒绝空名称和重复名称。"""

        if not spec.name.strip():
            raise ValueError("工具名称不能为空。")
        if not spec.description.strip():
            raise ValueError(f"工具描述不能为空：{spec.name}")
        if spec.name in self._tools:
            raise ValueError(f"工具已注册：{spec.name}")

        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        """按名称查找工具。"""

        return self._tools.get(name)

    def names(self) -> tuple[str, ...]:
        """按注册顺序返回所有工具名称。"""

        return tuple(self._tools)

    def model_definitions(self) -> list[dict[str, Any]]:
        """返回供模型调用的全部工具定义。"""

        return [spec.to_model_definition() for spec in self._tools.values()]

    def execute(
        self,
        name: str,
        arguments: object,
        *,
        event_recorder: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> ToolResult:
        """通过注册表查找并执行工具；高风险工具在此统一授权。"""

        spec = self.get(name)
        if spec is None:
            return ToolResult.failure(f"不支持的工具：{name}")

        try:
            validated_arguments = validate_tool_arguments(
                spec.parameters, arguments
            )
        except ToolArgumentValidationError as exc:
            return ToolResult.failure(f"工具参数校验失败：{exc}")

        if spec.risk_level is RiskLevel.HIGH:
            authorization_arguments = validated_arguments
            if spec.describe_authorization is not None:
                try:
                    authorization_arguments = spec.describe_authorization(
                        validated_arguments
                    )
                except Exception as exc:  # noqa: BLE001 - 授权说明必须失败关闭
                    return ToolResult.failure(f"无法生成授权说明：{exc}")
            try:
                decision = self.authorizer.authorize(
                    spec.name, authorization_arguments
                )
            except Exception as exc:  # noqa: BLE001 - 授权边界必须失败关闭
                self._record_authorization(
                    event_recorder,
                    spec.name,
                    authorization_arguments,
                    approved=False,
                    reason=str(exc),
                )
                return ToolResult.failure(f"工具授权检查失败：{exc}")

            self._record_authorization(
                event_recorder,
                spec.name,
                authorization_arguments,
                approved=decision.approved,
                reason=decision.reason,
            )
            if not decision.approved:
                return ToolResult.cancelled(
                    f"已取消执行高风险工具 {spec.name}：{decision.reason}"
                )

        try:
            output = spec.executor(**validated_arguments)
        except TypeError as exc:
            return ToolResult.failure(f"工具参数不正确：{exc}")
        except Exception as exc:  # noqa: BLE001 - 注册表是工具异常边界
            return ToolResult.failure(f"工具执行异常：{exc}")

        return self._normalize_output(output)

    @staticmethod
    def _record_authorization(
        recorder: Callable[[str, dict[str, Any]], None] | None,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        approved: bool,
        reason: str,
    ) -> None:
        """把授权决定写入调用方提供的事件记录器。"""
        if recorder is None:
            return
        recorder(
            "tool_authorization_decided",
            tool=tool_name,
            approved=approved,
            reason=reason,
            arguments=arguments,
        )

    @staticmethod
    def _normalize_output(output: str | ToolResult) -> ToolResult:
        if isinstance(output, ToolResult):
            return output
        if not isinstance(output, str):
            return ToolResult.failure("工具返回了不支持的结果类型。")
        if output.startswith("错误："):
            return ToolResult.failure(output.removeprefix("错误："))
        if any(line.startswith("已取消") for line in output.splitlines()):
            return ToolResult.cancelled(output)
        if any(line.startswith("无法删除目录") for line in output.splitlines()):
            return ToolResult.failure(output)
        return ToolResult.success(output)


TOOL_RISK_LEVELS = {
    "delete_directory": RiskLevel.HIGH,
    "delete_file": RiskLevel.HIGH,
}


def build_default_registry(workspace: WorkspaceContext) -> ToolRegistry:
    """按受控工作区建立内置工具注册表。

    工具实例绑定 workspace，注册表只保存规格与执行入口，因此同一进程可以
    为不同工作区各建一个注册表并行执行。
    """

    registry = ToolRegistry()
    builtin = tools.WorkspaceTools(workspace)
    git_reader = GitReadTools(workspace)
    describers: dict[str, AuthorizationDescriber] = {
        "delete_directory": builtin.describe_directory_deletion,
    }
    git_executors = {
        "git_status": git_reader.status,
        "git_diff": git_reader.diff,
    }

    for definition in tools.TOOL_DEFINITIONS:
        function = definition["function"]
        name = function["name"]
        registry.register(
            ToolSpec(
                name=name,
                description=function["description"],
                parameters=function["parameters"],
                executor=getattr(builtin, name),
                risk_level=TOOL_RISK_LEVELS.get(name, RiskLevel.LOW),
                describe_authorization=describers.get(name),
            )
        )

    for definition in git_tools.TOOL_DEFINITIONS:
        function = definition["function"]
        name = function["name"]
        registry.register(
            ToolSpec(
                name=name,
                description=function["description"],
                parameters=function["parameters"],
                executor=git_executors[name],
            )
        )

    return registry
