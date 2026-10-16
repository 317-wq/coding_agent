import argparse
from pathlib import Path

from app import tools
from app.agent import Agent
from app.workspace import WorkspaceContext


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
            task = input("\n请输入编程任务：").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n程序已退出。")
            break

        if not task:
            continue

        if task.lower() in {"exit", "quit"}:
            print("程序已退出。")
            break

        print("\n开始执行任务，请稍候……")

        try:
            result = agent.run(task)
            print("\n" + "=" * 50)
            print("任务执行结果")
            print("=" * 50)
            print(result)
        except KeyboardInterrupt:
            print("\n用户中断了当前任务。")
        except Exception as exc:
            print(f"\n程序发生异常：{exc}")


if __name__ == "__main__":
    main()