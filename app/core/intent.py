"""从任务文本抽取意图与可核验的验收条件。

全部基于确定性规则，不调用模型。只把用户原文里**明确出现**的要求转成验收项：
推断出来的内容不设 `required`，避免凭空提高完成门槛。
"""

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class EvidenceKind(StrEnum):
    """验收条件对应的可核验证据类型。"""

    FILE_EXISTS = "file_exists"
    FILE_CHANGED = "file_changed"
    TEST_PASSED = "test_passed"
    CONTENT_CONTAINS = "content_contains"
    WORKSPACE_READ = "workspace_read"


@dataclass(frozen=True)
class AcceptanceItem:
    """一条可核验的验收条件。"""

    item_id: int
    text: str
    kind: EvidenceKind
    target: str | None = None
    required: bool = True

    def to_dict(self) -> dict[str, Any]:
        """返回可序列化的验收项。"""
        return {
            "item_id": self.item_id,
            "text": self.text,
            "kind": self.kind.value,
            "target": self.target,
            "required": self.required,
        }


@dataclass(frozen=True)
class TaskIntent:
    """用户任务的结构化意图：目标、范围、范围外、验收与待确认。"""

    objective: str
    scope: tuple[str, ...] = ()
    out_of_scope: tuple[str, ...] = ()
    acceptance: tuple[AcceptanceItem, ...] = ()
    open_questions: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """返回可序列化的意图，供计划 JSON 与 Trace 使用。"""
        return {
            "objective": self.objective,
            "scope": list(self.scope),
            "out_of_scope": list(self.out_of_scope),
            "acceptance": [item.to_dict() for item in self.acceptance],
            "open_questions": list(self.open_questions),
        }


_PATH_PATTERN = re.compile(r"(?<![\w./-])((?:[\w.-]+/)*[\w.-]+\.py)\b")
_EXCEPTION_PATTERN = re.compile(r"\b[A-Z][A-Za-z0-9]*(?:Error|Exception)\b")
_CALL_PATTERN = re.compile(r"\b([a-z_][A-Za-z0-9_]*)\((?=\s*[A-Za-z_)])")
_TEST_RUN_PATTERN = re.compile(
    r"(?:运行|跑|执行|重跑)[^。；;\n]{0,10}测试"
    r"|测试[^。；;\n]{0,10}(?:通过|确认)"
    r"|回归测试"
)
_READ_PATTERN = re.compile(r"阅读|读取|查看|搜索|检查|项目结构")
_OUT_OF_SCOPE_PATTERN = re.compile(r"(?:不要|不得|禁止|不可)\s*([^，。；;\n]+)")
_QUESTION_SPLIT = re.compile(r"[。\n]")
_CHOICE_PATTERN = re.compile(r"是否|还是|要么|或者")

_IGNORED_CALLS = {
    "if",
    "for",
    "while",
    "print",
    "open",
    "len",
    "str",
    "int",
    "list",
    "dict",
    "set",
    "tuple",
    "range",
    "main",
}

MAX_ACCEPTANCE_ITEMS = 8

_READ_VERBS = re.compile(r"阅读|读取|查看|检查|分析|搜索")
_WRITE_VERBS = re.compile(r"创建|新增|编写|实现|生成|添加|修改|修复|重构|删除")
_CLAUSE_SPLIT = re.compile(r"[。；;\n]")


def _first_sentence(task: str) -> str:
    """取任务的第一句作为目标描述。"""
    first = re.split(r"[。\n]", task, maxsplit=1)[0].strip()
    return first or task.strip()


def _is_read_only_reference(task: str, path: str) -> bool:
    """判断路径是否只出现在只读上下文里（该路径不要求必须存在）。"""
    for clause in _CLAUSE_SPLIT.split(task):
        if path in clause:
            return bool(_READ_VERBS.search(clause)) and not (
                _WRITE_VERBS.search(clause)
            )
    return False


def extract_intent(task: str) -> TaskIntent:
    """按确定性规则从任务文本抽取意图。"""
    if not isinstance(task, str) or not task.strip():
        raise ValueError("任务文本不能为空。")
    normalized = task.strip()

    paths = list(dict.fromkeys(match.group(1) for match in _PATH_PATTERN.finditer(normalized)))
    out_of_scope = list(
        dict.fromkeys(
            match.group(1).strip()
            for match in _OUT_OF_SCOPE_PATTERN.finditer(normalized)
            if match.group(1).strip()
        )
    )
    questions = [
        clause.strip()
        for clause in _QUESTION_SPLIT.split(normalized)
        if _CHOICE_PATTERN.search(clause) and ("？" in clause or "?" in clause)
    ]

    acceptance: list[AcceptanceItem] = []

    def add(text: str, kind: EvidenceKind, target: str | None = None) -> None:
        """追加一条验收项，超过上限后忽略。"""
        if len(acceptance) >= MAX_ACCEPTANCE_ITEMS:
            return
        acceptance.append(
            AcceptanceItem(
                item_id=len(acceptance) + 1,
                text=text,
                kind=kind,
                target=target,
            )
        )

    for path in paths[:4]:
        if not _is_read_only_reference(normalized, path):
            add(f"{path} 存在", EvidenceKind.FILE_EXISTS, path)

    for name in list(dict.fromkeys(_EXCEPTION_PATTERN.findall(normalized)))[:3]:
        add(f"源码包含 {name}", EvidenceKind.CONTENT_CONTAINS, name)

    call_names = [
        name
        for name in dict.fromkeys(_CALL_PATTERN.findall(normalized))
        if name not in _IGNORED_CALLS
    ][:3]
    for name in call_names:
        add(f"源码包含 {name}", EvidenceKind.CONTENT_CONTAINS, name)

    if _TEST_RUN_PATTERN.search(normalized):
        test_path = next(
            (
                path
                for path in paths
                if "test" in path.lower() or path.startswith("tests/")
            ),
            None,
        )
        add("指定测试运行通过", EvidenceKind.TEST_PASSED, test_path)

    if _READ_PATTERN.search(normalized):
        add("读取或检查了相关代码", EvidenceKind.WORKSPACE_READ)

    return TaskIntent(
        objective=_first_sentence(normalized),
        scope=tuple(paths[:5]),
        out_of_scope=tuple(out_of_scope[:5]),
        acceptance=tuple(acceptance),
        open_questions=tuple(questions[:3]),
    )
