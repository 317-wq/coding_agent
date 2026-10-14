from app.agent import Agent


def main() -> None:
    """程序入口。"""

    print("=" * 50)
    print("Mini Coding Agent")
    print("工作目录：workspace/demo")
    print("输入 exit 或 quit 退出程序")
    print("=" * 50)

    agent = Agent()

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