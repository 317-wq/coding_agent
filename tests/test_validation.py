import pytest

from app.registry import ToolRegistry, ToolSpec
from app.result import ToolStatus
from app.validation import ToolArgumentValidationError, validate_tool_arguments

SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string"},
        "recursive": {"type": "boolean"},
    },
    "required": ["path"],
    "additionalProperties": False,
}


def test_validator_accepts_valid_required_and_optional_arguments():
    arguments = {"path": "src", "recursive": False}

    assert validate_tool_arguments(SCHEMA, arguments) is arguments


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({}, "缺少必填参数：path"),
        ({"path": "src", "extra": 1}, "存在未知参数：extra"),
        ({"path": "src", "recursive": "yes"}, "需要 布尔值"),
        (["src"], "工具参数必须是 JSON 对象"),
    ],
)
def test_validator_rejects_invalid_arguments(arguments, message):
    with pytest.raises(ToolArgumentValidationError, match=message):
        validate_tool_arguments(SCHEMA, arguments)


def test_validator_checks_nested_objects_and_arrays():
    schema = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"count": {"type": "integer"}},
                    "required": ["count"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["items"],
        "additionalProperties": False,
    }

    with pytest.raises(
        ToolArgumentValidationError, match=r"参数\.items\[0\]\.count 类型错误"
    ):
        validate_tool_arguments(schema, {"items": [{"count": True}]})


def test_registry_does_not_call_executor_when_validation_fails():
    calls = []

    def executor(path):
        calls.append(path)
        return "不应执行"

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="read",
            description="读取文件。",
            parameters=SCHEMA,
            executor=executor,
        )
    )

    result = registry.execute("read", {"unexpected": "value"})

    assert result.status is ToolStatus.FAILURE
    assert result.error.startswith("工具参数校验失败：")
    assert calls == []
