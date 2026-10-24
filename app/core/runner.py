"""单次任务的执行内核：初始化、模型-工具循环、恢复决策与收尾。

执行流程按显式阶段拆分：

- ``_initialize``           建立 Trace、项目快照、计划、上下文与安全会话
- ``_run_step``             请求模型并决定走完成判定还是工具批次
- ``_handle_model_answer``  完成判定、恢复与收尾（Decide / Finish）
- ``_execute_tool_batch``   按顺序执行工具并更新状态（Act / Recover）
- ``_finish_at_step_limit`` 轮次耗尽时的确定性收尾核验

各阶段只通过 ``TaskRunner`` 的实例状态交换数据，不共享局部变量。
"""

import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from openai.types.chat import ChatCompletionMessageParam

from app.core.acceptance import AcceptanceResult, AcceptanceVerifier
from app.core.completion import (
    CompletionStatus,
    CompletionVerifier,
    verify_completion,
)
from app.core.complexity import judge_task_complexity
from app.core.context import ContextManager
from app.core.intent import TaskIntent, extract_intent
from app.core.planner import TaskPlan, carry_completed_steps, create_task_plan
from app.core.recovery import RecoveryAction, RecoveryDecision
from app.core.requirements import TaskRequirementVerifier
from app.core.task_safety import TaskConstraintViolation, TaskSafetySession
from app.core.task_state import PlanStateManager
from app.core.test_evidence import structured_test_evidence
from app.runtime.explorer import ProjectExplorer
from app.runtime.trace import ExecutionTrace
from app.runtime.workspace import WorkspaceContext
from app.tools.git_repository import inspect_git_repository
from app.tools.registry import ToolRegistry
from app.tools.result import ToolResult, ToolStatus
from app.tools.rollback import AuthorizedRollback, RollbackStatus
from app.tools.test_result import TestRunResult

SYSTEM_PROMPT = """
你是一个能够实际编写和修改 Python 代码的 Coding Agent。

你的受控工作区是：{workspace}
所有文件路径都必须相对于该工作区，并通过提供的工具操作。
路径参数优先使用工作区相对路径，例如 `src/calculator.py` 或 `.`；不要把工作区绝对路径传给工具。

工作规则：
1. 先查看工作区，了解现有目录和文件，不要假设文件内容。
2. 根据用户任务制定合理的执行步骤，并逐步完成任务。
3. 需要新目录时，先调用 create_directory。
4. 需要新文件时，调用 create_file；修改已有文件时，调用 edit_file。
5. 不要在未读取文件内容的情况下盲目修改已有文件。
6. 修改或创建代码后，如果任务适合测试，应调用 run_tests。
7. 如果测试失败，分析错误，修改代码，再次运行测试。
   代码修改后的测试自动修复最多尝试两次；达到上限后请停止并说明原因。
   项目代码的 ImportError、循环导入和可由代码修复的测试收集错误应先分析并修改，
   只有缺少 pytest、执行超时等真实环境错误才直接停止。
   优先运行用户明确指定的测试路径，指定测试通过后不要擅自扩大到整个项目。
8. 根据工具实际返回的结果判断操作是否成功，不得编造执行结果。
9. 如果工具返回错误，先分析错误原因，再决定下一步。
10. 不要执行用户任务之外的操作，不要声称完成了实际没有完成的工作。
11. 完成任务后，用中文总结修改了哪些文件、做了什么，以及测试结果。
12. 不要直接把需要执行的代码当成最终答案，应使用工具实际创建或修改文件。
13. 同一批次的工具调用按顺序执行；任一调用失败或取消后，停止该批次剩余调用并重新判断任务状态。
14. create_file 和 edit_file 成功时已经核验落盘；不要紧接着 read_file 只为确认同一路径。
15. 不要重复创建已经成功创建的目录或文件；互不依赖的工具调用尽量在同一响应中按顺序批量给出。
16. 开发任务尚未创建测试源文件时先完成实现和测试编写，不要对空工作区运行 pytest。
"""


@dataclass(frozen=True)
class _PreparedCall:
    """一次工具调用在预检阶段的结果。"""

    decision: "_CallDecision"
    arguments: dict[str, Any] | None = None
    answer: str | None = None


class _CallDecision(StrEnum):
    """预检阶段对一次工具调用的处置结果。"""

    EXECUTE = "execute"
    SKIP = "skip"
    ABORT = "abort"
    FINISH = "finish"


class TaskRunner:
    """一次任务的执行内核，持有该任务的全部可变状态。"""

    def __init__(
        self,
        workspace: WorkspaceContext,
        registry: ToolRegistry,
        model: Any,
        *,
        max_steps: int,
        max_tool_calls: int,
        debug: bool = False,
        event_listener: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        """保存本次任务需要的依赖与初始状态。"""
        self.workspace = workspace
        self.registry = registry
        self.model = model
        self.max_steps = max_steps
        self.max_tool_calls = max_tool_calls
        self.debug = debug
        self.event_listener = event_listener

        self.task = ""
        self.trace: ExecutionTrace | None = None
        self.plan: TaskPlan | None = None
        self.context: ContextManager | None = None
        self.safety: TaskSafetySession | None = None
        self.change_set: Any = None
        self.recovery_decision: RecoveryDecision | None = None
        self.state_manager: PlanStateManager | None = None
        self.project_snapshot: Any = None
        self.messages: list[ChatCompletionMessageParam] = []
        self.tool_call_count = 0
        self.clarification: str | None = None
        self.intent: TaskIntent | None = None

    def _debug_print(self, message: str) -> None:
        """仅在调试模式输出底层执行日志。"""
        if self.debug:
            print(message)

    def run(self, task: str) -> str:
        """建立任务状态并逐步推进，直到完成、停止或耗尽轮次。"""
        self._initialize(task)
        if self.clarification is not None:
            self.trace.record("task_stopped", reason=self.clarification)
            return self.clarification
        for step in range(1, self.max_steps + 1):
            answer = self._run_step(step)
            if answer is not None:
                return answer
        return self._finish_at_step_limit(task)

    def _initialize(self, task: str) -> None:
        """建立 Trace、项目快照、计划、上下文、安全会话与初始消息。"""
        self.task = task
        self.trace = ExecutionTrace(
            verbose=self.debug,
            event_listener=self.event_listener,
        )
        self.trace.record(
            "task_started",
            task=task,
            workspace=str(self.workspace.root),
        )
        self.project_snapshot = ProjectExplorer(self.workspace).explore()
        git_info = inspect_git_repository(self.workspace)
        self.trace.record("git_baseline_detected", **git_info.to_dict())
        decision = judge_task_complexity(task, self.project_snapshot)
        self.trace.record(
            "complexity_decided",
            complexity=decision.complexity.value,
            requires_planning=decision.requires_planning,
            reasons=decision.reasons,
        )
        self.plan = (
            create_task_plan(task, self.project_snapshot, decision)
            if decision.requires_planning
            else None
        )
        self.intent = (
            self.plan.intent
            if self.plan is not None and self.plan.intent is not None
            else extract_intent(task)
        )
        if self.plan is not None:
            self.trace.record("plan_created", plan=self.plan.to_dict())
            if self.plan.lint_issues:
                self.trace.record("plan_linted", issues=list(self.plan.lint_issues))
        self.trace.record(
            "intent_extracted",
            acceptance=[item.to_dict() for item in self.intent.acceptance],
            open_questions=list(self.intent.open_questions),
        )
        if self.intent.open_questions and not sys.stdin.isatty():
            self.clarification = (
                "任务需要确认：" + "；".join(self.intent.open_questions)
            )
        self.state_manager = (
            PlanStateManager(self.plan) if self.plan is not None else None
        )
        self.context = ContextManager(task, self.project_snapshot, self.plan)
        self.safety = TaskSafetySession(self.workspace, task=task)
        self.change_set = self.safety.change_set

        self.messages = [
            {
                "role": "system",
                "content": SYSTEM_PROMPT.format(workspace=self.workspace.root),
            },
            {
                "role": "system",
                "content": (
                    "以下是任务开始时自动生成的项目快照。"
                    "它只用于初步定位，修改前仍需读取相关文件：\n"
                    f"{self.project_snapshot.to_prompt()}"
                ),
            },
            {
                "role": "user",
                "content": task,
            },
        ]
        if self.plan is not None:
            self.messages.insert(
                2,
                {
                    "role": "system",
                    "content": (
                        "这是本次任务的结构化计划，请按步骤推进；"
                        "只有工具结果和测试结果证明完成后才能结束：\n"
                        f"{self.plan.to_json()}"
                    ),
                },
            )

    def _run_step(self, step: int) -> str | None:
        """请求模型并推进一轮；返回字符串表示任务已经结束。"""
        request_id = f"model-{step}"
        self._debug_print(f"\n[Agent] 第 {step}/{self.max_steps} 轮")
        request_id = f"model-{step}"
        self.trace.record("model_request_started", step=step, request_id=request_id)

        available_tools = self.registry.model_definitions()
        forced_rerun_target = self.safety.pending_test_rerun_target()
        if forced_rerun_target is not None:
            available_tools = [
                definition
                for definition in available_tools
                if definition["function"]["name"] == "run_tests"
            ]
            self.messages.append(
                {
                    "role": "system",
                    "content": (
                        "控制层检测到失败测试后的修改尚未验证。"
                        "本轮唯一允许的动作是调用 run_tests 重新运行目标 "
                        f"`{forced_rerun_target}`；不要调用其他工具或直接总结。"
                    ),
                }
            )
            self.trace.record(
                "test_rerun_enforced",
                request_id=request_id,
                test_target=forced_rerun_target,
                allowed_tools=("run_tests",),
            )
        elif (
            self.state_manager is not None
            and self.state_manager.should_defer_tests_without_sources()
            and not self._has_test_sources()
        ):
            available_tools = [
                definition
                for definition in available_tools
                if definition["function"]["name"] != "run_tests"
            ]
            self.messages.append(
                {
                    "role": "system",
                    "content": (
                        "当前开发计划尚未创建任何测试源文件，run_tests 暂不可用。"
                        "请先完成业务实现并创建 test_*.py 或 conftest.py，"
                        "不要对空工作区运行测试。"
                    ),
                }
            )
            self.trace.record(
                "tool_availability_constrained",
                request_id=request_id,
                unavailable_tools=("run_tests",),
                reason="开发任务尚未创建测试源文件。",
            )

        try:
            response = self.model.chat(
                messages=self.messages,
                tools=available_tools,
            )
        except KeyboardInterrupt:
            self.trace.record(
                "task_stopped",
                reason="user_interrupted",
                step=step,
                request_id=request_id,
            )
            raise
        except Exception as exc:  # noqa: BLE001 - model boundary
            self.trace.record(
                "model_request_failed",
                step=step,
                request_id=request_id,
                error=str(exc),
            )
            self.trace.record(
                "task_failed",
                reason="model_request_failed",
                request_id=request_id,
            )
            return f"模型请求失败：{exc}"

        if not response.choices:
            self.trace.record(
                "model_response_empty",
                step=step,
                request_id=request_id,
            )
            self.trace.record(
                "task_failed",
                reason="empty_model_response",
                request_id=request_id,
            )
            return "模型没有返回任何候选响应。"

        message = response.choices[0].message

        self.trace.record(
            "model_response_received",
            step=step,
            request_id=request_id,
            has_tool_calls=bool(message.tool_calls),
            tool_call_count=len(message.tool_calls or []),
        )

        rerun_target = self.safety.pending_test_rerun_target()
        if (
            rerun_target is not None
            and message.tool_calls
            and not any(
                call.function.name == "run_tests" for call in message.tool_calls
            )
        ):
            self.trace.record(
                "test_rerun_required",
                request_id=request_id,
                test_target=rerun_target,
                repair_attempts=self.safety.test_repair_attempts,
                deferred_tool_calls=[
                    call.function.name for call in message.tool_calls
                ],
                reason="测试失败后的实际修改必须先重跑验证。",
            )
            self.messages.append(
                {
                    "role": "system",
                    "content": (
                        "上一轮测试失败后的代码修改尚未验证。"
                        "本轮非测试工具调用已被控制层暂缓；"
                        "下一步只能调用 run_tests "
                        f"重新运行测试目标 `{rerun_target}`。"
                        "请勿继续读取、编辑或创建旁路文件。"
                    ),
                }
            )
            return None

        # 没有工具调用，说明模型准备直接回复用户。

        if not message.tool_calls:
            return self._handle_model_answer(step, request_id, message)
        return self._execute_tool_batch(step, request_id, message)

    def _handle_model_answer(
        self, step: int, request_id: str, message: Any
    ) -> str | None:
        """模型给出文字结论时执行完成判定与恢复决策。"""
        answer = message.content or "任务结束，但模型没有提供文字总结。"
        if self.context is not None:
            completion_check_id = f"completion-{step}"
            if self.plan is not None:
                decision = verify_completion(
                    self.context.context.plan or self.plan,
                    self.context.context.tool_results,
                    self.context.context.test_results,
                    self._acceptance_results(),
                )
            else:
                decision = CompletionVerifier().verify_basic(
                    self.context.context.tool_results,
                    self.context.context.test_results,
                    self._acceptance_results(),
                )
            self.trace.record(
                "completion_checked",
                completion_check_id=completion_check_id,
                request_id=request_id,
                status=decision.status.value,
                completed=decision.completed,
                reason=decision.reason,
                pending_step_ids=decision.pending_step_ids,
                failed_step_ids=decision.failed_step_ids,
                evidence=decision.evidence,
            )
            requirement_check = TaskRequirementVerifier().verify(
                self.task, self.workspace
            )
            self.trace.record(
                "requirements_checked",
                completion_check_id=completion_check_id,
                passed=requirement_check.passed,
                missing=requirement_check.missing,
            )
            if not requirement_check.passed:
                self.messages.append({"role": "assistant", "content": answer})
                self.messages.append(
                    {
                        "role": "system",
                        "content": (
                            "最终代码尚未满足用户明确要求，请继续修正："
                            + "；".join(requirement_check.missing)
                        ),
                    }
                )
                return None
            if decision.status is not CompletionStatus.COMPLETE:
                if self.safety.validation_only:
                    self.trace.record(
                        "task_stopped",
                        reason="validation_incomplete",
                        error=decision.reason,
                        completion_check_id=completion_check_id,
                    )
                    return decision.reason
                unresolved_test = self.safety.unresolved_test_reason()
                if unresolved_test is not None:
                    rerun_target = self.safety.pending_test_rerun_target()
                    if rerun_target is not None:
                        self.trace.record(
                            "test_rerun_required",
                            completion_check_id=completion_check_id,
                            request_id=request_id,
                            test_target=rerun_target,
                            repair_attempts=self.safety.test_repair_attempts,
                            reason="测试失败后的代码修改尚未经过测试验证。",
                        )
                        self.messages.append(
                            {"role": "assistant", "content": answer}
                        )
                        self.messages.append(
                            {
                                "role": "system",
                                "content": (
                                    "上一轮测试失败后已经产生实际代码修改，"
                                    "这些修改尚未验证。现在必须调用 run_tests "
                                    f"重新运行测试目标 `{rerun_target}`；"
                                    "不得直接总结或结束任务。根据重跑结果继续修复，"
                                    "或在修复次数达到上限时报告未完成。"
                                ),
                            }
                        )
                        return None
                    self.trace.record(
                        "task_stopped",
                        reason="unresolved_test_failure",
                        error=unresolved_test,
                        completion_check_id=completion_check_id,
                    )
                    return unresolved_test
                recovery = self.safety.observe_completion(decision.status)
                self.recovery_decision = recovery
                if recovery.action is not RecoveryAction.CONTINUE:
                    self.trace.record(
                        "recovery_decided",
                        recovery_id=f"recovery-{completion_check_id}",
                        completion_check_id=completion_check_id,
                        action=recovery.action.value,
                        reason=recovery.reason,
                        triggers=recovery.triggers,
                        recoverable_paths=recovery.recoverable_paths,
                    )
                if recovery.action is RecoveryAction.SUGGEST_ROLLBACK:
                    reason, _ = self._rollback_and_replan(
                        recovery,
                        f"recovery-{completion_check_id}",
                        {"completion_check_id": completion_check_id},
                    )
                    if reason is not None:
                        return reason
                    return None
                if recovery.action is RecoveryAction.STOP:
                    self.trace.record(
                        "task_stopped",
                        reason=recovery.reason,
                        completion_check_id=completion_check_id,
                    )
                    return recovery.reason
                self.messages.append({"role": "assistant", "content": answer})
                self.messages.append(
                    {
                        "role": "system",
                        "content": (
                            f"完成判定未通过，请继续执行：{decision.reason}"
                        ),
                    }
                )
                return None
        self.trace.record(
            "task_completed",
            answer=answer,
            request_id=request_id,
        )
        self._debug_print("\n[Agent] 任务结束")
        return answer


    def _execute_tool_batch(
        self, step: int, request_id: str, message: Any
    ) -> str | None:
        """按顺序执行本轮的模型工具调用，并更新状态。"""
        # 保存模型的工具调用消息，供下一轮对话使用。
        self.messages.append(
            {
                "role": "assistant",
                "content": message.content,
                "tool_calls": [
                    {
                        "id": tool_call.id,
                        "type": "function",
                        "function": {
                            "name": tool_call.function.name,
                            "arguments": tool_call.function.arguments,
                        },
                    }
                    for tool_call in message.tool_calls
                ],
            }
        )

        # 按顺序执行工具调用。
        for call_index, tool_call in enumerate(message.tool_calls):
            tool_name = tool_call.function.name

            prepared = self._prepare_tool_call(
                tool_call, tool_name, request_id
            )
            if prepared.decision is _CallDecision.FINISH:
                return prepared.answer
            if prepared.decision is _CallDecision.ABORT:
                return None
            if prepared.decision is _CallDecision.SKIP:
                continue
            arguments = prepared.arguments or {}

            (
                tool_result,
                snapshot_paths,
                pre_tool_changes,
                finish_reason,
            ) = self._execute_tool_call(
                tool_name, arguments, tool_call, request_id
            )
            if finish_reason is not None:
                return finish_reason

            finish_reason, abort_batch = self._apply_tool_result(
                tool_call=tool_call,
                tool_name=tool_name,
                arguments=arguments,
                snapshot_paths=snapshot_paths,
                pre_tool_changes=pre_tool_changes,
                tool_result=tool_result,
                request_id=request_id,
                remaining_calls=len(message.tool_calls) - call_index - 1,
            )
            if finish_reason is not None:
                return finish_reason
            if abort_batch:
                return None


        return None

    def _prepare_tool_call(
        self, tool_call: Any, tool_name: str, request_id: str
    ) -> _PreparedCall:
        """解析参数并完成重复写、创建冲突、调用预算与强制重跑等预检。"""
        try:
            arguments = json.loads(tool_call.function.arguments)

            if not isinstance(arguments, dict):
                raise ValueError(  # noqa: TRY004 - normalized below
                    "工具参数必须是 JSON 对象。"
                )

        except (json.JSONDecodeError, ValueError) as exc:
            tool_result = ToolResult.failure(f"工具参数解析失败：{exc}")
            if self.context is not None:
                before_trim = self.context.context.truncated_count
                self.context.add_tool_result(tool_result)
                if self.context.context.truncated_count > before_trim:
                    self.trace.record(
                        "context_trimmed",
                        tool_call_id=tool_call.id,
                        truncated_count=(
                            self.context.context.truncated_count
                        ),
                    )
            self.trace.record(
                "tool_arguments_invalid",
                tool=tool_name,
                tool_call_id=tool_call.id,
                request_id=request_id,
                status=tool_result.status.value,
                error=str(exc),
            )
            self._debug_print(
                f"[Tool Error] {tool_name}: {tool_result.to_json()}"
            )

            self.messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": tool_result.to_json(),
                }
            )

            # 让模型根据错误重新决定，而不是继续当前批次。
            return _PreparedCall(_CallDecision.ABORT)

        self.trace.record(
            "tool_call_started",
            tool=tool_name,
            arguments=arguments,
            tool_call_id=tool_call.id,
            request_id=request_id,
        )
        self._debug_print(
            f"[Tool] {tool_name}({json.dumps(arguments, ensure_ascii=False)})"
        )

        cached_result = self.safety.completed_mutation(tool_name, arguments)
        if cached_result is not None:
            if tool_name == "create_directory":
                cached_result = ToolResult.success(
                    f"无需创建，目录已存在：{arguments.get('path', '')}"
                )
            self.trace.record(
                "duplicate_tool_call_skipped",
                tool=tool_name,
                arguments=arguments,
                tool_call_id=tool_call.id,
                request_id=request_id,
                reason="相同写操作已成功完成，复用先前结果。",
            )
            self.trace.record(
                "tool_call_finished",
                tool=tool_name,
                tool_call_id=tool_call.id,
                request_id=request_id,
                status=cached_result.status.value,
                result=cached_result.result,
                error=cached_result.error,
                cached=True,
            )
            result_json = cached_result.to_json()
            self._debug_print(f"[Tool Result] {result_json[:500]}")
            self.messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result_json,
                }
            )
            self.messages.append(
                {
                    "role": "system",
                    "content": (
                        "该写操作已经成功完成且目标状态仍然成立，"
                        "不要再次请求相同操作；请继续下一个未完成目标。"
                    ),
                }
            )
            return _PreparedCall(_CallDecision.SKIP)

        if tool_name == "create_file" and self.safety.task_created_file_exists(
            arguments.get("path")
        ):
            path = str(arguments.get("path"))
            redirect_result = ToolResult.success(
                f"未重复创建：{path} 已由本任务创建；"
                "如需改变内容，请基于已知内容调用 edit_file。"
            )
            self.trace.record(
                "conflicting_create_redirected",
                tool=tool_name,
                path=path,
                tool_call_id=tool_call.id,
                request_id=request_id,
                reason="本任务创建的文件已存在，要求改用 edit_file。",
            )
            self.trace.record(
                "tool_call_finished",
                tool=tool_name,
                tool_call_id=tool_call.id,
                request_id=request_id,
                status=redirect_result.status.value,
                result=redirect_result.result,
                error=None,
                redirected=True,
            )
            result_json = redirect_result.to_json()
            self._debug_print(f"[Tool Result] {result_json[:500]}")
            self.messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result_json,
                }
            )
            self.messages.append(
                {
                    "role": "system",
                    "content": (
                        f"`{path}` 已由本任务创建。不要再次 create_file；"
                        "如需调整内容，请使用 edit_file，"
                        "否则继续创建下一个尚未完成的文件。"
                    ),
                }
            )
            return _PreparedCall(_CallDecision.SKIP)

        if self.tool_call_count >= self.max_tool_calls:
            reason = (
                f"已达到工具调用上限 {self.max_tool_calls}，"
                "停止执行。请缩小任务范围后重试。"
            )
            self.trace.record(
                "task_stopped",
                reason="max_tool_calls",
                limit=self.max_tool_calls,
                request_id=request_id,
            )
            return _PreparedCall(_CallDecision.FINISH, answer=reason)

        rerun_block_reason = self.safety.test_rerun_block_reason(
            tool_name, arguments
        )
        if rerun_block_reason is not None:
            self.tool_call_count += 1
            tool_result = ToolResult.cancelled(rerun_block_reason)
            if self.context is not None:
                self.context.add_tool_result(tool_result)
            self.trace.record(
                "safety_rejected",
                rejection_type="test_rerun_without_mutation",
                tool=tool_name,
                tool_call_id=tool_call.id,
                request_id=request_id,
                reason=rerun_block_reason,
            )
            self.trace.record(
                "tool_call_finished",
                tool=tool_name,
                tool_call_id=tool_call.id,
                request_id=request_id,
                status=tool_result.status.value,
                result=tool_result.result,
                error=tool_result.error,
                rejected=True,
            )
            result_json = tool_result.to_json()
            self._debug_print(f"[Tool Result] {result_json[:500]}")
            self.messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result_json,
                }
            )
            self.messages.append(
                {
                    "role": "system",
                    "content": rerun_block_reason,
                }
            )
            return _PreparedCall(_CallDecision.ABORT)
        return _PreparedCall(_CallDecision.EXECUTE, arguments)

    def _execute_tool_call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        tool_call: Any,
        request_id: str,
    ) -> tuple[ToolResult, tuple[str, ...], dict[str, dict], str | None]:
        """执行一次工具调用，返回结果、快照基线与可能的结束原因。"""
        snapshot_paths: tuple[str, ...] = ()
        pre_tool_changes: dict[str, dict] = {}
        try:
            self.safety.validate_tool(tool_name, arguments)
            snapshot_paths = self.safety.prepare_tool(tool_name, arguments)
            pre_tool_changes = {
                change.path: change.to_dict()
                for change in self.safety.change_set.summary().changes
            }
            tool_result = self.registry.execute(
                tool_name,
                arguments,
                event_recorder=self.trace.record,
            )
        except KeyboardInterrupt:
            self.trace.record(
                "task_stopped",
                reason="user_interrupted",
                tool=tool_name,
                tool_call_id=tool_call.id,
                request_id=request_id,
            )
            raise
        except TaskConstraintViolation as exc:
            tool_result = ToolResult.cancelled(str(exc))
            self.tool_call_count += 1
            self.trace.record(
                "safety_rejected",
                rejection_type="user_constraint",
                tool=tool_name,
                tool_call_id=tool_call.id,
                request_id=request_id,
                reason=str(exc),
            )
            self.trace.record(
                "tool_call_finished",
                tool=tool_name,
                tool_call_id=tool_call.id,
                request_id=request_id,
                status=tool_result.status.value,
                result=tool_result.result,
                error=tool_result.error,
                rejected=True,
            )
            self.trace.record(
                "task_stopped",
                reason="user_constraint_violation",
                error=str(exc),
                tool_call_id=tool_call.id,
                request_id=request_id,
            )
            return tool_result, (), {}, str(exc)
        except Exception as exc:  # noqa: BLE001 - tool boundary
            tool_result = ToolResult.failure(f"工具执行异常：{exc}")
        return tool_result, snapshot_paths, pre_tool_changes, None

    def _apply_tool_result(
        self,
        *,
        tool_call: Any,
        tool_name: str,
        arguments: dict[str, Any],
        snapshot_paths: tuple[str, ...],
        pre_tool_changes: dict[str, dict],
        tool_result: ToolResult,
        request_id: str,
        remaining_calls: int,
    ) -> tuple[str | None, bool]:
        """记录一次工具执行结果并处理恢复决策。

        返回 ``(结束原因, 是否中断本批次)``：结束原因非空时任务应立即返回，
        否则按第二项决定是本批次中断还是继续下一个调用。
        """
        self.tool_call_count += 1
        reported_mutation = self.safety.record_completed_mutation(
            tool_name, arguments, tool_result
        )
        post_tool_changes = self.safety.change_set.summary()
        post_tool_changes_by_path = {
            change.path: change.to_dict()
            for change in post_tool_changes.changes
        }
        changed_snapshot_paths = tuple(
            path
            for path in snapshot_paths
            if pre_tool_changes.get(path) != post_tool_changes_by_path.get(path)
        )
        if changed_snapshot_paths:
            self.trace.record(
                "snapshot_created",
                tool=tool_name,
                tool_call_id=tool_call.id,
                request_id=request_id,
                paths=changed_snapshot_paths,
                purpose="rollback_baseline",
            )
        mutation_applied = bool(changed_snapshot_paths) or (
            reported_mutation and not snapshot_paths
        )
        if mutation_applied:
            mutation_path = arguments.get("path")
            self.safety.record_mutation_applied(
                mutation_path if isinstance(mutation_path, str) else None
            )
            self.trace.record(
                "mutation_applied",
                tool=tool_name,
                path=arguments.get("path"),
                tool_call_id=tool_call.id,
                request_id=request_id,
                tool_status=tool_result.status.value,
                net_change=any(
                    path in post_tool_changes_by_path
                    for path in changed_snapshot_paths
                ),
            )

        if (
            tool_result.status is ToolStatus.FAILURE
            and self._is_boundary_failure(tool_result)
        ):
            reason = tool_result.error or "工具访问违反工作区安全边界。"
            self.trace.record(
                "safety_rejected",
                rejection_type="workspace_boundary",
                tool=tool_name,
                tool_call_id=tool_call.id,
                request_id=request_id,
                reason=reason,
            )
            self.trace.record(
                "tool_call_finished",
                tool=tool_name,
                tool_call_id=tool_call.id,
                request_id=request_id,
                status=tool_result.status.value,
                result=tool_result.result,
                error=tool_result.error,
                rejected=True,
            )
            self.trace.record(
                "task_stopped",
                reason="workspace_boundary_violation",
                error=reason,
                tool_call_id=tool_call.id,
                request_id=request_id,
            )
            return reason, False

        if tool_result.status is ToolStatus.FAILURE and self.safety.validation_only:
            reason = tool_result.error or "验收工具执行失败，已停止继续探索。"
            self.trace.record(
                "tool_call_finished",
                tool=tool_name,
                tool_call_id=tool_call.id,
                request_id=request_id,
                status=tool_result.status.value,
                result=tool_result.result,
                error=tool_result.error,
            )
            self.trace.record(
                "task_stopped",
                reason="validation_failure",
                error=reason,
                tool_call_id=tool_call.id,
                request_id=request_id,
            )
            return reason, False

        if self.context is not None:
            before_trim = self.context.context.truncated_count
            if tool_name == "run_tests":
                self.context.add_test_result(
                    tool_result.data
                    if isinstance(tool_result.data, TestRunResult)
                    else (
                        tool_result.result
                        or tool_result.error
                        or "测试执行失败"
                    )
                )
            else:
                self.context.add_tool_result(tool_result)
            if self.context.context.truncated_count > before_trim:
                self.trace.record(
                    "context_trimmed",
                    tool_call_id=tool_call.id,
                    truncated_count=self.context.context.truncated_count,
                )
        if self.state_manager is not None:
            current = self.state_manager.state().current_step_id
            if current is not None and self.state_manager.should_advance_for_tool(
                current, tool_name, arguments
            ):
                self.state_manager.complete_step(current, tool_result.result)
                self.plan = self.state_manager.state().plan
                self.trace.record(
                    "plan_step_status_changed",
                    tool_call_id=tool_call.id,
                    step_id=current,
                    status="completed",
                    reason="后续阶段已开始",
                )
                current = None
            if current is None:
                pending = self.state_manager.match_step(tool_name, arguments)
                if pending is not None:
                    self.state_manager.start_step(pending)
                    current = pending
            if current is not None:
                if tool_result.status is ToolStatus.SUCCESS:
                    if self.state_manager.can_complete_step(
                        current, tool_name, arguments, tool_result
                    ):
                        self.state_manager.complete_step(current, tool_result.result)
                    # 工具成功但未满足当前步骤条件时，保留 running 状态，
                    # 等待匹配的工具结果，不能误标记为失败。
                elif tool_result.status is ToolStatus.CANCELLED:
                    self.state_manager.cancel_step(
                        current, tool_result.result or "工具已取消"
                    )
                else:
                    self.state_manager.fail_step(
                        current, tool_result.error or "工具执行失败"
                    )
                self.plan = self.state_manager.state().plan
                updated_step = next(
                    step
                    for step in self.plan.steps
                    if step.step_id == current
                )
                self.trace.record(
                    "plan_step_status_changed",
                    tool_call_id=tool_call.id,
                    step_id=current,
                    status=updated_step.status.value,
                )
                self.context.update_plan(
                    self.plan,
                    self.state_manager.state().current_step_id,
                )

        plan_failed = bool(
            self.state_manager is not None and self.state_manager.state().failed_step_ids
        )
        try:
            recovery = self.safety.observe(
                tool_name,
                tool_result,
                plan_failed=plan_failed,
                arguments=arguments,
            )
        except Exception as exc:  # noqa: BLE001 - self.safety boundary
            recovery = RecoveryDecision(
                RecoveryAction.STOP,
                f"无法生成安全恢复决策：{exc}",
            )
        self.recovery_decision = recovery
        change_summary = self.safety.change_set.summary()
        self.trace.record(
            "change_set_updated",
            tool_call_id=tool_call.id,
            request_id=request_id,
            comparison_basis="task_initial_state",
            touched_count=change_summary.touched_count,
            unchanged_count=change_summary.unchanged_count,
            changed_count=change_summary.net_changed_count,
            net_changed_count=change_summary.net_changed_count,
            created_paths=change_summary.created_paths,
            modified_paths=change_summary.modified_paths,
            deleted_paths=change_summary.deleted_paths,
        )
        recovery_id = f"recovery-{tool_call.id}"
        if recovery.action is not RecoveryAction.CONTINUE:
            self.trace.record(
                "recovery_decided",
                recovery_id=recovery_id,
                tool_call_id=tool_call.id,
                request_id=request_id,
                action=recovery.action.value,
                reason=recovery.reason,
                triggers=recovery.triggers,
                recoverable_paths=recovery.recoverable_paths,
            )

        trace_details: dict[str, Any] = {}
        if isinstance(tool_result.data, TestRunResult):
            trace_details["test"] = tool_result.data.to_dict()
        self.trace.record(
            "tool_call_finished",
            tool=tool_name,
            tool_call_id=tool_call.id,
            request_id=request_id,
            status=tool_result.status.value,
            result=tool_result.result,
            error=tool_result.error,
            is_error=tool_result.status is ToolStatus.FAILURE,
            **trace_details,
        )
        result_json = tool_result.to_json()
        self._debug_print(f"[Tool Result] {result_json[:500]}")

        self.messages.append(
            {
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": result_json,
            }
        )

        if recovery.action is RecoveryAction.SUGGEST_ROLLBACK:
            reason, _ = self._rollback_and_replan(
                recovery,
                recovery_id,
                {"tool_call_id": tool_call.id},
            )
            if reason is not None:
                return reason, False
            return None, True
        if recovery.action is RecoveryAction.REPLAN:
            # 只有真正重建计划才消耗重规划预算，避免未应用的决策污染计数。
            self.safety.note_replan()
            self.project_snapshot = ProjectExplorer(self.workspace).explore()
            decision = judge_task_complexity(self.task, self.project_snapshot)
            replacement_plan = create_task_plan(
                self.task, self.project_snapshot, decision
            )
            prior_plan = self.plan
            self.plan = (
                replacement_plan
                if prior_plan is None
                else carry_completed_steps(prior_plan, replacement_plan)
            )
            self.state_manager = PlanStateManager(self.plan)
            self.context.begin_replan(self.plan)
            self.messages.append(
                {
                    "role": "system",
                    "content": (
                        "当前执行失败，失败输出已保留。请先根据错误定位根因，"
                        "调用读取或编辑工具完成一次实际修复后再重跑测试；"
                        "不要在未修改相关代码时重复运行同一失败测试。"
                        "已刷新项目状态并重新规划：\n"
                        f"{self.plan.to_json()}"
                    ),
                }
            )
            return None, True

        if recovery.action is RecoveryAction.STOP:
            self.trace.record(
                "task_stopped",
                reason=recovery.reason,
                recovery_id=recovery_id,
                tool_call_id=tool_call.id,
            )
            return recovery.reason, False

        # 批次采用失败关闭规则：失败或取消后均停止剩余调用，
        # 避免后续工具建立在无效前置结果之上。
        if tool_result.status is not ToolStatus.SUCCESS:
            self.trace.record(
                "tool_batch_aborted",
                tool=tool_name,
                tool_call_id=tool_call.id,
                request_id=request_id,
                status=tool_result.status.value,
                skipped_calls=remaining_calls,
            )
            return None, True
        return None, False

    def _rollback_and_replan(
        self,
        recovery: RecoveryDecision,
        recovery_id: str,
        trace_context: dict[str, Any],
    ) -> tuple[str | None, str]:
        """授权并执行回滚；成功时重建计划并返回 ``(None, "restored")``。

        返回的字符串表示任务需要立即结束，第二个值是结束类别
        （``cancelled`` / ``failed`` / ``restored``）。完成判定分支和工具分支
        共用这一份实现，只是 Trace 关联字段不同。
        """
        self.trace.record(
            "rollback_authorization_requested",
            recovery_id=recovery_id,
            paths=recovery.recoverable_paths,
            reason=recovery.reason,
            **trace_context,
        )
        rollback = AuthorizedRollback(
            self.safety.change_set, self.registry.authorizer
        ).execute()
        self.trace.record(
            "rollback_finished",
            recovery_id=recovery_id,
            status=rollback.status.value,
            reason=rollback.reason,
            restored_paths=rollback.restored_paths,
            pending_paths=rollback.pending_paths,
            **trace_context,
        )
        if rollback.status is not RollbackStatus.RESTORED:
            if rollback.status is RollbackStatus.CANCELLED:
                detail = rollback.reason.rstrip("。；;. ")
                reason = (
                    f"{detail}；任务已停止，当前修改已保留，请确认后重试。"
                )
                self.trace.record(
                    "user_confirmation_required",
                    reason=reason,
                    **trace_context,
                )
                self.trace.record("task_stopped", reason=reason, **trace_context)
                return reason, "cancelled"
            reason = f"恢复未完成：{rollback.reason}"
            self.trace.record("task_stopped", reason=reason, **trace_context)
            return reason, "failed"

        post_rollback = self.safety.change_set.summary()
        self.trace.record(
            "change_set_updated",
            recovery_id=recovery_id,
            comparison_basis="task_initial_state",
            reason="rollback_verified",
            touched_count=post_rollback.touched_count,
            unchanged_count=post_rollback.unchanged_count,
            changed_count=post_rollback.net_changed_count,
            net_changed_count=post_rollback.net_changed_count,
            created_paths=post_rollback.created_paths,
            modified_paths=post_rollback.modified_paths,
            deleted_paths=post_rollback.deleted_paths,
            **trace_context,
        )
        if self.safety.test_repair_attempts >= 2:
            reason = (
                "测试自动修复已达到上限，当前任务变更已回滚；"
                "任务停止，不再从初始状态重复执行。"
            )
            self.trace.record("task_stopped", reason=reason, **trace_context)
            return reason, "failed"

        self.safety.reset_after_recovery(rollback.status)
        self.change_set = self.safety.change_set
        self.project_snapshot = ProjectExplorer(self.workspace).explore()
        complexity = judge_task_complexity(self.task, self.project_snapshot)
        self.plan = create_task_plan(self.task, self.project_snapshot, complexity)
        self.state_manager = PlanStateManager(self.plan)
        self.context = ContextManager(self.task, self.project_snapshot, self.plan)
        self.messages.append(
            {
                "role": "system",
                "content": (
                    "本次任务变更已授权回滚。项目快照和计划已刷新，"
                    "请从新计划重新执行：\n"
                    f"{self.plan.to_json()}"
                ),
            }
        )
        return None, "restored"

    def _finish_at_step_limit(self, task: str) -> str:
        """轮次耗尽时用已有证据做确定性收尾核验。"""
        if self.plan is not None and self.context is not None:
            final_decision = verify_completion(
                self.context.context.plan or self.plan,
                self.context.context.tool_results,
                self.context.context.test_results,
                self._acceptance_results(),
            )
            requirement_check = TaskRequirementVerifier().verify(task, self.workspace)
            self.trace.record(
                "completion_checked",
                completion_check_id="completion-step-limit",
                status=final_decision.status.value,
                completed=final_decision.completed,
                reason=final_decision.reason,
                pending_step_ids=final_decision.pending_step_ids,
                failed_step_ids=final_decision.failed_step_ids,
                evidence=final_decision.evidence,
                trigger="step_limit_boundary",
            )
            if final_decision.completed and requirement_check.passed:
                answer = (
                    "最后一个允许轮次中的工具已完成任务，"
                    "控制层已根据计划、测试和明确需求证据完成收尾核验。"
                )
                self.trace.record(
                    "task_completed",
                    answer=answer,
                    reason="verified_at_step_limit",
                )
                return answer

        self.trace.record(
            "task_stopped",
            reason="max_steps",
            limit=self.max_steps,
        )

        return (
            f"已达到最大执行轮数 {self.max_steps}，停止自动执行。"
            "请检查当前工作区的代码和测试结果，再决定是否继续。"
        )


    @staticmethod
    def _is_boundary_failure(result: ToolResult) -> bool:
        """判断工具失败是否属于不可继续的工作区边界违规。"""
        message = result.error or ""
        markers = ("工作目录之外", "工作区之外", "路径越界", "符号链接")
        return any(marker in message for marker in markers)

    def _acceptance_results(self) -> tuple[AcceptanceResult, ...]:
        """按验收项核验当前证据，并把结果写入 Trace。"""
        intent = self.intent
        if intent is None or not intent.acceptance:
            return ()
        results = AcceptanceVerifier().verify(
            intent,
            self.workspace,
            self.safety.change_set.summary(),
            structured_test_evidence(self.context.context.test_results),
            workspace_read=self._has_successful_read(),
        )
        self.trace.record(
            "acceptance_checked",
            passed=[item.item.item_id for item in results if item.passed],
            failed=[item.item.item_id for item in results if not item.passed],
            missing=[item.item.text for item in results if not item.passed],
        )
        return results

    def _has_successful_read(self) -> bool:
        """判断本任务是否发生过成功的读取或搜索。"""
        if self.trace is None:
            return False
        read_tools = {"read_file", "list_files", "search_code"}
        return any(
            event.get("event") == "tool_call_finished"
            and event.get("details", {}).get("tool") in read_tools
            and event.get("details", {}).get("status") == "success"
            for event in self.trace.events
        )

    def _has_test_sources(self) -> bool:
        """检查工作区是否已经存在可被 pytest 收集的测试源文件。"""
        ignored = {".git", ".venv", ".pytest_cache", "__pycache__"}
        for path in self.workspace.root.rglob("*.py"):
            relative = path.relative_to(self.workspace.root)
            if any(part in ignored for part in relative.parts):
                continue
            if path.name == "conftest.py" or path.name.startswith("test_"):
                return True
        return False
