"""高风险工具操作的授权策略与交互式授权器。"""

import sys
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class AuthorizationDecision:
    """高风险工具执行前的授权决定。"""

    approved: bool
    reason: str

    @classmethod
    def allow(cls, reason: str = "用户已授权。") -> "AuthorizationDecision":
        return cls(approved=True, reason=reason)

    @classmethod
    def deny(cls, reason: str = "用户未授权。") -> "AuthorizationDecision":
        return cls(approved=False, reason=reason)


class ToolAuthorizer(Protocol):
    """工具授权策略需要实现的最小接口。"""

    def authorize(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> AuthorizationDecision: ...


class AutoApproveAuthorizer:
    """自动授权所有高风险工具，仅用于非交互演示或受控环境。"""

    def authorize(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> AuthorizationDecision:
        return AuthorizationDecision.allow("已按 --yes 自动授权。")


class ConsoleAuthorizer:
    """通过终端输入 y 对高风险工具进行明确授权。"""

    def authorize(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> AuthorizationDecision:
        if not sys.stdin.isatty():
            return AuthorizationDecision.deny("非交互环境默认拒绝授权。")

        try:
            answer = input(
                f"高风险工具 {tool_name} 将以参数 {arguments} 执行。"
                "请输入 y 确认："
            ).strip()
        except (EOFError, OSError):
            return AuthorizationDecision.deny("无法读取用户授权。")

        if answer.lower() == "y":
            return AuthorizationDecision.allow()
        return AuthorizationDecision.deny()
