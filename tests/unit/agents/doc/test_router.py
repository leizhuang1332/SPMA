import pytest
from spma.agents.doc.retriever import route_retrieval_mode


class TestRetrievalRouter:
    def test_precise_when_req_ids_present(self):
        entities = {"req_ids": ["REQ-187"], "module": None}
        assert route_retrieval_mode(entities) == "precise"

    def test_hybrid_when_module_present_no_req_ids(self):
        entities = {"req_ids": [], "module": "用户登录"}
        assert route_retrieval_mode(entities) == "hybrid"

    def test_semantic_when_no_entities(self):
        entities = {"req_ids": [], "module": None}
        assert route_retrieval_mode(entities) == "semantic"

    def test_precise_takes_priority_over_hybrid(self):
        entities = {"req_ids": ["REQ-187"], "module": "支付模块"}
        assert route_retrieval_mode(entities) == "precise"
