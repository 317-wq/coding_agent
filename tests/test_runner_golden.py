"""R5 行为基线：重构前后 ``TaskResult`` 必须逐字段一致。

快照由 ``python /tmp/gen_golden.py`` 基于重构前实现生成，之后任何改动都
必须让本测试保持通过；搬迁期间不允许通过改快照来迁就实现。
"""

import json
from pathlib import Path

import pytest

from tests.golden_scenarios import SCENARIOS, normalize_result

GOLDEN_DIR = Path(__file__).parent / "golden"


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_behavior_matches_golden_snapshot(name, monkeypatch, tmp_path):
    """验证当前实现与行为基线一致。"""
    agent = SCENARIOS[name](monkeypatch, tmp_path)

    assert agent.last_result is not None
    expected = json.loads(
        (GOLDEN_DIR / f"{name}.json").read_text(encoding="utf-8")
    )
    assert normalize_result(agent.last_result) == expected
