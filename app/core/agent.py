"""Agent 主循环：协调规划、工具调用、安全监控与任务完成判定。"""

import json

from openai.types.chat import ChatCompletionMessageParam

from app.core.completion import (
    CompletionDecision,
    CompletionStatus,
    CompletionVerifier,
    verify_completion,
)
from app.core.complexity import judge_task_complexity
from app.core.context import ContextManager
from app.core.final_verification import FinalStateVerifier
from app.core.planner import TaskPlan, create_task_plan
from app.core.recovery import RecoveryAction, RecoveryDecision
from app.core.requirements import TaskRequirementVerifier
from app.core.result_aggregator import TaskResultAggregator
from app.core.task_result import TaskResult, TaskStatus
from app.core.task_safety import TaskConstraintViolation, TaskSafetySession
from app.core.task_state import PlanStateManager
from app.runtime.explorer import ProjectExplorer
from app.runtime.model import ModelAdapter
from app.runtime.trace import ExecutionTrace
from app.runtime.workspace import WorkspaceContext
from app.tools import set_workspace
from app.tools.change_set import ChangeSetSummary
from app.tools.git_repository import inspect_git_repository
from app.tools.registry import DEFAULT_TOOL_REGISTRY, ToolRegistry
from app.tools.result import ToolResult, ToolStatus
from app.tools.rollback import AuthorizedRollback, RollbackStatus

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
"""


class Agent:
    """通过大模型和工具循环完成编程任务。"""

    def __init__(
        self,
        workspace: WorkspaceContext,
        max_steps: int = 20,
        max_tool_calls: int = 40,
        registry: ToolRegistry = DEFAULT_TOOL_REGISTRY,
    ) -> None:
        self.workspace = workspace
        self.registry = registry
        set_workspace(workspace)
        self.model = ModelAdapter()
        self.max_steps = max_steps
        self.max_tool_calls = max_tool_calls
        self.last_trace: ExecutionTrace | None = None
        self.last_plan: TaskPlan | None = None
        self.last_context: ContextManager | None = None
        self.last_change_set = None
        self.last_recovery_decision: RecoveryDecision | None = None
        self.last_result: TaskResult | None = None
        self.last_safety: TaskSafetySession | None = None

    @staticmethod
    def _print_trace_summary(trace: ExecutionTrace) -> None:
        """统一输出任务的 Trace 统计摘要。"""
        print("\n[Trace Summary]")
        print(trace.summary())

    def run(self, task: str) -> str:
        """执行任务并生成结构化结果，同时兼容返回原始文本结果。"""
        self.last_result = None
        self.last_safety = None
        try:
            answer = self._run_legacy(task)
        finally:
            self._build_task_result(task)
            if self.last_trace is not None:
                self._print_trace_summary(self.last_trace)
        return answer

    def _build_task_result(self, task: str) -> None:
        """从本次 Agent 运行证据生成并保存 TaskResult。"""
        if (
            self.last_trace is None
            or self.last_context is None
            or self.last_change_set is None
        ):
            return
        try:
            final_evidence = FinalStateVerifier().verify(
                self.workspace,
                self.last_change_set,
                self.last_trace,
            )
        except Exception as exc:  # noqa: BLE001 - reporting boundary
            reason = f"最终状态核验失败：{exc}"
            self.last_trace.record("final_state_verification_failed", error=str(exc))
            self.last_result = TaskResultAggregator().aggregate(
                self.last_context.context,
                ChangeSetSummary(touched_count=0, unchanged_count=0, changes=()),
                completion=CompletionDecision(CompletionStatus.BLOCKED, reason),
                trace=self.last_trace,
                recovery=self.last_recovery_decision,
                repair_attempts=(
                    self.last_safety.test_repair_attempts
                    if self.last_safety is not None
                    else 0
                ),
                task_status=TaskStatus.BLOCKED,
            )
            return
        completion_status = None
        for event in reversed(self.last_trace.events):
            if event["event"] == "completion_checked":
                completion_status = event.get("details", {}).get("status")
                break
        task_status = None
        if completion_status == "blocked":
            task_status = TaskStatus.BLOCKED
        repair_attempts = (
            self.last_safety.test_repair_attempts if self.last_safety is not None else 0
        )
        self.last_result = TaskResultAggregator().aggregate(
            self.last_context.context,
            final_evidence.changes,
            trace=self.last_trace,
            recovery=self.last_recovery_decision,
            repair_attempts=repair_attempts,
            task_status=task_status,
            final_evidence=final_evidence,
        )

    def _run_legacy(self, task: str) -> str:
        """执行原有 Agent 工具循环并返回兼容的文本结果。"""

        trace = ExecutionTrace()
        self.last_trace = trace
        trace.record(
            "task_started",
            task=task,
            workspace=str(self.workspace.root),
        )
        project_snapshot = ProjectExplorer(self.workspace).explore()
        git_info = inspect_git_repository(self.workspace)
        trace.record("git_baseline_detected", **git_info.to_dict())
        decision = judge_task_complexity(task, project_snapshot)
        trace.record(
            "complexity_decided",
            complexity=decision.complexity.value,
            requires_planning=decision.requires_planning,
            reasons=decision.reasons,
        )
        self.last_plan = (
            create_task_plan(task, project_snapshot, decision)
            if decision.requires_planning
            else None
        )
        if self.last_plan is not None:
            trace.record("plan_created", plan=self.last_plan.to_dict())
        state_manager = (
            PlanStateManager(self.last_plan) if self.last_plan is not None else None
        )
        self.last_context = ContextManager(task, project_snapshot, self.last_plan)
        safety = TaskSafetySession(self.workspace, task=task)
        self.last_safety = safety
        self.last_change_set = safety.change_set

        messages: list[ChatCompletionMessageParam] = [
            {
                "role": "system",
                "content": SYSTEM_PROMPT.format(workspace=self.workspace.root),
            },
            {
                "role": "system",
                "content": (
                    "以下是任务开始时自动生成的项目快照。"
                    "它只用于初步定位，修改前仍需读取相关文件：\n"
                    f"{project_snapshot.to_prompt()}"
                ),
            },
            {
                "role": "user",
                "content": task,
            },
        ]
        if self.last_plan is not None:
            messages.insert(
                2,
                {
                    "role": "system",
                    "content": (
                        "这是本次任务的结构化计划，请按步骤推进；"
                        "只有工具结果和测试结果证明完成后才能结束：\n"
                        f"{self.last_plan.to_json()}"
                    ),
                },
            )

        tool_call_count = 0
        rollback_denied = False

        for step in range(1, self.max_steps + 1):
            print(f"\n[Agent] 第 {step}/{self.max_steps} 轮")
            request_id = f"model-{step}"
            trace.record("model_request_started", step=step, request_id=request_id)

            try:
                response = self.model.chat(
                    messages=messages,
                    tools=self.registry.model_definitions(),
                )
            except KeyboardInterrupt:
                trace.record(
                    "task_stopped",
                    reason="user_interrupted",
                    step=step,
                    request_id=request_id,
                )
                raise
            except Exception as exc:  # noqa: BLE001 - model boundary
                trace.record(
                    "model_request_failed",
                    step=step,
                    request_id=request_id,
                    error=str(exc),
                )
                trace.record(
                    "task_failed",
                    reason="model_request_failed",
                    request_id=request_id,
                )
                return f"模型请求失败：{exc}"

            if not response.choices:
                trace.record(
                    "model_response_empty",
                    step=step,
                    request_id=request_id,
                )
                trace.record(
                    "task_failed",
                    reason="empty_model_response",
                    request_id=request_id,
                )
                return "模型没有返回任何候选响应。"

            message = response.choices[0].message

            trace.record(
                "model_response_received",
                step=step,
                request_id=request_id,
                has_tool_calls=bool(message.tool_calls),
                tool_call_count=len(message.tool_calls or []),
            )

            # 没有工具调用，说明模型准备直接回复用户。
            if not message.tool_calls:
                answer = message.content or "任务结束，但模型没有提供文字总结。"
                if self.last_context is not None:
                    completion_check_id = f"completion-{step}"
                    if self.last_plan is not None:
                        decision = verify_completion(
                            self.last_context.context.plan or self.last_plan,
                            self.last_context.context.tool_results,
                            self.last_context.context.test_results,
                        )
                    else:
                        decision = CompletionVerifier().verify_basic(
                            self.last_context.context.tool_results,
                            self.last_context.context.test_results,
                        )
                    trace.record(
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
                        task, self.workspace
                    )
                    trace.record(
                        "requirements_checked",
                        completion_check_id=completion_check_id,
                        passed=requirement_check.passed,
                        missing=requirement_check.missing,
                    )
                    if not requirement_check.passed:
                        messages.append({"role": "assistant", "content": answer})
                        messages.append(
                            {
                                "role": "system",
                                "content": (
                                    "最终代码尚未满足用户明确要求，请继续修正："
                                    + "；".join(requirement_check.missing)
                                ),
                            }
                        )
                        continue
                    if decision.status is not CompletionStatus.COMPLETE:
                        if safety.validation_only:
                            trace.record(
                                "task_stopped",
                                reason="validation_incomplete",
                                error=decision.reason,
                                completion_check_id=completion_check_id,
                            )
                            return decision.reason
                        unresolved_test = safety.unresolved_test_reason()
                        if unresolved_test is not None:
                            trace.record(
                                "task_stopped",
                                reason="unresolved_test_failure",
                                error=unresolved_test,
                                completion_check_id=completion_check_id,
                            )
                            return unresolved_test
                        recovery = safety.observe_completion(decision.status)
                        self.last_recovery_decision = recovery
                        if recovery.action is not RecoveryAction.CONTINUE:
                            trace.record(
                                "recovery_decided",
                                recovery_id=f"recovery-{completion_check_id}",
                                completion_check_id=completion_check_id,
                                action=recovery.action.value,
                                reason=recovery.reason,
                                triggers=recovery.triggers,
                                recoverable_paths=recovery.recoverable_paths,
                            )
                        if recovery.action is RecoveryAction.SUGGEST_ROLLBACK:
                            if rollback_denied:
                                reason = (
                                    "用户拒绝回滚，当前修改已保留；"
                                    "请确认是否继续执行后续修复。"
                                )
                                trace.record(
                                    "user_confirmation_required",
                                    reason=reason,
                                    completion_check_id=completion_check_id,
                                )
                                return reason
                            trace.record(
                                "rollback_authorization_requested",
                                recovery_id=f"recovery-{completion_check_id}",
                                completion_check_id=completion_check_id,
                                paths=recovery.recoverable_paths,
                                reason=recovery.reason,
                            )
                            rollback = AuthorizedRollback(
                                safety.change_set, self.registry.authorizer
                            ).execute()
                            trace.record(
                                "rollback_finished",
                                recovery_id=f"recovery-{completion_check_id}",
                                completion_check_id=completion_check_id,
                                status=rollback.status.value,
                                reason=rollback.reason,
                                restored_paths=rollback.restored_paths,
                                pending_paths=rollback.pending_paths,
                            )
                            if rollback.status is not RollbackStatus.RESTORED:
                                reason = f"恢复未完成：{rollback.reason}"
                                if rollback.status is RollbackStatus.CANCELLED:
                                    rollback_denied = True
                                    reason = (
                                        "用户拒绝回滚，当前修改已保留；"
                                        "请确认是否继续执行后续修复。"
                                    )
                                    trace.record(
                                        "user_confirmation_required",
                                        reason=reason,
                                        completion_check_id=completion_check_id,
                                    )
                                    return reason
                                trace.record(
                                    "task_stopped",
                                    reason=reason,
                                    completion_check_id=completion_check_id,
                                )
                                return reason
                            post_rollback = safety.change_set.summary()
                            trace.record(
                                "change_set_updated",
                                recovery_id=f"recovery-{completion_check_id}",
                                completion_check_id=completion_check_id,
                                comparison_basis="task_initial_state",
                                reason="rollback_verified",
                                touched_count=post_rollback.touched_count,
                                unchanged_count=post_rollback.unchanged_count,
                                changed_count=post_rollback.net_changed_count,
                                net_changed_count=post_rollback.net_changed_count,
                                created_paths=post_rollback.created_paths,
                                modified_paths=post_rollback.modified_paths,
                                deleted_paths=post_rollback.deleted_paths,
                            )
                            safety.reset_after_recovery(rollback.status)
                            self.last_change_set = safety.change_set
                            project_snapshot = ProjectExplorer(self.workspace).explore()
                            new_complexity = judge_task_complexity(
                                task, project_snapshot
                            )
                            self.last_plan = create_task_plan(
                                task, project_snapshot, new_complexity
                            )
                            state_manager = PlanStateManager(self.last_plan)
                            self.last_context = ContextManager(
                                task, project_snapshot, self.last_plan
                            )
                            messages.append(
                                {
                                    "role": "system",
                                    "content": (
                                        "本次任务变更已授权回滚。项目快照和计划已刷新，"
                                        "请从新计划重新执行：\n"
                                        f"{self.last_plan.to_json()}"
                                    ),
                                }
                            )
                            continue
                        if recovery.action is RecoveryAction.STOP:
                            trace.record(
                                "task_stopped",
                                reason=recovery.reason,
                                completion_check_id=completion_check_id,
                            )
                            return recovery.reason
                        messages.append({"role": "assistant", "content": answer})
                        messages.append(
                            {
                                "role": "system",
                                "content": (
                                    f"完成判定未通过，请继续执行：{decision.reason}"
                                ),
                            }
                        )
                        continue
                trace.record(
                    "task_completed",
                    answer=answer,
                    request_id=request_id,
                )
                print("\n[Agent] 任务结束")
                return answer

            # 保存模型的工具调用消息，供下一轮对话使用。
            messages.append(
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
                if tool_call_count >= self.max_tool_calls:
                    reason = (
                        f"已达到工具调用上限 {self.max_tool_calls}，"
                        "停止执行。请缩小任务范围后重试。"
                    )
                    trace.record(
                        "task_stopped",
                        reason="max_tool_calls",
                        limit=self.max_tool_calls,
                        request_id=request_id,
                    )
                    return reason

                tool_name = tool_call.function.name

                try:
                    arguments = json.loads(tool_call.function.arguments)

                    if not isinstance(arguments, dict):
                        raise ValueError(  # noqa: TRY004 - normalized below
                            "工具参数必须是 JSON 对象。"
                        )

                except (json.JSONDecodeError, ValueError) as exc:
                    tool_result = ToolResult.failure(f"工具参数解析失败：{exc}")
                    if self.last_context is not None:
                        before_trim = self.last_context.context.truncated_count
                        self.last_context.add_tool_result(tool_result)
                        if self.last_context.context.truncated_count > before_trim:
                            trace.record(
                                "context_trimmed",
                                tool_call_id=tool_call.id,
                                truncated_count=(
                                    self.last_context.context.truncated_count
                                ),
                            )
                    trace.record(
                        "tool_arguments_invalid",
                        tool=tool_name,
                        tool_call_id=tool_call.id,
                        request_id=request_id,
                        status=tool_result.status.value,
                        error=str(exc),
                    )
                    print(f"[Tool Error] {tool_name}: {tool_result.to_json()}")

                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": tool_result.to_json(),
                        }
                    )

                    # 让模型根据错误重新决定，而不是继续当前批次。
                    break

                trace.record(
                    "tool_call_started",
                    tool=tool_name,
                    arguments=arguments,
                    tool_call_id=tool_call.id,
                    request_id=request_id,
                )
                print(
                    f"[Tool] {tool_name}({json.dumps(arguments, ensure_ascii=False)})"
                )

                cached_result = safety.completed_mutation(tool_name, arguments)
                if cached_result is not None:
                    tool_call_count += 1
                    trace.record(
                        "duplicate_tool_call_skipped",
                        tool=tool_name,
                        arguments=arguments,
                        tool_call_id=tool_call.id,
                        request_id=request_id,
                        reason="相同写操作已成功完成，复用先前结果。",
                    )
                    trace.record(
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
                    print(f"[Tool Result] {result_json[:500]}")
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": result_json,
                        }
                    )
                    continue

                rerun_block_reason = safety.test_rerun_block_reason(
                    tool_name, arguments
                )
                if rerun_block_reason is not None:
                    tool_call_count += 1
                    tool_result = ToolResult.cancelled(rerun_block_reason)
                    if self.last_context is not None:
                        self.last_context.add_tool_result(tool_result)
                    trace.record(
                        "safety_rejected",
                        rejection_type="test_rerun_without_mutation",
                        tool=tool_name,
                        tool_call_id=tool_call.id,
                        request_id=request_id,
                        reason=rerun_block_reason,
                    )
                    trace.record(
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
                    print(f"[Tool Result] {result_json[:500]}")
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": result_json,
                        }
                    )
                    messages.append(
                        {
                            "role": "system",
                            "content": rerun_block_reason,
                        }
                    )
                    break

                snapshot_paths: tuple[str, ...] = ()
                pre_tool_changes: dict[str, dict] = {}
                try:
                    safety.validate_tool(tool_name, arguments)
                    snapshot_paths = safety.prepare_tool(tool_name, arguments)
                    pre_tool_changes = {
                        change.path: change.to_dict()
                        for change in safety.change_set.summary().changes
                    }
                    tool_result = self.registry.execute(tool_name, arguments)
                except KeyboardInterrupt:
                    trace.record(
                        "task_stopped",
                        reason="user_interrupted",
                        tool=tool_name,
                        tool_call_id=tool_call.id,
                        request_id=request_id,
                    )
                    raise
                except TaskConstraintViolation as exc:
                    tool_result = ToolResult.cancelled(str(exc))
                    tool_call_count += 1
                    trace.record(
                        "safety_rejected",
                        rejection_type="user_constraint",
                        tool=tool_name,
                        tool_call_id=tool_call.id,
                        request_id=request_id,
                        reason=str(exc),
                    )
                    trace.record(
                        "tool_call_finished",
                        tool=tool_name,
                        tool_call_id=tool_call.id,
                        request_id=request_id,
                        status=tool_result.status.value,
                        result=tool_result.result,
                        error=tool_result.error,
                        rejected=True,
                    )
                    trace.record(
                        "task_stopped",
                        reason="user_constraint_violation",
                        error=str(exc),
                        tool_call_id=tool_call.id,
                        request_id=request_id,
                    )
                    return str(exc)
                except Exception as exc:  # noqa: BLE001 - tool boundary
                    tool_result = ToolResult.failure(f"工具执行异常：{exc}")

                tool_call_count += 1
                reported_mutation = safety.record_completed_mutation(
                    tool_name, arguments, tool_result
                )
                post_tool_changes = safety.change_set.summary()
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
                    trace.record(
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
                    safety.record_mutation_applied()
                    trace.record(
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
                    trace.record(
                        "safety_rejected",
                        rejection_type="workspace_boundary",
                        tool=tool_name,
                        tool_call_id=tool_call.id,
                        request_id=request_id,
                        reason=reason,
                    )
                    trace.record(
                        "tool_call_finished",
                        tool=tool_name,
                        tool_call_id=tool_call.id,
                        request_id=request_id,
                        status=tool_result.status.value,
                        result=tool_result.result,
                        error=tool_result.error,
                        rejected=True,
                    )
                    trace.record(
                        "task_stopped",
                        reason="workspace_boundary_violation",
                        error=reason,
                        tool_call_id=tool_call.id,
                        request_id=request_id,
                    )
                    return reason

                if tool_result.status is ToolStatus.FAILURE and safety.validation_only:
                    reason = tool_result.error or "验收工具执行失败，已停止继续探索。"
                    trace.record(
                        "tool_call_finished",
                        tool=tool_name,
                        tool_call_id=tool_call.id,
                        request_id=request_id,
                        status=tool_result.status.value,
                        result=tool_result.result,
                        error=tool_result.error,
                    )
                    trace.record(
                        "task_stopped",
                        reason="validation_failure",
                        error=reason,
                        tool_call_id=tool_call.id,
                        request_id=request_id,
                    )
                    return reason

                if self.last_context is not None:
                    before_trim = self.last_context.context.truncated_count
                    if tool_name == "run_tests":
                        self.last_context.add_test_result(
                            tool_result.result or tool_result.error or "测试执行失败"
                        )
                    else:
                        self.last_context.add_tool_result(tool_result)
                    if self.last_context.context.truncated_count > before_trim:
                        trace.record(
                            "context_trimmed",
                            tool_call_id=tool_call.id,
                            truncated_count=self.last_context.context.truncated_count,
                        )
                if state_manager is not None:
                    current = state_manager.state().current_step_id
                    if current is None:
                        pending = state_manager.match_step(tool_name, arguments)
                        if pending is not None:
                            state_manager.start_step(pending)
                            current = pending
                    if current is not None:
                        if tool_result.status is ToolStatus.SUCCESS:
                            if state_manager.can_complete_step(
                                current, tool_name, arguments, tool_result
                            ):
                                state_manager.complete_step(current, tool_result.result)
                            # 工具成功但未满足当前步骤条件时，保留 running 状态，
                            # 等待匹配的工具结果，不能误标记为失败。
                        elif tool_result.status is ToolStatus.CANCELLED:
                            state_manager.cancel_step(
                                current, tool_result.result or "工具已取消"
                            )
                        else:
                            state_manager.fail_step(
                                current, tool_result.error or "工具执行失败"
                            )
                        self.last_plan = state_manager.state().plan
                        updated_step = next(
                            step
                            for step in self.last_plan.steps
                            if step.step_id == current
                        )
                        trace.record(
                            "plan_step_status_changed",
                            tool_call_id=tool_call.id,
                            step_id=current,
                            status=updated_step.status.value,
                        )
                        self.last_context.update_plan(
                            self.last_plan,
                            state_manager.state().current_step_id,
                        )

                plan_failed = bool(
                    state_manager is not None and state_manager.state().failed_step_ids
                )
                try:
                    recovery = safety.observe(
                        tool_name,
                        tool_result,
                        plan_failed=plan_failed,
                        arguments=arguments,
                    )
                except Exception as exc:  # noqa: BLE001 - safety boundary
                    recovery = RecoveryDecision(
                        RecoveryAction.STOP,
                        f"无法生成安全恢复决策：{exc}",
                    )
                self.last_recovery_decision = recovery
                change_summary = safety.change_set.summary()
                trace.record(
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
                    trace.record(
                        "recovery_decided",
                        recovery_id=recovery_id,
                        tool_call_id=tool_call.id,
                        request_id=request_id,
                        action=recovery.action.value,
                        reason=recovery.reason,
                        triggers=recovery.triggers,
                        recoverable_paths=recovery.recoverable_paths,
                    )

                trace.record(
                    "tool_call_finished",
                    tool=tool_name,
                    tool_call_id=tool_call.id,
                    request_id=request_id,
                    status=tool_result.status.value,
                    result=tool_result.result,
                    error=tool_result.error,
                    is_error=tool_result.status is ToolStatus.FAILURE,
                )
                result_json = tool_result.to_json()
                print(f"[Tool Result] {result_json[:500]}")

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": result_json,
                    }
                )

                if recovery.action is RecoveryAction.SUGGEST_ROLLBACK:
                    if rollback_denied:
                        reason = (
                            "用户拒绝回滚，当前修改已保留；请确认是否继续执行后续修复。"
                        )
                        trace.record(
                            "user_confirmation_required",
                            reason=reason,
                            recovery_id=recovery_id,
                            tool_call_id=tool_call.id,
                        )
                        return reason
                    trace.record(
                        "rollback_authorization_requested",
                        recovery_id=recovery_id,
                        tool_call_id=tool_call.id,
                        paths=recovery.recoverable_paths,
                        reason=recovery.reason,
                    )
                    rollback = AuthorizedRollback(
                        safety.change_set, self.registry.authorizer
                    ).execute()
                    trace.record(
                        "rollback_finished",
                        recovery_id=recovery_id,
                        tool_call_id=tool_call.id,
                        status=rollback.status.value,
                        reason=rollback.reason,
                        restored_paths=rollback.restored_paths,
                        pending_paths=rollback.pending_paths,
                    )
                    if rollback.status is not RollbackStatus.RESTORED:
                        reason = f"恢复未完成：{rollback.reason}"
                        if rollback.status is RollbackStatus.CANCELLED:
                            rollback_denied = True
                            reason = (
                                "用户拒绝回滚，当前修改已保留；"
                                "请确认是否继续执行后续修复。"
                            )
                            trace.record(
                                "user_confirmation_required",
                                reason=reason,
                                recovery_id=recovery_id,
                                tool_call_id=tool_call.id,
                            )
                            return reason
                        trace.record(
                            "task_stopped",
                            reason=reason,
                            recovery_id=recovery_id,
                            tool_call_id=tool_call.id,
                        )
                        return reason

                    post_rollback = safety.change_set.summary()
                    trace.record(
                        "change_set_updated",
                        recovery_id=recovery_id,
                        tool_call_id=tool_call.id,
                        comparison_basis="task_initial_state",
                        reason="rollback_verified",
                        touched_count=post_rollback.touched_count,
                        unchanged_count=post_rollback.unchanged_count,
                        changed_count=post_rollback.net_changed_count,
                        net_changed_count=post_rollback.net_changed_count,
                        created_paths=post_rollback.created_paths,
                        modified_paths=post_rollback.modified_paths,
                        deleted_paths=post_rollback.deleted_paths,
                    )
                    safety.reset_after_recovery(rollback.status)
                    self.last_change_set = safety.change_set
                    project_snapshot = ProjectExplorer(self.workspace).explore()
                    decision = judge_task_complexity(task, project_snapshot)
                    self.last_plan = create_task_plan(task, project_snapshot, decision)
                    state_manager = PlanStateManager(self.last_plan)
                    self.last_context = ContextManager(
                        task, project_snapshot, self.last_plan
                    )
                    messages.append(
                        {
                            "role": "system",
                            "content": (
                                "本次任务变更已授权回滚。项目快照和计划已刷新，"
                                "请从新计划重新执行：\n"
                                f"{self.last_plan.to_json()}"
                            ),
                        }
                    )
                    break

                if recovery.action is RecoveryAction.REPLAN:
                    project_snapshot = ProjectExplorer(self.workspace).explore()
                    decision = judge_task_complexity(task, project_snapshot)
                    self.last_plan = create_task_plan(task, project_snapshot, decision)
                    state_manager = PlanStateManager(self.last_plan)
                    self.last_context.begin_replan(self.last_plan)
                    messages.append(
                        {
                            "role": "system",
                            "content": (
                                "当前执行失败，失败输出已保留。请先根据错误定位根因，"
                                "调用读取或编辑工具完成一次实际修复后再重跑测试；"
                                "不要在未修改相关代码时重复运行同一失败测试。"
                                "已刷新项目状态并重新规划：\n"
                                f"{self.last_plan.to_json()}"
                            ),
                        }
                    )
                    break

                if recovery.action is RecoveryAction.STOP:
                    trace.record(
                        "task_stopped",
                        reason=recovery.reason,
                        recovery_id=recovery_id,
                        tool_call_id=tool_call.id,
                    )
                    return recovery.reason

                # 批次采用失败关闭规则：失败或取消后均停止剩余调用，
                # 避免后续工具建立在无效前置结果之上。
                if tool_result.status is not ToolStatus.SUCCESS:
                    trace.record(
                        "tool_batch_aborted",
                        tool=tool_name,
                        tool_call_id=tool_call.id,
                        request_id=request_id,
                        status=tool_result.status.value,
                        skipped_calls=len(message.tool_calls) - call_index - 1,
                    )
                    break

        trace.record(
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
