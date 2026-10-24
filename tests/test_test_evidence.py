"""测试证据摘要模块的行为验证。"""

from app.core.task_result import TestEvidenceStatus as EvidenceStatus
from app.core.test_evidence import summarize_test_evidence


def test_summarizes_passed_and_failed_test_output():
    """验证摘要保留路径、退出码和错误分类。"""
    evidence = summarize_test_evidence(
        (
            "测试目标：tests/test_ok.py\n测试退出码：0\n测试结果：通过",
            "测试目标：tests/test_bad.py\n测试退出码：1\n测试结果：失败\n测试错误类型：断言失败",
        )
    )
    assert evidence[0].status is EvidenceStatus.PASSED
    assert evidence[0].target == "tests/test_ok.py"
    assert evidence[1].status is EvidenceStatus.FAILED
    assert evidence[1].exit_code == 1
    assert evidence[1].error_kind == "断言失败"


def test_adds_not_run_evidence_for_requested_target():
    """验证用户指定但未执行的测试路径不会被伪装成通过。"""
    evidence = summarize_test_evidence(requested_targets=("tests/test_app.py",))
    assert len(evidence) == 1
    assert evidence[0].status is EvidenceStatus.NOT_RUN
    assert evidence[0].exit_code is None


def test_unstructured_test_error_is_never_reported_as_passed():
    """验证缺少退出码的测试工具错误不会被摘要器伪装成成功。"""
    evidence = summarize_test_evidence(("测试路径不存在：tests/missing.py",))

    assert evidence[0].status is EvidenceStatus.FAILED
