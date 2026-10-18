"""Coding Agent 应用包，并保留常用入口的兼容导出。"""

__all__ = ["git_tools", "main"]


def __getattr__(name: str):
    """按需提供旧入口导出，避免启动 CLI 时提前加载模块。"""

    if name == "main":
        from app.cli import main

        return main
    if name == "git_tools":
        from app.tools import git_tools

        return git_tools
    raise AttributeError(name)
