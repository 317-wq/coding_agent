
import json
from datetime import datetime, timezone
from typing import Any
import re


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

BEARER_PATTERN = re.compile(
    r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"
)


def _sanitize_string(value: str) -> str:
    """过滤常见密钥形式，并限制字符串长度。"""
    value = SECRET_VALUE_PATTERN.sub(
        lambda match: (
            f"{match.group(1)}{match.group(2)}{REDACTED}"
        ),
        value,
    )
    value = BEARER_PATTERN.sub(f"Bearer {REDACTED}", value)

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
    }:
        if isinstance(value, str):
            file_name = value.replace("\\", "/").rsplit("/", 1)[-1]
            if file_name in SENSITIVE_FILE_NAMES:
                return REDACTED

    if depth >= MAX_DEPTH:
        if isinstance(value, (dict, list, tuple)):
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
        result = [
            _sanitize(item, depth=depth + 1)
            for item in value[:MAX_ITEMS]
        ]

        if len(value) > MAX_ITEMS:
            result.append(
                f"{TRUNCATED} 其余 {len(value) - MAX_ITEMS} 项已省略"
            )

        return result

    if value is None or isinstance(value, (bool, int, float)):
        return value

    return _sanitize_string(str(value))


class ExecutionTrace:
    """记录单次 Agent 任务的执行事件和统计信息。"""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def record(self, event_type: str, **details: Any) -> None:
        """清理事件详情后保存，并输出一条终端日志。"""
        safe_details = _sanitize(details)

        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": _sanitize_string(event_type),
            "details": safe_details,
        }
        self.events.append(event)

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
            end_time = datetime.now(timezone.utc)

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
        """统计工具调用次数、成功次数和失败次数。"""
        started = sum(
            event["event"] == "tool_call_started"
            for event in self.events
        )

        succeeded = 0
        failed = 0

        for event in self.events:
            if event["event"] != "tool_call_finished":
                continue

            details = event["details"]
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
            "calls_finished": succeeded + failed,
            "succeeded": succeeded,
            "failed": failed,
            "unfinished": max(0, started - succeeded - failed),
        }

    def get_statistics(self) -> dict[str, Any]:
        """返回任务耗时、模型请求、工具调用和结束状态统计。"""
        status, reason = self._get_status()

        return {
            "status": status,
            "stop_reason": reason,
            "duration_seconds": self._get_duration(),
            "model_requests": sum(
                event["event"] == "model_request_started"
                for event in self.events
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
