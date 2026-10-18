"""Coding Agent 应用包，并保留常用入口的兼容导出。"""

from app.cli import main
from app.tools import git_tools

__all__ = ["git_tools", "main"]
