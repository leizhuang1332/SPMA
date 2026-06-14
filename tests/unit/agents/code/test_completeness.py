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
