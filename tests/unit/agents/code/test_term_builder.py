# tests/unit/agents/code/test_term_builder.py
import pytest
from spma.agents.code.term_builder import build_search_terms


class TestTermBuilder:
    def test_code_refs_to_exact_terms(self):
        entities = {"code_refs": ["src/auth/oauth.py", "token_refresh"]}
        terms = build_search_terms(entities)
        assert "src/auth/oauth.py" in terms["exact_terms"]
        assert "token_refresh" in terms["exact_terms"]
        assert "oauth" in terms["fuzzy_terms"]

    def test_module_to_synonym_terms(self):
        entities = {"code_refs": [], "module": "认证"}
        terms = build_search_terms(entities)
        assert any(t in terms["exact_terms"] for t in ["auth", "authentication"])

    def test_req_ids_to_tag_terms(self):
        entities = {"req_ids": ["REQ-001", "REQ-002"]}
        terms = build_search_terms(entities)
        assert "REQ-001" in terms["tag_terms"]
        assert "REQ-002" in terms["tag_terms"]

    def test_person_to_tag_terms(self):
        entities = {"person": "张三"}
        terms = build_search_terms(entities)
        assert "author:张三" in terms["tag_terms"]

    def test_deduplication(self):
        entities = {"code_refs": ["auth.py"], "module": "认证"}
        terms = build_search_terms(entities)
        assert "auth.py" in terms["exact_terms"]
        all_terms = terms["exact_terms"] + terms["fuzzy_terms"] + terms["tag_terms"]
        assert len(all_terms) == len(set(all_terms))

    def test_table_names_to_exact_terms(self):
        entities = {"table_names": ["users", "orders"]}
        terms = build_search_terms(entities)
        assert "users" in terms["exact_terms"]
        assert "orders" in terms["exact_terms"]

    def test_empty_entities_returns_all_empty_lists(self):
        entities = {}
        terms = build_search_terms(entities)
        assert terms["exact_terms"] == []
        assert terms["fuzzy_terms"] == []
        assert terms["tag_terms"] == []

    def test_module_without_synonym_maps_to_fuzzy(self):
        entities = {"module": "some_custom_module"}
        terms = build_search_terms(entities)
        assert "some_custom_module" in terms["fuzzy_terms"]

    def test_custom_synonyms_override_default(self):
        custom_synonyms = {"自定义模块": ["custom", "module", "foo", "bar"]}
        entities = {"module": "自定义模块"}
        terms = build_search_terms(entities, module_synonyms=custom_synonyms)
        assert "custom" in terms["exact_terms"]
        assert "module" in terms["exact_terms"]
        assert "foo" in terms["exact_terms"]   # first 3 terms → exact
        assert "bar" in terms["fuzzy_terms"]   # terms[3:] → fuzzy
