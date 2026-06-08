import pytest
from spma.agents.synthesis.auditor import audit_answer, AuditResult


class MockLLM:
    def __init__(self, json_response):
        self.json_response = json_response
    async def generate(self, prompt):
        return self.json_response


class TestAuditAnswer:
    async def test_pass_when_all_checks_ok(self):
        llm = MockLLM('{"citation_coverage": 0.90, "unverified_claims": [], "contradictions": [], "coverage_gaps": [], "verdict": "pass"}')
        result = await audit_answer("登录需要用户名密码。[PRD §2.1]", "登录流程", [{"chunk_id": "c1", "snippet": "登录流程描述"}], llm)
        assert result.verdict == "pass"

    async def test_fix_when_low_coverage(self):
        llm = MockLLM('{"citation_coverage": 0.55, "unverified_claims": ["缺少引用"], "contradictions": [], "coverage_gaps": [], "verdict": "fix"}')
        result = await audit_answer("支付流程。[PRD §3.1]", "支付流程", [{"chunk_id": "c1", "snippet": "概述"}], llm)
        assert result.verdict == "fix"

    async def test_contradiction_detected(self):
        llm = MockLLM('{"citation_coverage": 0.85, "unverified_claims": [], "contradictions": [{"claim_a": "3步", "claim_b": "2步", "source_a": "doc", "source_b": "sql"}], "coverage_gaps": [], "verdict": "contradiction"}')
        result = await audit_answer("支付流程有3步。[PRD] 2步。[SQL]", "支付流程", [{"chunk_id": "c1", "snippet": "doc"}, {"chunk_id": "c2", "snippet": "sql", "source_type": "sql"}], llm)
        assert result.verdict == "contradiction"
        assert len(result.contradictions) == 1

    async def test_gap_detected(self):
        llm = MockLLM('{"citation_coverage": 0.80, "unverified_claims": [], "contradictions": [], "coverage_gaps": ["退款流程"], "verdict": "gap"}')
        result = await audit_answer("支付流程包括下单。[PRD]", "支付流程包括下单和退款", [{"chunk_id": "c1", "snippet": "下单"}], llm)
        assert result.verdict == "gap"
        assert "退款" in str(result.coverage_gaps)
