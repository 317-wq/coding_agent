"""覆盖工具能力类别登记，防止新增工具被静默判为未登记。"""

from app.runtime.workspace import WorkspaceContext
from app.tools import tools
from app.tools.capability import (
    TOOL_CAPABILITIES,
    ToolCapability,
    capability_of,
    is_registered_tool,
)
from app.tools.registry import build_default_registry


def test_every_registered_tool_declares_capability():
    """验证全部内置工具都登记了能力类别。"""
    registry = build_default_registry(
        WorkspaceContext.from_path(tools.DEFAULT_WORKSPACE)
    )
    missing = [
        name
        for name in registry.names()
        if capability_of(name) is ToolCapability.UNKNOWN
    ]

    assert missing == []


def test_capabilities_follow_tool_semantics():
    """验证能力类别与工具真实作用一致，只读查询不与写操作混淆。"""
    assert capability_of("list_files") is ToolCapability.READ
    assert capability_of("read_file") is ToolCapability.READ
    assert capability_of("search_code") is ToolCapability.READ
    assert capability_of("create_file") is ToolCapability.MUTATE
    assert capability_of("edit_file") is ToolCapability.MUTATE
    assert capability_of("delete_directory") is ToolCapability.MUTATE
    assert capability_of("run_tests") is ToolCapability.TEST
    assert capability_of("git_status") is ToolCapability.VCS
    assert capability_of("git_diff") is ToolCapability.VCS


def test_unregistered_and_invalid_names_are_unknown():
    """验证未登记或非法工具名称不会获得任何能力。"""
    assert capability_of("custom_tool") is ToolCapability.UNKNOWN
    assert capability_of("") is ToolCapability.UNKNOWN
    assert capability_of(None) is ToolCapability.UNKNOWN
    assert is_registered_tool("custom_tool") is False
    assert is_registered_tool("run_tests") is True


def test_capability_mapping_is_well_formed():
    """验证能力映射的键都是非空工具名，且不登记 UNKNOWN 类别。"""
    assert all(isinstance(name, str) and name.strip() for name in TOOL_CAPABILITIES)
    assert ToolCapability.UNKNOWN not in TOOL_CAPABILITIES.values()
