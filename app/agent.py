
import json

from openai.types.chat import ChatCompletionMessageParam

from app.completion import CompletionStatus, verify_completion
from app.complexity import judge_task_complexity
from app.context import ContextManager
from app.explorer import ProjectExplorer
from app.model import ModelAdapter
from app.planner import TaskPlan, create_task_plan
from app.registry import DEFAULT_TOOL_REGISTRY, ToolRegistry
from app.result import ToolResult, ToolStatus
from app.task_state import PlanStateManager
from app.tools import set_workspace
from app.trace import ExecutionTrace
from app.workspace import WorkspaceContext

SYSTEM_PROMPT = """
你是一个能够实际编写和修改 Python 代码的 Coding Agent。

你的受控工作区是：{workspace}
所有文件路径都必须相对于该工作区，并通过提供的工具操作。

工作规则：
1. 先查看工作区，了解现有目录和文件，不要假设文件内容。
2. 根据用户任务制定合理的执行步骤，并逐步完成任务。
3. 需要新目录时，先调用 create_directory。
4. 需要新文件时，调用 create_file；修改已有文件时，调用 edit_file。
5. 不要在未读取文件内容的情况下盲目修改已有文件。
6. 修改或创建代码后，如果任务适合测试，应调用 run_tests。
7. 如果测试失败，分析错误，修改代码，再次运行测试。
8. 根据工具实际返回的结果判断操作是否成功，不得编造执行结果。
9. 如果工具返回错误，先分析错误原因，再决定下一步。
10. 不要执行用户任务之外的操作，不要声称完成了实际没有完成的工作。
11. 完成任务后，用中文总结修改了哪些文件、做了什么，以及测试结果。
12. 不要直接把需要执行的代码当成最终答案，应使用工具实际创建或修改文件。
"""


class Agent:
    """通过大模型和工具循环完成编程任务。"""

    def __init__(
        self,
        workspace: WorkspaceContext,
        max_steps: int = 10,
        max_tool_calls: int = 20,
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

    @staticmethod
    def _print_trace_summary(trace: ExecutionTrace) -> None:
        """统一输出任务的 Trace 统计摘要。"""
        print("\n[Trace Summary]")
        print(trace.summary())

    def run(self, task: str) -> str:
        """执行一个用户任务，返回最终结果。"""

        trace = ExecutionTrace()
        self.last_trace = trace
        trace.record(
            "task_started",
            task=task,
            workspace=str(self.workspace.root),
        )
        project_snapshot = ProjectExplorer(self.workspace).explore()
        decision = judge_task_complexity(task, project_snapshot)
        self.last_plan = (
            create_task_plan(task, project_snapshot, decision)
            if decision.requires_planning
            else None
        )
        state_manager = (
            PlanStateManager(self.last_plan) if self.last_plan is not None else None
        )
        self.last_context = ContextManager(
            task, project_snapshot, self.last_plan
        )

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

        for step in range(1, self.max_steps + 1):
            print(f"\n[Agent] 第 {step}/{self.max_steps} 轮")
            trace.record("model_request_started", step=step)

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
                )
                self._print_trace_summary(trace)
                raise
            except Exception as exc:  # noqa: BLE001 - model boundary
                trace.record(
                    "model_request_failed",
                    step=step,
                    error=str(exc),
                )
                trace.record(
                    "task_failed",
                    reason="model_request_failed",
                )
                self._print_trace_summary(trace)
                return f"模型请求失败：{exc}"

            if not response.choices:
                trace.record("model_response_empty", step=step)
                trace.record(
                    "task_failed",
                    reason="empty_model_response",
                )
                self._print_trace_summary(trace)
                return "模型没有返回任何候选响应。"

            message = response.choices[0].message

            trace.record(
                "model_response_received",
                step=step,
                has_tool_calls=bool(message.tool_calls),
                tool_call_count=len(message.tool_calls or []),
            )

            # 没有工具调用，说明模型准备直接回复用户。
            if not message.tool_calls:
                answer = message.content or "任务结束，但模型没有提供文字总结。"
                if self.last_plan is not None and self.last_context is not None:
                    decision = verify_completion(
                        self.last_context.context.plan or self.last_plan,
                        self.last_context.context.tool_results,
                        self.last_context.context.test_results,
                    )
                    if decision.status is not CompletionStatus.COMPLETE:
                        messages.append(
                            {"role": "assistant", "content": answer}
                        )
                        messages.append(
                            {
                                "role": "system",
                                "content": (
                                    "完成判定未通过，请继续执行："
                                    f"{decision.reason}"
                                ),
                            }
                        )
                        continue
                trace.record("task_completed", answer=answer)
                print("\n[Agent] 任务结束")
                self._print_trace_summary(trace)
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
            for tool_call in message.tool_calls:
                if tool_call_count >= self.max_tool_calls:
                    reason = (
                        f"已达到工具调用上限 {self.max_tool_calls}，"
                        "停止执行。请缩小任务范围后重试。"
                    )
                    trace.record(
                        "task_stopped",
                        reason="max_tool_calls",
                        limit=self.max_tool_calls,
                    )
                    self._print_trace_summary(trace)
                    return reason

                tool_name = tool_call.function.name

                try:
                    arguments = json.loads(tool_call.function.arguments)

                    if not isinstance(arguments, dict):
                        raise ValueError(  # noqa: TRY004 - normalized below
                            "工具参数必须是 JSON 对象。"
                        )

                except (json.JSONDecodeError, ValueError) as exc:
                    tool_result = ToolResult.failure(
                        f"工具参数解析失败：{exc}"
                    )
                    if self.last_context is not None:
                        self.last_context.add_tool_result(tool_result)
                    trace.record(
                        "tool_arguments_invalid",
                        tool=tool_name,
                        status=tool_result.status.value,
                        error=str(exc),
                    )
                    print(
                        f"[Tool Error] {tool_name}: "
                        f"{tool_result.to_json()}"
                    )

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
                )
                print(
                    f"[Tool] {tool_name}"
                    f"({json.dumps(arguments, ensure_ascii=False)})"
                )

                try:
                    tool_result = self.registry.execute(tool_name, arguments)
                except KeyboardInterrupt:
                    trace.record(
                        "task_stopped",
                        reason="user_interrupted",
                        tool=tool_name,
                    )
                    self._print_trace_summary(trace)
                    raise
                except Exception as exc:  # noqa: BLE001 - tool boundary
                    tool_result = ToolResult.failure(
                        f"工具执行异常：{exc}"
                    )

                tool_call_count += 1

                if self.last_context is not None:
                    self.last_context.add_tool_result(tool_result)
                if state_manager is not None:
                    current = state_manager.state().current_step_id
                    if current is None:
                        pending = next(
                            (
                                step.step_id
                                for step in state_manager.state().plan.steps
                                if step.status.value == "pending"
                            ),
                            None,
                        )
                        if pending is not None:
                            state_manager.start_step(pending)
                            current = pending
                    if current is not None:
                        if tool_result.status is ToolStatus.SUCCESS:
                            state_manager.complete_step(current, tool_result.result)
                        elif tool_result.status is ToolStatus.CANCELLED:
                            state_manager.cancel_step(
                                current, tool_result.result or "工具已取消"
                            )
                        else:
                            state_manager.fail_step(
                                current, tool_result.error or "工具执行失败"
                            )
                        self.last_plan = state_manager.state().plan
                        self.last_context.update_plan(
                            self.last_plan,
                            state_manager.state().current_step_id,
                        )

                trace.record(
                    "tool_call_finished",
                    tool=tool_name,
                    tool_call_id=tool_call.id,
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

                # 工具返回错误时，停止本批次剩余调用，交由模型重新判断。
                if tool_result.status is ToolStatus.FAILURE:
                    break

        trace.record(
            "task_stopped",
            reason="max_steps",
            limit=self.max_steps,
        )
        self._print_trace_summary(trace)

        return (
            f"已达到最大执行轮数 {self.max_steps}，停止自动执行。"
            "请检查当前工作区的代码和测试结果，再决定是否继续。"
        )
