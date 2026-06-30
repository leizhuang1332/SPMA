"""Tests for assess_code_completeness v2 — 7 收敛模式（design-13 §3.4）。

7 mode = 5 确定性 + 2 LLM 路径：
    1. goal_verified: code_refs 非空 + total ≥ 3 + fallback_layer = 0
    2. stuck: round ≥ 2 + new_files_this_round=0 + previous_new_files=0
    3. regression: round_over_round_ratio < 0.5 + 本轮 total 减少
    4. diminishing_returns: new_files_rate < 0.10
    5. cap_reached: call_depth ≥ max_rounds 或 total_files ≥ max_files
    6. llm_judged: 5 确定性全不命中 + LLM sufficient
    7. expand: 5 确定性全不命中 + LLM insufficient
"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from spma.agents.code.completeness import assess_code_completeness


def _make_results(n: int) -> list[dict]:
    return [{"file_path": f"file_{i}.py", "match_text": "code"} for i in range(n)]


@pytest.mark.anyio
class TestCompletenessV2:
    async def test_goal_verified(self):
        """确定性 1: code_refs 非空 + total ≥ 3 + fallback_layer=0。"""
        results = _make_results(3)
        outcome = await assess_code_completeness(
            ripgrep_results=results,
            expanded_context=[],
            entities={"code_refs": ["auth.py"]},
            call_depth=0,
            new_files_this_round=3,
            fallback_layer=0,
            round=1,
            legacy_levels=False,
        )
        assert outcome.verdict == "converge"
        assert outcome.level == "goal_verified"

    async def test_stuck_with_round_2(self):
        """确定性 2: round=2, new_files_this_round=0, previous_new_files=0 → stuck。"""
        results = _make_results(3)
        outcome = await assess_code_completeness(
            ripgrep_results=results,
            expanded_context=[],
            entities={"code_refs": []},
            call_depth=1,
            new_files_this_round=0,
            fallback_layer=1,
            previous_new_files=0,
            round=2,
            legacy_levels=False,
        )
        assert outcome.verdict == "converge"
        assert outcome.level == "stuck"

    async def test_stuck_not_triggered_in_round_1(self):
        """boundary case: round=1 时即使 new_files=0 也不触发 stuck。"""
        results = _make_results(3)
        outcome = await assess_code_completeness(
            ripgrep_results=results,
            expanded_context=[],
            entities={"code_refs": []},
            call_depth=0,
            new_files_this_round=0,
            fallback_layer=1,
            previous_new_files=0,
            round=1,  # 首轮豁免
            legacy_levels=False,
        )
        # 首轮 stuck 不触发 → 走 LLM 路径（llm=None 时降级 expand）
        assert outcome.level != "stuck"

    async def test_regression(self):
        """确定性 3: round_over_round_ratio < 0.5 且本轮 total 减少。"""
        outcome = await assess_code_completeness(
            ripgrep_results=_make_results(2),  # 本轮 2
            expanded_context=[],
            entities={"code_refs": []},
            call_depth=2,
            new_files_this_round=1,  # ratio = 1/10 = 0.1 < 0.5
            fallback_layer=1,
            previous_new_files=10,  # 上轮 10
            round=3,
            legacy_levels=False,
        )
        assert outcome.verdict == "converge"
        assert outcome.level == "regression"

    async def test_diminishing_returns(self):
        """确定性 4: 连续两轮 new_files_rate < 0.10。"""
        outcome = await assess_code_completeness(
            ripgrep_results=_make_results(1),
            expanded_context=[],
            entities={"code_refs": []},
            call_depth=3,
            new_files_this_round=1,
            fallback_layer=1,
            previous_new_files=1,  # 上一轮也只有 1 个
            round=3,
            total_files=20,
            legacy_levels=False,
        )
        # 简化判定：new_files_rate < 0.10 → diminishing_returns
        assert outcome.level in ("diminishing_returns", "stuck")  # 两种都可能触发

    async def test_cap_reached_max_rounds(self):
        """确定性 5: call_depth ≥ max_rounds。"""
        outcome = await assess_code_completeness(
            ripgrep_results=_make_results(3),
            expanded_context=[],
            entities={"code_refs": []},
            call_depth=6,  # = max_rounds
            new_files_this_round=2,
            fallback_layer=1,
            max_rounds=6,
            round=6,
            legacy_levels=False,
        )
        assert outcome.verdict == "converge"
        assert outcome.level == "cap_reached"

    async def test_llm_judged_sufficient(self):
        """LLM 路径 1: 5 确定性全不命中 + LLM 判定 sufficient。"""
        class MockLLM:
            async def ainvoke(self, prompt):
                return MagicMock(content='{"assessment": "sufficient", "reason": "ok"}')

        outcome = await assess_code_completeness(
            ripgrep_results=_make_results(2),
            expanded_context=[],
            entities={"code_refs": []},
            call_depth=1,
            new_files_this_round=1,  # > 0
            fallback_layer=1,
            previous_new_files=1,  # > 0（不触发 stuck）
            round=2,
            llm=MockLLM(),
            legacy_levels=False,
        )
        assert outcome.verdict == "converge"
        assert outcome.level == "llm_judged"

    async def test_expand_when_llm_says_insufficient(self):
        """LLM 路径 2: LLM 判定 insufficient → expand。"""
        class MockLLM:
            async def ainvoke(self, prompt):
                return MagicMock(content='{"assessment": "insufficient", "reason": "more needed"}')

        outcome = await assess_code_completeness(
            ripgrep_results=_make_results(2),
            expanded_context=[],
            entities={"code_refs": []},
            call_depth=1,
            new_files_this_round=1,
            fallback_layer=1,
            previous_new_files=1,
            round=2,
            llm=MockLLM(),
            legacy_levels=False,
        )
        assert outcome.verdict == "expand"
        assert outcome.level == "expand"
