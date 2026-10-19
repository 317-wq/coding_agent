from pathlib import Path

from app.core.recovery import RecoveryAction
from app.core.task_safety import TaskSafetySession
from app.runtime.workspace import WorkspaceContext
from app.tools.change_set import ChangeSetError
from app.tools.result import ToolResult
from app.tools.rollback import RollbackStatus


def test_prepare_file_mutation_keeps_first_snapshot(tmp_path: Path):
    target = tmp_path / "module.py"
    target.write_text("before", encoding="utf-8")
    session = TaskSafetySession(WorkspaceContext.from_path(tmp_path))

    session.prepare_tool("edit_file", {"path": "module.py"})
    target.write_text("after", encoding="utf-8")
    session.prepare_tool("edit_file", {"path": "module.py"})

    snapshot = session.change_set.snapshot_store.get("module.py")
    assert snapshot.content == b"before"
    assert len(session.change_set.snapshot_store.snapshots) == 1


def test_prepare_directory_delete_captures_regular_files(tmp_path: Path):
    directory = tmp_path / "package"
    directory.mkdir()
    (directory / "one.py").write_text("one", encoding="utf-8")
    nested = directory / "nested"
    nested.mkdir()
    (nested / "two.py").write_text("two", encoding="utf-8")
    session = TaskSafetySession(WorkspaceContext.from_path(tmp_path))

    paths = session.prepare_tool("delete_directory", {"path": "package"})

    assert paths == ("package/nested/two.py", "package/one.py")


def test_completed_edit_is_reused_only_while_target_state_matches(tmp_path: Path):
    """验证写操作缓存会检查当前文件状态，避免复用过期结果。"""
    target = tmp_path / "module.py"
    target.write_text("return 43", encoding="utf-8")
    session = TaskSafetySession(WorkspaceContext.from_path(tmp_path))
    arguments = {
        "path": "module.py",
        "old_text": "return 42",
        "new_text": "return 43",
    }
    success = ToolResult.success("修改成功")
    session.record_completed_mutation("edit_file", arguments, success)

    assert session.completed_mutation("edit_file", arguments) == success
    target.write_text("return 42", encoding="utf-8")
    assert session.completed_mutation("edit_file", arguments) is None


def test_completed_mutation_reports_only_non_idempotent_success(tmp_path: Path):
    """验证写操作记录只把非幂等成功标记为实际应用修改。"""
    session = TaskSafetySession(WorkspaceContext.from_path(tmp_path))
    arguments = {"path": "module.py", "content": "value"}

    assert (
        session.record_completed_mutation(
            "create_file", arguments, ToolResult.failure("失败")
        )
        is False
    )
    assert (
        session.record_completed_mutation(
            "create_file", arguments, ToolResult.success("无需修改，目标内容已存在")
        )
        is False
    )
    assert (
        session.record_completed_mutation(
            "create_file", arguments, ToolResult.success("创建成功")
        )
        is True
    )


def test_observe_failure_uses_current_recoverable_paths(tmp_path: Path):
    target = tmp_path / "module.py"
    target.write_text("before", encoding="utf-8")
    session = TaskSafetySession(WorkspaceContext.from_path(tmp_path))
    session.prepare_tool("edit_file", {"path": "module.py"})
    target.write_text("after", encoding="utf-8")

    decision = session.observe(
        "edit_file", ToolResult.failure("修改失败"), plan_failed=True
    )

    assert decision.action is RecoveryAction.SUGGEST_ROLLBACK
    assert decision.recoverable_paths == ("module.py",)


def test_first_test_failure_starts_repair_without_rollback(tmp_path: Path):
    target = tmp_path / "module.py"
    target.write_text("before", encoding="utf-8")
    session = TaskSafetySession(WorkspaceContext.from_path(tmp_path))
    session.prepare_tool("edit_file", {"path": "module.py"})
    target.write_text("after", encoding="utf-8")

    decision = session.observe(
        "run_tests",
        ToolResult.failure("测试错误类型：断言失败\n测试结果：失败"),
        plan_failed=True,
    )

    assert decision.action is RecoveryAction.REPLAN
    assert session.test_repair_attempts == 0


def test_successful_rerun_after_mutation_counts_as_repair_attempt(tmp_path: Path):
    """验证失败基线后的代码修改和成功重跑计为一轮自动修复。"""
    target = tmp_path / "module.py"
    target.write_text("before", encoding="utf-8")
    session = TaskSafetySession(WorkspaceContext.from_path(tmp_path))
    session.observe(
        "run_tests",
        ToolResult.failure("测试错误类型：代码导入错误\n测试结果：失败"),
        plan_failed=True,
    )
    session.prepare_tool("edit_file", {"path": "module.py"})
    target.write_text("after", encoding="utf-8")
    session.record_mutation_applied()

    decision = session.observe(
        "run_tests",
        ToolResult.success("测试目标：.\n测试退出码：0\n测试结果：通过"),
    )

    assert decision.action is RecoveryAction.CONTINUE
    assert session.test_repair_attempts == 1


def test_test_file_fixes_do_not_consume_implementation_repair_budget(
    tmp_path: Path,
):
    """验证修正测试脚手架不会挤占两次业务代码自动修复额度。"""
    session = TaskSafetySession(WorkspaceContext.from_path(tmp_path))
    failure = ToolResult.failure(
        "测试错误类型：测试代码\n测试结果：失败\n"
        "FAILED tests/test_sample.py::test_value"
    )
    arguments = {"test_path": "tests/test_sample.py"}

    session.observe("run_tests", failure, arguments=arguments)
    session.record_mutation_applied("tests/test_sample.py")
    first_retry = session.observe("run_tests", failure, arguments=arguments)

    assert first_retry.action is RecoveryAction.REPLAN
    assert session.test_repair_attempts == 0

    session.record_mutation_applied("src/sample.py")
    final_retry = session.observe(
        "run_tests",
        ToolResult.success("测试目标：tests/test_sample.py\n测试结果：通过"),
        arguments=arguments,
    )

    assert final_retry.action is RecoveryAction.CONTINUE
    assert session.test_repair_attempts == 1


def test_same_failed_test_requires_real_mutation_before_rerun(tmp_path: Path):
    """验证失败编辑不会放行同一测试，真实落盘修改才会放行。"""
    session = TaskSafetySession(WorkspaceContext.from_path(tmp_path))
    arguments = {"test_path": "tests/"}
    session.observe(
        "run_tests",
        ToolResult.failure(
            "测试错误类型：断言失败\n测试结果：失败\n"
            "FAILED tests/test_sample.py::test_value"
        ),
        arguments=arguments,
    )

    assert session.test_rerun_block_reason("run_tests", arguments) is not None

    # prepare_tool 只建立快照；失败或未落盘的编辑不能算作修复。
    session.prepare_tool("edit_file", {"path": "module.py"})
    assert session.test_rerun_block_reason("run_tests", arguments) is not None

    session.record_mutation_applied()
    assert session.test_rerun_block_reason("run_tests", arguments) is None
    assert "tests/test_sample.py::test_value" in session.unresolved_test_reason()


def test_reset_after_recovery_starts_new_change_session(tmp_path: Path):
    session = TaskSafetySession(WorkspaceContext.from_path(tmp_path))
    session.prepare_tool("create_file", {"path": "new.py"})

    session.reset_after_recovery(RollbackStatus.RESTORED)

    assert session.recovery_attempts == 1
    assert session.change_set.summary().touched_count == 0


def test_reset_requires_successful_rollback(tmp_path: Path):
    session = TaskSafetySession(WorkspaceContext.from_path(tmp_path))

    try:
        session.reset_after_recovery(RollbackStatus.CANCELLED)
    except ValueError as exc:
        assert "回滚成功" in str(exc)
    else:
        raise AssertionError("未成功回滚时不应重置变更会话")


def test_observe_change_set_error_stops_safely(tmp_path: Path, monkeypatch):
    session = TaskSafetySession(WorkspaceContext.from_path(tmp_path))
    monkeypatch.setattr(
        session.change_set,
        "summary",
        lambda: (_ for _ in ()).throw(ChangeSetError("状态冲突")),
    )

    decision = session.observe("edit_file", ToolResult.failure("失败"))

    assert decision.action is RecoveryAction.STOP
    assert "状态冲突" in decision.reason
