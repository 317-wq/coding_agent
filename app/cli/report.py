"""渲染结构化任务结果，提供 CLI 中文摘要和 JSON 输出。"""

from __future__ import annotations

from app.core.change_summary import render_change_summary
from app.core.recovery_summary import RecoverySummaryBuilder
from app.core.task_result import TaskResult, TestEvidenceStatus


class TaskReportRenderer:
    """使用同一份 TaskResult 生成文本或 JSON 报告。"""

    def render(self, result: TaskResult, output_format: str = "text") -> str:
        """按指定格式渲染任务结果，支持 text 和 json。"""
        if not isinstance(result, TaskResult):
            raise TypeError("报告输入必须是 TaskResult。")
        if output_format == "json":
            return result.to_json()
        if output_format == "text":
            return self._render_text(result)
        raise ValueError("报告格式必须是 text 或 json。")

    @staticmethod
    def _render_text(result: TaskResult) -> str:
        """生成简洁中文任务报告，避免回显完整测试输出。"""
        lines = [
            "任务报告",
            f"状态：{result.status.value}",
            f"任务：{result.task}",
            f"摘要：{result.summary or '无'}",
            render_change_summary(result.changes),
            "测试证据：",
        ]
        if result.tests:
            for evidence in result.tests:
                status = {
                    TestEvidenceStatus.PASSED: "通过",
                    TestEvidenceStatus.FAILED: "失败",
                    TestEvidenceStatus.NOT_RUN: "未运行",
                }[evidence.status]
                details = f"退出码 {evidence.exit_code}" if evidence.exit_code is not None else "无退出码"
                if evidence.error_kind:
                    details += f"，{evidence.error_kind}"
                lines.append(f"  - {evidence.target}：{status}（{details}）")
        else:
            lines.append("  - 无测试证据")
        lines.append(RecoverySummaryBuilder.render(result.recovery, result.repair_attempts))
        if result.stop_reason:
            lines.append(f"未完成原因：{result.stop_reason}")
        lines.append(f"Trace 事件：{result.trace_event_count}")
        return "\n".join(lines)


def render_task_report(result: TaskResult, output_format: str = "text") -> str:
    """使用默认渲染器输出人类可读或 JSON 任务报告。"""
    return TaskReportRenderer().render(result, output_format)
