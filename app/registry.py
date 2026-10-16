from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app import tools
from app.validation import ToolArgumentValidationError, validate_tool_arguments

ToolExecutor = Callable[..., str]


class RiskLevel(StrEnum):
    """工具执行前需要考虑的风险等级。"""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class ToolSpec:
    """一个工具的完整注册信息。"""

    name: str
    description: str
    parameters: Mapping[str, Any]
    executor: ToolExecutor
    risk_level: RiskLevel = RiskLevel.LOW

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

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

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

    def execute(self, name: str, arguments: object) -> str:
        """通过注册表查找并执行工具。"""

        spec = self.get(name)
        if spec is None:
            return f"错误：不支持的工具：{name}"

        try:
            validated_arguments = validate_tool_arguments(
                spec.parameters, arguments
            )
        except ToolArgumentValidationError as exc:
            return f"错误：工具参数校验失败：{exc}"

        try:
            return spec.executor(**validated_arguments)
        except TypeError as exc:
            return f"错误：工具参数不正确：{exc}"
        except Exception as exc:  # noqa: BLE001 - 注册表是工具异常边界
            return f"错误：工具执行异常：{exc}"


TOOL_RISK_LEVELS = {
    "create_directory": RiskLevel.MEDIUM,
    "create_file": RiskLevel.MEDIUM,
    "delete_directory": RiskLevel.HIGH,
    "delete_file": RiskLevel.HIGH,
    "edit_file": RiskLevel.MEDIUM,
    "run_tests": RiskLevel.MEDIUM,
}


def build_default_registry() -> ToolRegistry:
    """根据当前内置工具定义建立默认注册表。"""

    registry = ToolRegistry()

    for definition in tools.TOOL_DEFINITIONS:
        function = definition["function"]
        name = function["name"]
        executor = getattr(tools, name)
        registry.register(
            ToolSpec(
                name=name,
                description=function["description"],
                parameters=function["parameters"],
                executor=executor,
                risk_level=TOOL_RISK_LEVELS.get(name, RiskLevel.LOW),
            )
        )

    return registry


DEFAULT_TOOL_REGISTRY = build_default_registry()
