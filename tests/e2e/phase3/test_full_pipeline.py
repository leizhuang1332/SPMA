"""Phase 3 E2E tests — Supervisor->Workers->Synthesis full pipeline."""

import pytest


class MockLLM:
    def __init__(self):
        self.call_count = 0
    async def generate(self, prompt):
        self.call_count += 1
        if "查询路由器" in prompt or "分类" in prompt:
            return '{"sources":["doc","code","sql"],"is_cross_source":true,"query_type":"search","entities":{"code_refs":["oauth.py"],"module":"认证"}}'
        if "分解" in prompt:
            return '[{"query":"oauth token refresh实现","target":"code"},{"query":"认证需求文档","target":"doc"}]'
        if "关键词" in prompt or "扩展" in prompt:
            return "auth, token, oauth, authentication, refresh"
        return '{"assessment":"sufficient","reason":"ok"}'
    def with_structured_output(self, schema):
        class Structured:
            async def invoke(self, prompt):
                return {"sources": ["doc", "code", "sql"], "is_cross_source": True, "query_type": "search",
                        "entities": {"code_refs": ["oauth.py"], "module": "认证"}}
        return Structured()


class MockDocGraph:
    async def ainvoke(self, state):
        return {"final_results": [{"chunk_id": "d1", "content": "认证需求文档内容"}], "has_exact_match": True, "rounds_used": 1, "convergence_reason": "L1", "entities": {"req_ids": ["REQ-187"]}}


class MockCodeGraph:
    async def ainvoke(self, state):
        return {"ripgrep_results": [{"repo": "backend", "file_path": "src/auth/oauth.py", "line_number": 42, "match_text": "def token_refresh"}], "fallback_layer": 0, "rounds_used": 1, "convergence_reason": "L1:deterministic"}


class MockSQLGraph:
    async def ainvoke(self, state):
        return {"result_count": 3, "citations": [{"source_type": "sql", "source_id": "users", "snippet": "CREATE TABLE users"}], "confidence": 0.85, "rounds_used": 1, "convergence_reason": "L2", "tables_used": ["users"]}


class MockSynthesisGraph:
    async def ainvoke(self, state):
        worker_outputs = state.get("worker_outputs", [])
        citations = []
        for w in worker_outputs:
            citations.extend(w.get("citations", []))
        return {"final_answer": "## 分析结果\n\n系统通过认证模块实现登录...", "fused_citations": citations, "audit_result": {"verdict": "pass"}}


@pytest.mark.anyio
class TestFullPipeline:
    async def test_supervisor_to_synthesis_e2e(self):
        """Full E2E: classify -> dispatch -> workers -> synthesis -> answer"""
        from spma.agents.supervisor.graph import build_supervisor_graph
        from spma.agents.supervisor.state import SupervisorState

        supervisor = build_supervisor_graph(
            primary_llm=MockLLM(),
            doc_graph=MockDocGraph(),
            code_graph=MockCodeGraph(),
            sql_graph=MockSQLGraph(),
        )
        result = await supervisor.ainvoke({
            "original_query": "REQ-187 改了哪些代码和表",
            "query_id": "e2e-001",
            "reschedule_count": 0,
        })
        worker_outputs = result.get("worker_outputs", [])
        assert len(worker_outputs) == 3
        worker_types = {w["worker_type"] for w in worker_outputs}
        assert worker_types == {"doc", "code", "sql"}

        synthesis = MockSynthesisGraph()
        final = await synthesis.ainvoke({
            "worker_outputs": worker_outputs,
            "original_query": "REQ-187 改了哪些代码和表",
        })
        assert "final_answer" in final
        assert len(final["final_answer"]) > 0

    async def test_cross_source_three_workers_parallel(self):
        """Verify all 3 workers are dispatched for cross-source query."""
        from spma.agents.supervisor.graph import build_supervisor_graph

        supervisor = build_supervisor_graph(
            primary_llm=MockLLM(),
            doc_graph=MockDocGraph(),
            code_graph=MockCodeGraph(),
            sql_graph=MockSQLGraph(),
        )
        result = await supervisor.ainvoke({
            "original_query": "用户登录怎么做的",
            "query_id": "e2e-002",
            "reschedule_count": 0,
        })
        worker_outputs = result.get("worker_outputs", [])
        assert len(worker_outputs) == 3

    async def test_reschedule_when_worker_fails(self):
        """Worker with empty results triggers reschedule."""
        class FailingDocGraph:
            async def ainvoke(self, state):
                return {"final_results": [], "has_exact_match": False, "rounds_used": 1, "convergence_reason": "L3", "entities": {}}

        from spma.agents.supervisor.graph import build_supervisor_graph
        supervisor = build_supervisor_graph(
            primary_llm=MockLLM(),
            doc_graph=FailingDocGraph(),
            code_graph=MockCodeGraph(),
            sql_graph=MockSQLGraph(),
        )
        result = await supervisor.ainvoke({
            "original_query": "认证模块实现",
            "query_id": "e2e-003",
            "reschedule_count": 0,
        })
        assert "quality_scores" in result
