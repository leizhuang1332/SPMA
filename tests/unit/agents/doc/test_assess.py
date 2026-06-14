import pytest
from spma.agents.doc.completeness import assess_completeness, CompletenessResult


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

    async def is_available(self):
        return True


@pytest.mark.anyio
class TestCompletenessAssessment:
    async def test_l1_deterministic_convergence(self):
        results = [{"chunk_id": f"c{i}", "req_ids": ["REQ-001"]} for i in range(5)]
        llm = MockLLM()
        outcome = await assess_completeness(results=results, entities={"req_ids": ["REQ-001"]}, llm=llm)
        assert outcome.verdict == "converge"
        assert outcome.level == "L1"
        assert llm.call_count == 0

    async def test_l2_vector_threshold_convergence(self):
        results = [{"chunk_id": f"c{i}", "score": 0.95 - i * 0.02} for i in range(5)]
        llm = MockLLM()
        outcome = await assess_completeness(results=results, entities={"req_ids": []}, llm=llm)
        assert outcome.verdict == "converge"
        assert outcome.level == "L2"
        assert llm.call_count == 0

    async def test_l2_below_threshold_does_not_converge(self):
        results = [{"chunk_id": f"c{i}", "score": 0.70 - i * 0.05} for i in range(5)]
        llm = MockLLM()
        outcome = await assess_completeness(results=results, entities={"req_ids": []}, llm=llm)
        assert not (outcome.level == "L2" and outcome.verdict == "converge")

    async def test_l3_llm_fallback_sufficient(self):
        results = [{"chunk_id": f"c{i}", "score": 0.60 - i * 0.1} for i in range(3)]
        llm = MockLLM(responses={"信息是否充足": '{"assessment": "sufficient", "reason": "covers core"}'})
        outcome = await assess_completeness(results=results, entities={"req_ids": []}, llm=llm)
        assert outcome.verdict == "converge"
        assert outcome.level == "L3"
        assert llm.call_count == 1

    async def test_l3_llm_judges_insufficient(self):
        results = [{"chunk_id": "c1", "score": 0.50}]
        llm = MockLLM(responses={"信息是否充足": '{"assessment": "insufficient", "reason": "only 1 weak result"}'})
        outcome = await assess_completeness(results=results, entities={"req_ids": []}, llm=llm)
        assert outcome.verdict == "expand"
        assert outcome.reason == "llm_judged_insufficient"

    async def test_below_min_results_triggers_expand(self):
        results = [{"chunk_id": "c1", "score": 0.95}]
        llm = MockLLM(responses={"信息是否充足": '{"assessment": "insufficient", "reason": "only 1 result"}'})
        outcome = await assess_completeness(results=results, entities={"req_ids": []}, llm=llm, min_results=5)
        assert outcome.verdict == "expand"
