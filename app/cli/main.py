"""基于 Typer 和 Rich 的命令行交互入口。"""

from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel

from app import tools
from app.cli.report import render_task_report
from app.core.agent import Agent
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


def _read_task() -> str | None:
    """读取一条支持多行的任务；单独输入 exit/quit 退出程序。"""

    try:
        first = console.input(
            "\n[bold cyan]请输入编程任务[/]（输入 [bold]END[/] 结束多行任务）："
        )
    except (EOFError, KeyboardInterrupt):
        return None

    if first.strip().lower() in {"exit", "quit"}:
        return "__EXIT__"
    if first.strip() == "END":
        return ""

    lines = [first]
    while True:
        try:
            line = console.input("[dim]...[/]")
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


def _run_session(
    workspace: WorkspaceContext,
    output_format: OutputFormat,
    max_steps: int,
    max_tool_calls: int,
) -> None:
    """运行可连续接收任务的交互式 Agent 会话。"""

    console.print(
        Panel.fit(
            f"[bold]Mini Coding Agent[/]\n"
            f"工作目录：[cyan]{workspace.root}[/]\n"
            "输入 [bold]exit[/] 或 [bold]quit[/] 退出程序",
            border_style="cyan",
        )
    )
    agent = Agent(
        workspace=workspace,
        max_steps=max_steps,
        max_tool_calls=max_tool_calls,
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

        console.print("\n[cyan]开始执行任务，请稍候……[/]")
        try:
            result = agent.run(task)
            report = (
                render_task_report(agent.last_result, output_format.value)
                if agent.last_result is not None
                else result
            )
            if output_format is OutputFormat.JSON:
                console.print(report, markup=False, highlight=False, soft_wrap=True)
            else:
                console.print(
                    Panel(
                        report,
                        title="[bold]任务执行结果[/]",
                        border_style="green",
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
    ] = tools.WORKSPACE,
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
) -> None:
    """解析参数并启动交互式 Agent 会话。"""

    _run_session(
        _resolve_workspace(workspace),
        output_format,
        max_steps,
        max_tool_calls,
    )


def main() -> None:
    """启动 Typer 应用，供模块入口和项目脚本复用。"""

    app(prog_name="coding-agent")


if __name__ == "__main__":
    main()
