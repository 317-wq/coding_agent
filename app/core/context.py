"""Agent 运行期间的任务上下文及其长度控制。"""

import json
from dataclasses import dataclass, replace
from typing import Any

from app.core.planner import TaskPlan
from app.runtime.explorer import ProjectSnapshot
from app.tools.result import ToolResult, ToolStatus


@dataclass(frozen=True)
class TaskContext:
    """供模型和完成判定使用的最小任务上下文快照。"""

    task: str
    project_snapshot: ProjectSnapshot | None = None
    plan: TaskPlan | None = None
    current_step_id: int | None = None
    tool_results: tuple[ToolResult, ...] = ()
    test_results: tuple[str, ...] = ()
    truncated_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        snapshot = self.project_snapshot
        return {
            "task": self.task,
            "project_snapshot": (
                {
                    "project_types": list(snapshot.project_types),
                    "languages": list(snapshot.languages),
                    "manifests": list(snapshot.manifests),
                    "source_directories": list(snapshot.source_directories),
                    "entry_points": list(snapshot.entry_points),
                    "test_paths": list(snapshot.test_paths),
                    "metadata": list(snapshot.metadata),
                    "tree": list(snapshot.tree),
                    "truncated": snapshot.truncated,
                }
                if snapshot is not None
                else None
            ),
            "plan": self.plan.to_dict() if self.plan is not None else None,
            "current_step_id": self.current_step_id,
            "tool_results": [result.to_dict() for result in self.tool_results],
            "test_results": list(self.test_results),
            "truncated_count": self.truncated_count,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    def to_prompt(self) -> str:
        """生成长度受控、适合提供给模型的上下文文本。"""

        return self.to_json()


class ContextManager:
    """维护上下文，并裁剪较旧的非关键执行结果。"""

    def __init__(
        self,
        task: str,
        project_snapshot: ProjectSnapshot | None = None,
        plan: TaskPlan | None = None,
        *,
        max_entries: int = 20,
        max_chars: int = 12_000,
    ) -> None:
        if not isinstance(task, str) or not task.strip():
            raise ValueError("任务文本不能为空。")
        if max_entries < 1:
            raise ValueError("max_entries 必须大于 0。")
        if max_chars < 100:
            raise ValueError("max_chars 必须至少为 100。")
        self.max_entries = max_entries
        self.max_chars = max_chars
        self._context = TaskContext(
            task=task.strip(), project_snapshot=project_snapshot, plan=plan
        )

    @property
    def context(self) -> TaskContext:
        return self._context

    def update_plan(self, plan: TaskPlan, current_step_id: int | None = None) -> TaskContext:
        self._context = replace(
            self._context, plan=plan, current_step_id=current_step_id
        )
        return self._context

    def add_tool_result(self, result: ToolResult) -> TaskContext:
        if not isinstance(result, ToolResult):
            raise TypeError("工具结果必须是 ToolResult。")
        self._context = replace(
            self._context,
            tool_results=(*self._context.tool_results, result),
        )
        self._trim()
        return self._context

    def add_test_result(self, result: str) -> TaskContext:
        if not isinstance(result, str) or not result.strip():
            raise ValueError("测试结果不能为空。")
        self._context = replace(
            self._context,
            test_results=(*self._context.test_results, result.strip()),
        )
        self._trim()
        return self._context

    def begin_replan(self, plan: TaskPlan) -> TaskContext:
        """切换到恢复计划，并丢弃已经触发重规划的旧失败工具结果。"""
        successful_results = tuple(
            result
            for result in self._context.tool_results
            if result.status is ToolStatus.SUCCESS
        )
        self._context = replace(
            self._context,
            plan=plan,
            current_step_id=None,
            tool_results=successful_results,
        )
        self._trim()
        return self._context

    def _trim(self) -> None:
        tools = list(self._context.tool_results)
        tests = list(self._context.test_results)
        removed = 0

        while len(tools) + len(tests) > self.max_entries:
            if len(tools) > 1:
                # 优先丢弃最早的成功结果；失败信息属于关键上下文。
                index = next(
                    (i for i, item in enumerate(tools) if item.status is ToolStatus.SUCCESS),
                    0,
                )
                tools.pop(index)
            elif tests:
                tests.pop(0)
            else:
                break
            removed += 1

        candidate = replace(
            self._context,
            tool_results=tuple(tools),
            test_results=tuple(tests),
            truncated_count=self._context.truncated_count + removed,
        )
        # 以 JSON 长度近似 token 预算，继续从最旧的非关键结果裁剪。
        while len(candidate.to_json()) > self.max_chars and (tools or tests):
            if len(tools) > 1:
                index = next(
                    (i for i, item in enumerate(tools) if item.status is ToolStatus.SUCCESS),
                    0,
                )
                tools.pop(index)
            elif tests:
                tests.pop(0)
            else:
                break
            removed += 1
            candidate = replace(
                self._context,
                tool_results=tuple(tools),
                test_results=tuple(tests),
                truncated_count=self._context.truncated_count + removed,
            )
        self._context = candidate
