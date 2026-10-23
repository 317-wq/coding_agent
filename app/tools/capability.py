"""工具能力类别：步骤匹配与完成核验只依赖稳定的工具语义。

能力类别属于工具元数据，由工具层维护。Agent 核心按类别判断“这类操作能证明
哪种开发动作已完成”，不再按工具名称硬编码集合，也不再对未登记工具做兜底匹配。
未登记工具一律视为 ``UNKNOWN``，不参与任何步骤状态流转，避免新增工具时遗漏
登记导致实现步骤被只读操作误判完成。
"""

from enum import StrEnum


class ToolCapability(StrEnum):
    """一个工具在不依赖模型措辞时也能确定的稳定能力类别。"""

    READ = "read"
    """只读取工作区内容，例如列目录、读文件、搜索代码。"""

    MUTATE = "mutate"
    """改变工作区文件或目录，例如创建、编辑、删除。"""

    TEST = "test"
    """执行测试并产生可核验的测试证据。"""

    VCS = "vcs"
    """只读版本控制查询，例如查看状态和差异。"""

    UNKNOWN = "unknown"
    """未登记能力类别；不匹配也不能完成任何计划步骤。"""


TOOL_CAPABILITIES: dict[str, ToolCapability] = {
    "list_files": ToolCapability.READ,
    "read_file": ToolCapability.READ,
    "search_code": ToolCapability.READ,
    "create_directory": ToolCapability.MUTATE,
    "create_file": ToolCapability.MUTATE,
    "edit_file": ToolCapability.MUTATE,
    "delete_file": ToolCapability.MUTATE,
    "delete_directory": ToolCapability.MUTATE,
    "run_tests": ToolCapability.TEST,
    "git_status": ToolCapability.VCS,
    "git_diff": ToolCapability.VCS,
}


def capability_of(tool_name: str) -> ToolCapability:
    """返回工具的能力类别；未登记或名称非法时返回 ``UNKNOWN``。"""
    if not isinstance(tool_name, str):
        return ToolCapability.UNKNOWN
    return TOOL_CAPABILITIES.get(tool_name, ToolCapability.UNKNOWN)


def is_registered_tool(tool_name: str) -> bool:
    """判断工具是否已经登记能力类别。"""
    return capability_of(tool_name) is not ToolCapability.UNKNOWN
