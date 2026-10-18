"""工具能力：内置文件工具、注册校验、Git 查询和安全恢复。"""

from app.tools import tools as _builtin
from app.tools.tools import *


def set_workspace(context):
    """切换内置工具使用的工作区，并同步包级兼容属性。"""

    _builtin.set_workspace(context)
    globals()["WORKSPACE_CONTEXT"] = _builtin.WORKSPACE_CONTEXT
    globals()["WORKSPACE"] = _builtin.WORKSPACE
