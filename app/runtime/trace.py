"""记录 Agent 执行过程中的事件，并对敏感信息做脱敏。"""

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

MAX_STRING_LENGTH = 2000
MAX_ITEMS = 50
MAX_DEPTH = 5

SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "token",
    "password",
    "passwd",
    "secret",
    "authorization",
    "credential",
    "credentials",
}

SENSITIVE_FILE_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    ".env.development",
}

REDACTED = "[REDACTED]"
TRUNCATED = "...[TRUNCATED]"

SECRET_VALUE_PATTERN = re.compile(
    r"""(?ix)
    \b(
        api[_-]?key
        | access[_-]?token
        | refresh[_-]?token
        | password
        | passwd
        | client[_-]?secret
        | authorization
    )
    (\s*[:=]\s*)
    ("[^"]*"|'[^']*'|[^\s,;]+)
    """
)

BEARER_PATTERN = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")

SENSITIVE_FILE_PATTERN = re.compile(
    r"""(?ix)
    (?<![\w.-])
    (?:[^\s"'=,;<>]*[/\\])?
    \.env(?:\.(?:local|production|development))?
    (?![\w.-])
    """
)


def _sanitize_string(value: str) -> str:
    """过滤常见密钥形式，并限制字符串长度。"""
    value = BEARER_PATTERN.sub(f"Bearer {REDACTED}", value)
    value = SECRET_VALUE_PATTERN.sub(
        lambda match: f"{match.group(1)}{match.group(2)}{REDACTED}",
        value,
    )
    value = SENSITIVE_FILE_PATTERN.sub(REDACTED, value)

    if len(value) > MAX_STRING_LENGTH:
        value = value[:MAX_STRING_LENGTH] + TRUNCATED

    return value


def _sanitize(
    value: Any,
    key: str = "",
    depth: int = 0,
) -> Any:
    """递归清理 Trace 数据，限制敏感信息、长度和嵌套深度。"""
    normalized_key = key.lower().replace("-", "_")

    if normalized_key in SENSITIVE_KEYS:
        return REDACTED

    if normalized_key in {
        "path",
        "file_path",
        "filepath",
        "filename",
    } and isinstance(value, str):
        file_name = value.replace("\\", "/").rsplit("/", 1)[-1]
        if file_name in SENSITIVE_FILE_NAMES:
            return REDACTED

    if depth >= MAX_DEPTH and isinstance(value, (dict, list, tuple)):
        return TRUNCATED

    if isinstance(value, str):
        return _sanitize_string(value)

    if isinstance(value, dict):
        items = list(value.items())[:MAX_ITEMS]
        result = {
            str(item_key): _sanitize(
                item_value,
                key=str(item_key),
                depth=depth + 1,
            )
            for item_key, item_value in items
        }

        if len(value) > MAX_ITEMS:
            result["_truncated_items"] = len(value) - MAX_ITEMS

        return result

    if isinstance(value, (list, tuple)):
        result = [_sanitize(item, depth=depth + 1) for item in value[:MAX_ITEMS]]

        if len(value) > MAX_ITEMS:
            result.append(f"{TRUNCATED} 其余 {len(value) - MAX_ITEMS} 项已省略")

        return result

    if value is None or isinstance(value, (bool, int, float)):
        return value

    return _sanitize_string(str(value))


class ExecutionTrace:
    """记录单次 Agent 任务的执行事件和统计信息。"""

    def __init__(
        self,
        trace_id: str | None = None,
        *,
        verbose: bool = False,
        event_listener: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        """创建带稳定关联标识和递增事件序号的任务 Trace。"""
        self.trace_id = trace_id or uuid4().hex
        self.events: list[dict[str, Any]] = []
        self.verbose = verbose
        self.event_listener = event_listener

    def record(self, event_type: str, **details: Any) -> None:
        """清理事件详情后保存，并输出一条终端日志。"""
        safe_details = _sanitize(details)

        event = {
            "trace_id": self.trace_id,
            "sequence": len(self.events) + 1,
            "timestamp": datetime.now(UTC).isoformat(),
            "event": _sanitize_string(event_type),
            "details": safe_details,
        }
        self.events.append(event)

        if self.event_listener is not None:
            try:
                self.event_listener(event)
            except Exception as exc:  # noqa: BLE001 - optional UI boundary
                if self.verbose:
                    print(f"[Trace Listener Error] {exc}")

        if self.verbose:
            content = json.dumps(
                safe_details,
                ensure_ascii=False,
                default=str,
            )
            print(f"[Trace] {event['event']}: {content}")

    def get_events(self) -> list[dict[str, Any]]:
        """返回当前任务的事件记录副本。"""
        return [
            {
                **event,
                "details": _sanitize(event["details"]),
            }
            for event in self.events
        ]

    def _get_duration(self) -> float | None:
        """计算任务开始到结束的耗时；未结束时计算到当前时刻。"""
        start_time = None
        end_time = None

        for event in self.events:
            if event["event"] == "task_started" and start_time is None:
                start_time = datetime.fromisoformat(event["timestamp"])

            if event["event"] in {
                "task_completed",
                "task_failed",
                "task_stopped",
            }:
                end_time = datetime.fromisoformat(event["timestamp"])

        if start_time is None:
            return None

        if end_time is None:
            end_time = datetime.now(UTC)

        return round((end_time - start_time).total_seconds(), 3)

    def _get_status(self) -> tuple[str, str | None]:
        """从终止事件中获取任务状态和停止原因。"""
        terminal_events = {
            "task_completed": "completed",
            "task_failed": "failed",
            "task_stopped": "stopped",
        }

        for event in reversed(self.events):
            event_type = event["event"]

            if event_type in terminal_events:
                details = event["details"]
                reason = (
                    details.get("reason")
                    or details.get("error")
                    or details.get("message")
                )
                return terminal_events[event_type], reason

        if any(event["event"] == "task_started" for event in self.events):
            return "running", None

        return "not_started", None

    def _get_tool_statistics(self) -> dict[str, int]:
        """按结构化状态统计工具调用结果。"""
        started = sum(event["event"] == "tool_call_started" for event in self.events)

        succeeded = 0
        failed = 0
        cancelled = 0
        finished = 0

        for event in self.events:
            if event["event"] != "tool_call_finished":
                continue

            finished += 1
            details = event["details"]
            status = details.get("status")

            if status == "success":
                succeeded += 1
                continue
            if status == "failure":
                failed += 1
                continue
            if status == "cancelled":
                cancelled += 1
                continue

            # 兼容旧 Trace 事件，后续可在版本迁移完成后移除。
            result = details.get("result", "")
            is_error = details.get("is_error")

            if is_error is None:
                is_error = details.get("error")

            if is_error is None and isinstance(result, str):
                is_error = result.startswith("错误：")

            if is_error:
                failed += 1
            else:
                succeeded += 1

        return {
            "calls_started": started,
            "calls_finished": finished,
            "succeeded": succeeded,
            "failed": failed,
            "cancelled": cancelled,
            "unfinished": max(0, started - finished),
        }

    def _get_workspace(self) -> str | None:
        """返回任务启动事件中记录的受控工作区。"""

        for event in self.events:
            if event["event"] == "task_started":
                workspace = event["details"].get("workspace")
                return workspace if isinstance(workspace, str) else None
        return None

    def get_statistics(self) -> dict[str, Any]:
        """返回任务耗时、模型请求、工具调用和结束状态统计。"""
        status, reason = self._get_status()

        return {
            "trace_id": self.trace_id,
            "status": status,
            "stop_reason": reason,
            "workspace": self._get_workspace(),
            "duration_seconds": self._get_duration(),
            "model_requests": sum(
                event["event"] == "model_request_started" for event in self.events
            ),
            "tool_calls": self._get_tool_statistics(),
            "total_events": len(self.events),
        }

    def summary(self) -> str:
        """生成当前任务的 Trace 统计摘要。"""
        return json.dumps(
            self.get_statistics(),
            ensure_ascii=False,
            indent=2,
        )
