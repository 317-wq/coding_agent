"""把测试工具输出转换为可审查的结构化测试证据。"""

from __future__ import annotations

import re
from collections.abc import Sequence

from app.core.task_result import TestEvidence, TestEvidenceStatus

_TARGET = re.compile(r"测试目标：([^\n]+)")
_EXIT_CODE = re.compile(r"测试退出码：(-?\d+)")
_ERROR_KIND = re.compile(r"测试错误类型：([^\n]+)")


class TestEvidenceSummarizer:
    """从 run_tests 的结构化文本中提取稳定的测试证据。"""

    def summarize(
        self,
        results: Sequence[str] = (),
        requested_targets: Sequence[str] = (),
    ) -> tuple[TestEvidence, ...]:
        """汇总测试结果，并为未执行的用户指定目标补充 not_run 证据。"""
        evidence = [self._parse(result) for result in results if result.strip()]
        known_targets = {item.target for item in evidence}
        for target in requested_targets:
            normalized = target.strip()
            if normalized and normalized not in known_targets:
                evidence.append(TestEvidence(normalized, TestEvidenceStatus.NOT_RUN))
        return tuple(evidence)

    @staticmethod
    def _parse(raw: str) -> TestEvidence:
        """解析单次测试工具输出，保留退出码、状态和错误分类。"""
        target_match = _TARGET.search(raw)
        exit_match = _EXIT_CODE.search(raw)
        error_match = _ERROR_KIND.search(raw)
        target = target_match.group(1).strip() if target_match else "未标识测试"
        exit_code = int(exit_match.group(1)) if exit_match else None
        failed = (
            exit_code not in (None, 0)
            or "测试结果：失败" in raw
            or raw.startswith("错误：")
        )
        passed = exit_code == 0 or "测试结果：通过" in raw
        return TestEvidence(
            target=target,
            status=(
                TestEvidenceStatus.PASSED
                if passed and not failed
                else TestEvidenceStatus.FAILED
            ),
            exit_code=exit_code,
            error_kind=error_match.group(1).strip() if error_match else None,
            output=raw,
        )


def summarize_test_evidence(
    results: Sequence[str] = (),
    requested_targets: Sequence[str] = (),
) -> tuple[TestEvidence, ...]:
    """使用默认摘要器生成测试证据。"""
    return TestEvidenceSummarizer().summarize(results, requested_targets)


def test_target_covers(parent: str, child: str) -> bool:
    """判断父级 pytest 目标的成功结果是否覆盖某个子目标。"""
    parent = parent.strip().replace("\\", "/").rstrip("/") or "."
    child = child.strip().replace("\\", "/").rstrip("/") or "."
    if parent == ".":
        return True
    parent_path, separator, _ = parent.partition("::")
    child_path, _, _ = child.partition("::")
    if separator:
        return child == parent or child.startswith(f"{parent}::")
    if parent_path.endswith(".py"):
        return child_path == parent_path
    return child_path == parent_path or child_path.startswith(f"{parent_path}/")


def latest_test_evidence(
    evidence: Sequence[TestEvidence],
) -> tuple[TestEvidence, ...]:
    """折叠测试历史，使成功父目标覆盖此前的子目标失败。"""
    latest: dict[str, TestEvidence] = {}
    for item in evidence:
        if item.status is TestEvidenceStatus.PASSED:
            latest = {
                target: previous
                for target, previous in latest.items()
                if not test_target_covers(item.target, target)
            }
        latest[item.target] = item
    return tuple(latest.values())
