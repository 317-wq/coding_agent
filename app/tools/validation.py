"""校验工具参数并报告结构化参数错误。"""

from collections.abc import Mapping
from typing import Any


class ToolArgumentValidationError(ValueError):
    """工具调用参数不符合注册 schema。"""


def validate_tool_arguments(
    schema: Mapping[str, Any], arguments: object
) -> dict[str, Any]:
    """校验一次工具调用参数，并返回经过类型收窄的参数对象。"""

    if not isinstance(arguments, dict):
        raise ToolArgumentValidationError("工具参数必须是 JSON 对象。")

    _validate_value(arguments, schema, "参数")
    return arguments


def _validate_value(value: object, schema: Mapping[str, Any], path: str) -> None:
    allowed_values = schema.get("enum")
    if isinstance(allowed_values, list) and value not in allowed_values:
        choices = "、".join(repr(item) for item in allowed_values)
        raise ToolArgumentValidationError(
            f"{path} 的值无效，可选值为：{choices}"
        )

    expected_type = schema.get("type")
    if isinstance(expected_type, str) and not _matches_type(value, expected_type):
        raise ToolArgumentValidationError(
            f"{path} 类型错误：需要 {_type_name(expected_type)}。"
        )

    if expected_type == "object" and isinstance(value, dict):
        _validate_object(value, schema, path)
    elif expected_type == "array" and isinstance(value, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, Mapping):
            for index, item in enumerate(value):
                _validate_value(item, item_schema, f"{path}[{index}]")


def _validate_object(
    value: dict[str, Any], schema: Mapping[str, Any], path: str
) -> None:
    properties = schema.get("properties", {})
    if not isinstance(properties, Mapping):
        properties = {}

    required = schema.get("required", [])
    if isinstance(required, list):
        missing = [name for name in required if name not in value]
        if missing:
            raise ToolArgumentValidationError(
                f"缺少必填参数：{'、'.join(missing)}"
            )

    unknown = sorted(name for name in value if name not in properties)
    additional_properties = schema.get("additionalProperties", True)
    if unknown and additional_properties is False:
        raise ToolArgumentValidationError(
            f"存在未知参数：{'、'.join(unknown)}"
        )

    for name, item in value.items():
        property_schema = properties.get(name)
        if isinstance(property_schema, Mapping):
            _validate_value(item, property_schema, f"{path}.{name}")
        elif isinstance(additional_properties, Mapping):
            _validate_value(item, additional_properties, f"{path}.{name}")


def _matches_type(value: object, expected_type: str) -> bool:
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected_type == "array":
        return isinstance(value, list)
    if expected_type == "object":
        return isinstance(value, dict)
    if expected_type == "null":
        return value is None
    return True


def _type_name(expected_type: str) -> str:
    names = {
        "array": "数组",
        "boolean": "布尔值",
        "integer": "整数",
        "null": "null",
        "number": "数字",
        "object": "对象",
        "string": "字符串",
    }
    return names.get(expected_type, expected_type)
