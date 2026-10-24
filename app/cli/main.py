"""基于 Typer 和 Rich 的命令行交互入口。"""

import sys
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer
from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.key_binding import KeyBindings
from rich.console import Console, Group
from rich.markup import escape
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text

from app import tools
from app.cli.report import render_task_report
from app.core.agent import Agent
from app.core.task_result import TaskResult, TestEvidenceStatus
from app.runtime.workspace import WorkspaceContext


class OutputFormat(StrEnum):
    """CLI 支持的任务报告输出格式。"""

    TEXT = "text"
    JSON = "json"


app = typer.Typer(
    add_completion=False,
    invoke_without_command=True,
    no_args_is_help=False,
    rich_markup_mode="rich",
    help="在受控本地工作区中运行 Mini Coding Agent。",
)
console = Console()


class ProgressRenderer:
    """将关键 Trace 事件转换为面向用户的简洁 Rich 进度。"""

    def __init__(self, target_console: Console) -> None:
        self.console = target_console
        self.changed_paths: set[str] = set()
        self.analysis_rendered = False
        self.tests_rendered: set[str] = set()

    def reset(self) -> None:
        """开始新任务前清空已渲染的进度状态。"""
        self.changed_paths.clear()
        self.analysis_rendered = False
        self.tests_rendered.clear()

    def __call__(self, event: dict) -> None:
        """消费一条结构化 Trace 事件并按需渲染里程碑。"""
        name = event["event"]
        details = event["details"]
        if name == "complexity_decided" and not self.analysis_rendered:
            self.analysis_rendered = True
            self.console.print("[green]✓[/] [bold]分析任务[/]")
        elif name == "mutation_applied":
            path = details.get("path")
            if path and path not in self.changed_paths:
                self.changed_paths.add(path)
                action = {
                    "create_directory": "创建目录",
                    "create_file": "创建文件",
                    "delete_directory": "删除目录",
                    "delete_file": "删除文件",
                    "edit_file": "更新文件",
                }.get(details.get("tool"), "更新文件")
                self.console.print(
                    f"[green]✓[/] [bold]{action}[/] [cyan]{escape(str(path))}[/]"
                )
        elif name == "tool_call_finished" and details.get("tool") == "run_tests":
            target = _test_target(details)
            status = details.get("status")
            marker = f"{target}:{status}"
            if marker not in self.tests_rendered:
                self.tests_rendered.add(marker)
                passed = status == "success"
                color = "green" if passed else "yellow"
                symbol = "✓" if passed else "!"
                label = "测试通过" if status == "success" else "测试未通过，正在修复"
                self.console.print(
                    f"[{color}]{symbol}[/] [bold]{label}[/] [dim]{escape(target)}[/]"
                )


def _test_target(details: dict) -> str:
    """从结构化测试载荷中提取简短目标名称。"""
    payload = details.get("test")
    if isinstance(payload, dict):
        target = payload.get("target")
        if isinstance(target, str) and target:
            return target
    return "pytest"


def _display_result_text(value: str) -> str:
    """将内部停止原因转换为面向用户的中文说明。"""
    return {
        "max_steps": "已达到最大执行轮数，任务尚未完成。",
        "max_tool_calls": "已达到最大工具调用次数，任务尚未完成。",
    }.get(value, value)


def _input_bindings() -> KeyBindings:
    """创建 Enter 提交、Shift/Alt+Enter 换行的编辑器按键绑定。"""
    bindings = KeyBindings()

    @bindings.add("enter")
    def submit(event) -> None:
        """普通 Enter 提交整个输入缓冲区。"""
        event.current_buffer.validate_and_handle()

    @bindings.add("escape", "[", "2", "7", ";", "2", ";", "1", "3", "~")
    @bindings.add("escape", "[", "1", "3", ";", "2", "u")
    @bindings.add("escape", "enter")
    def insert_newline(event) -> None:
        """处理 Alt+Enter 和常见增强终端的 Shift+Enter 序列。"""
        event.current_buffer.insert_text("\n")

    return bindings


def _read_interactive_task() -> str | None:
    """通过支持 bracketed paste 的单缓冲编辑器读取一个任务。"""
    session: PromptSession[str] = PromptSession(
        multiline=True,
        key_bindings=_input_bindings(),
        prompt_continuation=lambda *_: "  … ",
    )
    try:
        value = session.prompt(
            HTML("<ansicyan><b>用户任务</b></ansicyan>  "),
            enable_open_in_editor=False,
        )
    except (EOFError, KeyboardInterrupt):
        return None
    if value.strip().lower() in {"exit", "quit"}:
        return "__EXIT__"
    return value.strip()


def _read_task() -> str | None:
    """按终端能力读取任务，管道输入继续兼容 END 分隔格式。"""

    if sys.stdin.isatty():
        return _read_interactive_task()

    try:
        first = input()
    except (EOFError, KeyboardInterrupt):
        return None

    if first.strip().lower() in {"exit", "quit"}:
        return "__EXIT__"
    if first.strip() == "END":
        return ""

    lines = [first]
    while True:
        try:
            line = input()
        except (EOFError, KeyboardInterrupt):
            return "\n".join(lines).strip()
        if line.strip() == "END":
            return "\n".join(lines).strip()
        lines.append(line)


def _resolve_workspace(path: Path) -> WorkspaceContext:
    """将 CLI 路径转换为受控工作区并生成易读的参数错误。"""

    try:
        return WorkspaceContext.from_path(path)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--workspace") from exc


def _render_user_result(result: TaskResult) -> Group:
    """生成不含底层 Trace 的默认用户结果视图。"""
    status_style = "bold green" if result.completed else "bold yellow"
    status_label = {
        "completed": "✓ 任务完成",
        "stopped": "! 任务已停止",
        "failed": "! 任务失败",
        "blocked": "! 任务受阻",
    }[result.status.value]
    blocks: list[Text] = [Text(status_label, style=status_style)]
    if result.summary:
        blocks.append(Text(_display_result_text(result.summary)))

    changes = result.changes
    paths = (
        [("创建", path) for path in changes.created]
        + [("修改", path) for path in changes.modified]
        + [("删除", path) for path in changes.deleted]
    )
    if paths:
        blocks.append(Text("\n文件变更", style="bold"))
        for action, path in paths:
            blocks.append(
                Text.assemble(
                    ("  ✓ ", "green"),
                    (path, "bold cyan"),
                    (f"  {action}", "dim"),
                )
            )

    if result.tests:
        blocks.append(Text("\n测试结果", style="bold"))
        for evidence in result.tests:
            passed = evidence.status is TestEvidenceStatus.PASSED
            blocks.append(
                Text.assemble(
                    ("  ✓ " if passed else "  ! ", "green" if passed else "yellow"),
                    (evidence.target, "cyan"),
                    ("  通过" if passed else "  未通过", "dim"),
                )
            )
    if result.stop_reason and result.stop_reason != result.summary:
        blocks.append(
            Text(f"\n{_display_result_text(result.stop_reason)}", style="yellow")
        )
    return Group(*blocks)


def _run_session(
    workspace: WorkspaceContext,
    output_format: OutputFormat,
    max_steps: int,
    max_tool_calls: int,
    debug: bool,
) -> None:
    """运行可连续接收任务的交互式 Agent 会话。"""

    console.print(
        Panel.fit(
            f"[bold]Mini Coding Agent[/]\n"
            f"工作目录：[cyan]{workspace.root}[/]\n"
            "Enter 提交 · Shift+Enter / Alt+Enter 换行 · 输入 exit 退出",
            border_style="cyan",
        )
    )
    progress = ProgressRenderer(console)
    agent = Agent(
        workspace=workspace,
        max_steps=max_steps,
        max_tool_calls=max_tool_calls,
        debug=debug,
        event_listener=None if debug else progress,
    )

    while True:
        task = _read_task()
        if task is None:
            console.print("\n[dim]程序已退出。[/]")
            break
        if task == "__EXIT__":
            console.print("[dim]程序已退出。[/]")
            break
        if not task.strip():
            continue

        progress.reset()
        console.print(Rule("[bold]用户任务[/]", style="dim"))
        console.print(task)
        console.print(Rule("[bold]执行进度[/]", style="dim"))
        try:
            result = agent.run(task)
            report = None
            if agent.last_result is not None:
                report = render_task_report(agent.last_result, output_format.value)
            if output_format is OutputFormat.JSON:
                console.print(
                    report or result,
                    markup=False,
                    highlight=False,
                    soft_wrap=True,
                )
            else:
                content = (
                    report or result
                    if debug or agent.last_result is None
                    else _render_user_result(agent.last_result)
                )
                console.print(
                    Panel(
                        content,
                        title="[bold]任务执行结果[/]",
                        border_style=(
                            "green"
                            if agent.last_result and agent.last_result.completed
                            else "yellow"
                        ),
                    )
                )
        except KeyboardInterrupt:
            console.print("\n[yellow]用户中断了当前任务。[/]")
        except Exception as exc:  # noqa: BLE001 - CLI task boundary
            console.print(f"\n[bold red]程序发生异常：[/]{exc}")


@app.callback()
def run(
    workspace: Annotated[
        Path,
        typer.Option(
            "--workspace",
            help="Agent 工作区路径，默认为 workspace/demo。",
        ),
    ] = tools.DEFAULT_WORKSPACE,
    output_format: Annotated[
        OutputFormat,
        typer.Option(
            "--output-format",
            help="任务结果报告格式。",
            case_sensitive=False,
        ),
    ] = OutputFormat.TEXT,
    max_steps: Annotated[
        int,
        typer.Option(
            "--max-steps",
            min=1,
            help="单个任务最大模型执行轮数。",
        ),
    ] = 20,
    max_tool_calls: Annotated[
        int,
        typer.Option(
            "--max-tool-calls",
            min=1,
            help="单个任务最大工具调用次数。",
        ),
    ] = 40,
    debug: Annotated[
        bool,
        typer.Option(
            "--debug",
            help="显示完整 Trace、模型轮次和工具调用日志。",
        ),
    ] = False,
) -> None:
    """解析参数并启动交互式 Agent 会话。"""

    _run_session(
        _resolve_workspace(workspace),
        output_format,
        max_steps,
        max_tool_calls,
        debug,
    )


def main() -> None:
    """启动 Typer 应用，供模块入口和项目脚本复用。"""

    app(prog_name="coding-agent")


if __name__ == "__main__":
    main()
