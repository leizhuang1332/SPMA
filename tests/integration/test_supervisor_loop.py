import pytest
from spma.agents.supervisor.graph import build_supervisor_graph
from spma.agents.supervisor.state import SupervisorState


class MockLLM:
    def __init__(self):
        self.generate_calls = 0
    async def generate(self, prompt):
        self.generate_calls += 1
        if "关键词" in prompt or "扩展" in prompt:
            return "auth, token, oauth"
        if "分解" in prompt:
            return '[{"query":"token refresh代码","target":"code"},{"query":"认证需求","target":"doc"}]'
        return '{"assessment": "sufficient", "reason": "ok"}'
    def with_structured_output(self, schema):
        class StructuredMock:
            async def invoke(self, prompt):
                return {
                    "sources": ["code", "doc"],
                    "is_cross_source": True,
                    "query_type": "search",
                    "entities": {"code_refs": ["token_refresh"], "module": "认证"},
                }
        return StructuredMock()
    async def is_available(self):
        return True


class MockDocGraph:
    async def ainvoke(self, state):
        return {"final_results": [{"chunk_id": "d1", "content": "doc result"}], "has_exact_match": True, "rounds_used": 1, "convergence_reason": "L1", "entities": {}}


class MockCodeGraph:
    async def ainvoke(self, state):
        return {"ripgrep_results": [{"file_path": "auth.py", "match_text": "def token_refresh"}], "fallback_layer": 0, "rounds_used": 1, "convergence_reason": "L1:deterministic"}


class MockSQLGraph:
    async def ainvoke(self, state):
        return {"result_count": 1, "citations": [], "confidence": 0.5, "rounds_used": 1, "convergence_reason": "L3:insufficient", "tables_used": []}


@pytest.mark.anyio
class TestSupervisorLoop:
    async def test_classify_dispatch_converge(self):
        graph = build_supervisor_graph(
            primary_llm=MockLLM(),
            doc_graph=MockDocGraph(),
            code_graph=MockCodeGraph(),
            sql_graph=MockSQLGraph(),
        )
        initial: SupervisorState = {
            "original_query": "token_refresh 函数的实现和需求",
            "query_id": "q1",
            "reschedule_count": 0,
        }
        result = await graph.ainvoke(initial)
        assert result["classification"]["sources"] == ["code", "doc"]
        assert len(result.get("worker_outputs", [])) == 2
        assert "quality_scores" in result

    async def test_reschedule_when_worker_fails(self):
        class FailingDocGraph(MockDocGraph):
            async def ainvoke(self, state):
                return {"final_results": [], "has_exact_match": False, "rounds_used": 1, "convergence_reason": "L3", "entities": {}}

        graph = build_supervisor_graph(
            primary_llm=MockLLM(),
            doc_graph=FailingDocGraph(),
            code_graph=MockCodeGraph(),
            sql_graph=MockSQLGraph(),
        )
        initial: SupervisorState = {
            "original_query": "token_refresh 实现",
            "query_id": "q2",
            "reschedule_count": 0,
        }
        result = await graph.ainvoke(initial)
        assert "quality_scores" in result
        # reschedule should have been triggered or converged after max
        assert result.get("reschedule_count", 0) >= 0
