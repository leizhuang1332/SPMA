import pytest
from spma.agents.code.completeness import assess_code_completeness


from unittest.mock import MagicMock


class MockLLM:
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.call_count = 0
    async def ainvoke(self, prompt):
        self.call_count += 1
        for key, resp in self.responses.items():
            if key in prompt:
                return MagicMock(content=resp)
        return MagicMock(content='{"assessment": "sufficient", "reason": "ok"}')


@pytest.mark.anyio
class TestCodeCompleteness:
    async def test_l1_deterministic_code_refs_converge(self):
        results = [{"file_path": "oauth.py", "match_text": "def token_refresh"} for _ in range(3)]
        llm = MockLLM()
        outcome = await assess_code_completeness(ripgrep_results=results, expanded_context=[],
            entities={"code_refs": ["oauth.py"]}, call_depth=0, new_files_this_round=1, fallback_layer=0, llm=llm)
        assert outcome.verdict == "converge"
        assert outcome.level == "L1"
        assert llm.call_count == 0

    async def test_l2_max_call_depth_converge(self):
        results = [{"file_path": f"file{i}.py", "match_text": "code"} for i in range(4)]
        llm = MockLLM()
        outcome = await assess_code_completeness(ripgrep_results=results, expanded_context=[],
            entities={"code_refs": []}, call_depth=2, new_files_this_round=1, fallback_layer=1, llm=llm)
        assert outcome.verdict == "converge"
        assert outcome.level == "L2"

    async def test_l2_no_new_files_converge(self):
        results = [{"file_path": f"file{i}.py", "match_text": "code"} for i in range(3)]
        llm = MockLLM()
        outcome = await assess_code_completeness(ripgrep_results=results, expanded_context=[],
            entities={"code_refs": []}, call_depth=1, new_files_this_round=0, fallback_layer=1, llm=llm)
        assert outcome.verdict == "converge"

    async def test_l3_llm_fallback_sufficient(self):
        results = [{"file_path": "x.py", "match_text": "def foo"}]
        llm = MockLLM(responses={"是否足以": '{"assessment": "sufficient", "reason": "found"}'})
        outcome = await assess_code_completeness(ripgrep_results=results, expanded_context=[],
            entities={"code_refs": []}, call_depth=1, new_files_this_round=1, fallback_layer=2, llm=llm)
        assert outcome.verdict == "converge"
        assert outcome.level == "L3"

    async def test_default_expand_without_llm(self):
        results = [{"file_path": "x.py", "match_text": "foo"}]
        outcome = await assess_code_completeness(ripgrep_results=results, expanded_context=[],
            entities={"code_refs": []}, call_depth=0, new_files_this_round=1, fallback_layer=2, llm=None)
        assert outcome.verdict == "expand"


def test_should_reflect_default_false():
    """CodeCompletenessResult 默认 should_reflect 必须为 False（Task 1 数据契约）。"""
    from spma.agents.code.completeness import CodeCompletenessResult
    result = CodeCompletenessResult(
        verdict="progress",
        reason="test",
        level="L1_deterministic",
    )
    assert result.should_reflect is False


async def test_assess_sets_should_reflect_on_diminishing_returns():
    """diminishing_returns 模式触发时，assess_code_completeness 应设置 should_reflect=True。

    触发条件：new_files_rate < 0.10 且 new_files_this_round < 3。
    构造：total_files=20, new_files_this_round=1 → rate=0.05 < 0.10, < 3 → diminishing_returns。
    """
    from spma.agents.code.completeness import assess_code_completeness
    # 构造 diminishing_returns 场景：rate=1/20=0.05 < 0.10 且 new_files_this_round=1 < 3
    result = await assess_code_completeness(
        ripgrep_results=[{"repo": "r", "file_path": f"f{i}"} for i in range(1)],
        expanded_context=[],
        entities={"module": ["auth"]},  # 无 code_refs 避免 goal_verified
        call_depth=2,  # < max_rounds=6
        new_files_this_round=1,
        fallback_layer=1,  # != 0
        previous_new_files=2,  # > 0 避免 stuck; ratio=1/2=0.5 不触发 regression (< 0.5)
        round=2,
        total_files=20,
        legacy_levels=False,
    )
    assert result.level == "diminishing_returns", f"expected diminishing_returns, got {result.level}"
    assert result.should_reflect is True
