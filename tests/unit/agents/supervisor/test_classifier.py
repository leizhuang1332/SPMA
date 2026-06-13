import pytest
from spma.agents.supervisor.classifier_rules import apply_rules
from spma.models.classification import ClassificationResult


class TestRuleBasedClassification:
    def test_stats_keyword_adds_sql(self):
        result = apply_rules("订单数量有多少", ClassificationResult(
            sources=["doc"], is_cross_source=False, query_type="search", entities={}))
        assert "sql" in result["sources"]

    def test_req_id_adds_doc(self):
        result = apply_rules("REQ-187 的代码", ClassificationResult(
            sources=["code"], is_cross_source=False, query_type="trace", entities={}))
        assert "doc" in result["sources"]

    def test_code_pattern_adds_code(self):
        result = apply_rules("oauth.py 的 token_refresh 实现", ClassificationResult(
            sources=["doc"], is_cross_source=False, query_type="search", entities={}))
        assert "code" in result["sources"]

    def test_short_ambiguous_query_defaults_to_all(self):
        result = apply_rules("登录", ClassificationResult(
            sources=[], is_cross_source=False, query_type="search", entities={}))
        assert set(result["sources"]) == {"doc", "code", "sql"}
        assert result["is_cross_source"] is True

    def test_already_correct_unchanged(self):
        result = apply_rules("REQ-187 改了哪些代码和表", ClassificationResult(
            sources=["doc", "code", "sql"], is_cross_source=True, query_type="trace", entities={}))
        assert set(result["sources"]) == {"doc", "code", "sql"}
