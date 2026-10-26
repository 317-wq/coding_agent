"""计划可执行性校验：合并不可执行步骤，并保证验收项被阶段覆盖。

校验是确定性的，不调用模型。它同时约束规则生成的计划和未来模型生成
的计划：任何进入执行链路的步骤都必须有动作、能映射到工具能力。
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.core.intent import EvidenceKind, TaskIntent

MAX_STEPS = 5
"""计划允许的最大步骤数，超过时优先合并实现阶段。"""

FILE_KINDS = {
    EvidenceKind.FILE_EXISTS,
    EvidenceKind.FILE_CHANGED,
    EvidenceKind.CONTENT_CONTAINS,
}

_PHASE_PATTERNS = (
    ("test_author", re.compile(r"(?:编写|补充|新增|添加|创建|写)[^。；;\n]{0,24}(?:测试|用例|test_|conftest)")),
    (
        "test_run",
        re.compile(
            r"(?:运行|执行|跑|重跑)[^。；;\n]{0,10}测试"
            r"|测试[^。；;\n]{0,10}(?:通过|确认)"
            r"|回归测试"
        ),
    ),
    ("read", re.compile(r"阅读|读取|查看|搜索|检查|项目结构")),
    (
        "implement",
        re.compile(r"实现|修改|新增|添加|创建|删除|移除|修复|重构|优化|配置|迁移|升级|支持"),
    ),
)


@dataclass(frozen=True)
class LintIssue:
    """一条校验发现，规则编号对应文档中的 L1–L6。"""

    rule: str
    detail: str


@dataclass(frozen=True)
class LintResult:
    """校验后的步骤目标与问题清单。"""

    objectives: tuple[str, ...]
    issues: tuple[LintIssue, ...]


def phase_of(objective: str) -> str | None:
    """判断步骤目标属于哪个可执行阶段；多个动作并存时以最先出现者为准。"""
    matches = [
        (match.start(), phase)
        for phase, pattern in _PHASE_PATTERNS
        if (match := pattern.search(objective)) is not None
    ]
    if not matches:
        return None
    return min(matches)[1]


def _normalize(value: str) -> str:
    """归一化步骤目标，用于重复检测。"""
    return re.sub(r"\s+", "", value.strip().strip("。；;，,"))


def lint_objectives(
    objectives: Sequence[str], intent: TaskIntent
) -> LintResult:
    """校验步骤目标，返回修正后的顺序列表与问题清单。"""
    issues: list[LintIssue] = []
    kept: list[str] = []
    pending_prefix = ""

    for raw in objectives:
        objective = raw.strip().strip("。.")
        if not objective:
            continue
        if phase_of(objective) is None:
            issues.append(LintIssue("L1", f"合并不可执行步骤：{objective}"))
            if kept:
                kept[-1] = f"{kept[-1]}；{objective}"
            else:
                pending_prefix = (
                    f"{pending_prefix}；{objective}" if pending_prefix else objective
                )
            continue
        if pending_prefix:
            objective = f"{pending_prefix}；{objective}"
            pending_prefix = ""
        if any(_normalize(item) == _normalize(objective) for item in kept):
            issues.append(LintIssue("L3", f"合并重复步骤：{objective}"))
            continue
        kept.append(objective)

    if pending_prefix and kept:
        kept[-1] = f"{kept[-1]}；{pending_prefix}"
    if not kept:
        kept = ["实现或修改任务要求的功能"]
        issues.append(LintIssue("L1", "全部步骤不可执行，回退到实现阶段"))

    if intent.out_of_scope:
        remaining = [
            objective
            for objective in kept
            if not any(phrase in objective for phrase in intent.out_of_scope)
        ]
        if len(remaining) != len(kept):
            issues.append(LintIssue("L6", "移除了触碰范围外内容的步骤"))
            kept = remaining or kept

    phases = {phase_of(objective) for objective in kept}
    required = [item for item in intent.acceptance if item.required]
    if any(item.kind is EvidenceKind.TEST_PASSED for item in required) and "test_run" not in phases:
        kept.append("运行指定测试并记录结果")
        phases.add("test_run")
        issues.append(LintIssue("L4", "补充测试执行步骤"))
    if any(item.kind is EvidenceKind.WORKSPACE_READ for item in required) and "read" not in phases:
        kept.insert(0, "阅读现有代码和项目结构")
        phases.add("read")
        issues.append(LintIssue("L4", "补充阅读步骤"))
    if any(item.kind in FILE_KINDS for item in required) and not (
        {"implement", "test_author"} & phases
    ):
        kept.append("实现或修改任务要求的功能")
        phases.add("implement")
        issues.append(LintIssue("L4", "补充实现步骤"))

    if len(kept) > MAX_STEPS:
        merged: list[str] = []
        inserted = False
        for objective in kept:
            if phase_of(objective) == "implement":
                if not inserted:
                    merged.append("实现或修改任务要求的全部功能")
                    inserted = True
                continue
            merged.append(objective)
        if inserted:
            kept = merged
            issues.append(LintIssue("L5", "合并实现阶段以控制步骤数"))
        if len(kept) > MAX_STEPS:
            issues.append(LintIssue("L5", f"步骤数 {len(kept)} 超过上限 {MAX_STEPS}"))

    return LintResult(tuple(kept), tuple(issues))
