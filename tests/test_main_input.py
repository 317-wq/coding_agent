from app import main


def test_read_task_supports_multiline_until_end(monkeypatch):
    values = iter(["实现计算器", "要求抛出 ZeroDivisionError", "END"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(values))

    assert main._read_task() == "实现计算器\n要求抛出 ZeroDivisionError"


def test_read_task_keeps_exit_and_quit_commands(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt="": " exit ")
    assert main._read_task() == "__EXIT__"


def test_read_task_ignores_empty_multiline_task(monkeypatch):
    values = iter(["", "END"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(values))
    assert main._read_task() == ""


def test_interactive_editor_keeps_pasted_multiline_task(monkeypatch):
    """验证交互编辑器将粘贴的多行内容作为一个任务返回。"""

    class FakeStdin:
        """模拟支持交互编辑器的 TTY。"""

        @staticmethod
        def isatty():
            return True

    class FakeSession:
        """模拟 Prompt Toolkit 已接收 bracketed paste 内容。"""

        def __init__(self, **kwargs):
            self.options = kwargs

        def prompt(self, *args, **kwargs):
            return "创建登录页\n并补充测试"

    monkeypatch.setattr(main.sys, "stdin", FakeStdin())
    monkeypatch.setattr(main, "PromptSession", FakeSession)

    assert main._read_task() == "创建登录页\n并补充测试"
