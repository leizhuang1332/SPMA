import pytest
from spma.agents.doc.clue_expander import rule_based_expand, llm_based_expand


class TestRuleBasedExpand:
    def test_extracts_new_req_ids(self):
        results = [
            {"req_ids": ["REQ-001", "REQ-003"]},
            {"req_ids": ["REQ-002", "REQ-003"]},
        ]
        known_req_ids = {"REQ-001"}
        new_query = rule_based_expand("支付流程", results, known_req_ids)
        assert "REQ-002" in new_query or "REQ-003" in new_query

    def test_extracts_frequent_terms(self):
        results = [
            {"content": "支付回调接口 需要处理超时"},
            {"content": "支付回调接口 需要实现幂等"},
            {"content": "订单状态流转"},
        ]
        new_query = rule_based_expand("支付流程", results, {"REQ-001"})
        assert "支付" in new_query

    def test_no_new_info_returns_original(self):
        results = [{"content": "简短的描述"}]
        new_query = rule_based_expand("支付流程", results, set())
        assert new_query == "支付流程"


class TestLLMBasedExpand:
    @pytest.mark.anyio
    async def test_generates_expansion_queries(self):
        results = [{"content": "支付流程包括下单和回调两个阶段"}]

        class MockLLM:
            async def generate(self, prompt):
                return "订单状态管理\n支付异常处理\n退款流程设计"

        new_query = await llm_based_expand("支付流程", results, MockLLM())
        assert "订单" in new_query or "退款" in new_query
