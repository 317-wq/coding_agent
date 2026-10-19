"""识别影响 Agent 执行策略的任务级意图。"""

import re

_VALIDATION_SUBJECT_PATTERN = re.compile(
    r"安全边界|完成状态|路径越界|越界路径|只尝试一次|仅验证|仅检查|验证是否|检查是否",
    re.IGNORECASE,
)
_DEVELOPMENT_ACTION_PATTERN = re.compile(
    r"实现|开发|新增|添加|创建|修改|更新|修复|删除|重构|迁移|编写(?:代码|功能|模块|项目)",
    re.IGNORECASE,
)


def is_validation_only_task(task: str) -> bool:
    """仅在任务目标本身是一次性检查且不含开发动作时返回真。"""
    if not isinstance(task, str) or not task.strip():
        return False
    if _DEVELOPMENT_ACTION_PATTERN.search(task):
        return False
    return bool(_VALIDATION_SUBJECT_PATTERN.search(task))
