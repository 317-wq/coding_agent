"""命令行交互入口，负责读取任务并启动 Agent。"""

import argparse
from pathlib import Path

from app import tools
from app.core.agent import Agent
from app.runtime.workspace import WorkspaceContext


def _read_task() -> str | None:
    """读取一条支持多行的任务；单独输入 exit/quit 仍退出程序。"""

    try:
        first = input("\n请输入编程任务（输入 END 结束多行任务）：")
    except (EOFError, KeyboardInterrupt):
        return None

    if first.strip().lower() in {"exit", "quit"}:
        return "__EXIT__"
    if first.strip() == "END":
        return ""

    lines = [first]
    while True:
        try:
            line = input("...")
        except (EOFError, KeyboardInterrupt):
            return "\n".join(lines).strip()
        if line.strip() == "END":
            return "\n".join(lines).strip()
        lines.append(line)


def _parse_args() -> WorkspaceContext:
    parser = argparse.ArgumentParser(description="Mini Coding Agent")
    parser.add_argument(
        "--workspace",
        type=Path,
        default=tools.WORKSPACE,
        help="Agent 工作区路径，默认为 workspace/demo",
    )
    args = parser.parse_args()
    try:
        return WorkspaceContext.from_path(args.workspace)
    except ValueError as exc:
        parser.error(str(exc))


def main() -> None:
    """程序入口。"""

    workspace = _parse_args()

    print("=" * 50)
    print("Mini Coding Agent")
    print(f"工作目录：{workspace.root}")
    print("输入 exit 或 quit 退出程序")
    print("=" * 50)

    agent = Agent(workspace=workspace)

    while True:
        try:
            task = _read_task()
        except (EOFError, KeyboardInterrupt):
            print("\n程序已退出。")
            break

        if task is None:
            print("\n程序已退出。")
            break
        if task == "__EXIT__":
            print("程序已退出。")
            break
        if not task.strip():
            continue

        print("\n开始执行任务，请稍候……")

        try:
            result = agent.run(task)
            print("\n" + "=" * 50)
            print("任务执行结果")
            print("=" * 50)
            print(result)
        except KeyboardInterrupt:
            print("\n用户中断了当前任务。")
        except Exception as exc:  # noqa: BLE001 - CLI task boundary
            print(f"\n程序发生异常：{exc}")


if __name__ == "__main__":
    main()
