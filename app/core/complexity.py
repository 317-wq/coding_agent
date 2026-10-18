"""根据任务描述和项目上下文评估任务复杂度。"""

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.runtime.explorer import ProjectSnapshot


class TaskComplexity(StrEnum):
    """任务进入执行循环前的轻量复杂度分类。"""

    SIMPLE = "simple"
    COMPLEX = "complex"


@dataclass(frozen=True)
class ComplexityDecision:
    """复杂度判断结果及其可解释原因。"""

    complexity: TaskComplexity
    reasons: tuple[str, ...]

    @property
    def requires_planning(self) -> bool:
        return self.complexity is TaskComplexity.COMPLEX

    def to_dict(self) -> dict[str, Any]:
        return {
            "complexity": self.complexity.value,
            "requires_planning": self.requires_planning,
            "reasons": list(self.reasons),
        }


_COMPLEXITY_PATTERNS = (
    (re.compile(r"\b(and|then|after|before|while)\b", re.IGNORECASE), "包含多个依赖动作"),
    (re.compile(r"(?:同时|然后|并且|以及|之后|先.+再|多步|逐步)"), "包含多个依赖动作"),
    (re.compile(r"(?:重构|迁移|升级|架构|重写|拆分|整合|兼容|排查|调试|修复.+测试)"), "涉及跨文件或验证流程"),
    (re.compile(r"(?:所有|全部|整个项目|全局|批量|多个文件|多处)"), "影响范围较大"),
)

_VALIDATION_TASK_PATTERN = re.compile(
    r"安全边界|完成状态|验收|越界|只尝试一次|仅验证|仅检查|验证是否|检查是否",
    re.IGNORECASE,
)


class TaskComplexityJudge:
    """根据任务文本和项目快照做确定性的轻量判断。"""

    def __init__(self, complex_score: int = 2) -> None:
        if complex_score < 1:
            raise ValueError("complex_score 必须大于 0。")
        self.complex_score = complex_score

    def judge(
        self,
        task: str,
        snapshot: ProjectSnapshot | None = None,
    ) -> ComplexityDecision:
        """返回任务分类；空任务会被拒绝。"""

        if not isinstance(task, str) or not task.strip():
            raise ValueError("任务文本不能为空。")

        normalized = task.strip()
        if _VALIDATION_TASK_PATTERN.search(normalized):
            return ComplexityDecision(
                complexity=TaskComplexity.SIMPLE,
                reasons=("识别为一次性验收或安全边界检查，不生成通用开发计划。",),
            )
        score = 0
        reasons: list[str] = []

        for pattern, reason in _COMPLEXITY_PATTERNS:
            if pattern.search(normalized):
                score += 1
                reasons.append(reason)

        action_count = self._count_actions(normalized)
        if action_count >= 3:
            score += 1
            reasons.append(f"识别到约 {action_count} 个动作")

        if snapshot is not None:
            if len(snapshot.tree) > 80:
                score += 1
                reasons.append("项目结构较大")
            if len(snapshot.languages) > 1:
                score += 1
                reasons.append("项目包含多种主要语言")

        if score >= self.complex_score:
            return ComplexityDecision(
                complexity=TaskComplexity.COMPLEX,
                reasons=tuple(dict.fromkeys(reasons)),
            )

        return ComplexityDecision(
            complexity=TaskComplexity.SIMPLE,
            reasons=("任务可在一次直接工具循环中处理。",),
        )

    @staticmethod
    def _count_actions(task: str) -> int:
        action_words = (
            "创建", "新增", "修改", "编辑", "读取", "查看", "删除", "搜索",
            "测试", "运行", "修复", "重构", "迁移", "升级", "create", "add",
            "update", "edit", "read", "inspect", "delete", "search", "test",
            "run", "fix", "refactor", "migrate", "upgrade",
        )
        return sum(task.lower().count(word.lower()) for word in action_words)


def judge_task_complexity(
    task: str, snapshot: ProjectSnapshot | None = None
) -> ComplexityDecision:
    """使用默认策略判断任务复杂度。"""

    return TaskComplexityJudge().judge(task, snapshot)
