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
