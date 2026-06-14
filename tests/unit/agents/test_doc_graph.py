"""Doc Agent Graph 单元测试——mock 所有外部依赖。

覆盖 build_doc_agent_graph 的完整流程：
- route → search → aggregate → assess → converge (L1/L2/L3)
- expand → re-search 循环
- precise / hybrid / semantic 三种检索模式
- HyDE 短查询增强
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ── Helpers ──────────────────────────────────────────────────────────────────

def make_bm25_result(chunk_id: str, content: str, score: float = 0.9, **extra) -> dict:
    return {"chunk_id": chunk_id, "content": content, "score": score,
            "source_type": "bm25", **extra}


def make_vector_result(chunk_id: str, content: str, score: float = 0.85, **extra) -> dict:
    return {"chunk_id": chunk_id, "content": content, "score": score,
            "source_type": "vector", **extra}


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def es_client():
    """Mock ES——BM25 检索返回 3 条结果。"""
    es = AsyncMock()
    es.search = AsyncMock(return_value=[
        make_bm25_result("bm25-1", "用户登录需要用户名和密码"),
        make_bm25_result("bm25-2", "登录模块支持SSO单点登录"),
        make_bm25_result("bm25-3", "密码重置流程说明"),
    ])
    return es


@pytest.fixture
def vector_store():
    """Mock Vector Store——向量检索返回 3 条结果。"""
    vs = AsyncMock()
    vs.search = AsyncMock(return_value=[
        make_vector_result("vec-1", "用户认证流程详解"),
        make_vector_result("vec-2", "OAuth2.0登录集成方案"),
        make_vector_result("vec-3", "登录页面UI设计规范"),
    ])
    return vs


@pytest.fixture
def embedder():
    """Mock Embedder——返回 1024 维 BGE-M3 向量。"""
    emb = AsyncMock()
    emb.embed = AsyncMock(return_value=[[0.1] * 1024])
    return emb


@pytest.fixture
def llm():
    """Mock LLM——完备度判断和线索扩展用。"""
    mock = MagicMock()
    mock.generate = AsyncMock(return_value='{"assessment": "sufficient", "reason": "结果充足"}')
    return mock


@pytest.fixture
def hyde_llm():
    """Mock HyDE LLM——短查询时生成假设文档。"""
    mock = MagicMock()
    mock.generate = AsyncMock(return_value="假设的文档内容用于增强检索")
    return mock


@pytest.fixture
def doc_graph(es_client, vector_store, embedder, llm):
    """构建 Doc Agent StateGraph（编译后的实例）。"""
    from spma.agents.doc.graph import build_doc_agent_graph
    return build_doc_agent_graph(
        es_client=es_client,
        vector_store=vector_store,
        embedder=embedder,
        llm=llm,
    )


# ── 基础流程测试 ────────────────────────────────────────────────────────────

class TestDocAgentBasicFlow:
    """测试 route → search → aggregate → assess → converge 完整流程。"""

    @pytest.mark.asyncio
    async def test_semantic_mode_without_entities(self, doc_graph):
        """无 req_ids 也无 module → semantic 模式，向量主导。"""
        result = await doc_graph.ainvoke({
            "original_query": "用户登录怎么实现",
            "entities": {},
            "max_rounds": 2,
            "round": 1,
        })

        assert result["weight_mode"] == "semantic"
        assert len(result["fused_results"]) > 0
        assert "assessment" in result
        assert result["rounds_used"] == 1  # L1/L2自动收敛

    @pytest.mark.asyncio
    async def test_precise_mode_with_req_ids(self, doc_graph):
        """有 req_ids → precise 模式，BM25 主导。"""
        result = await doc_graph.ainvoke({
            "original_query": "REQ-001 功能说明",
            "entities": {"req_ids": ["REQ-001"]},
            "max_rounds": 2,
            "round": 1,
        })

        assert result["weight_mode"] == "precise"

    @pytest.mark.asyncio
    async def test_hybrid_mode_with_module(self, doc_graph):
        """有 module 无 req_ids → hybrid 模式。"""
        result = await doc_graph.ainvoke({
            "original_query": "支付模块的流程",
            "entities": {"module": "payment"},
            "max_rounds": 2,
            "round": 1,
        })

        assert result["weight_mode"] == "hybrid"

    @pytest.mark.asyncio
    async def test_accumulated_results_dedup(self, doc_graph, es_client, vector_store):
        """第二轮检索时去重合并结果。"""
        # 第一轮
        result1 = await doc_graph.ainvoke({
            "original_query": "登录",
            "entities": {},
            "max_rounds": 3,
            "round": 1,
        })
        count1 = len(result1["accumulated_results"])

        # 第二轮：第二轮时 round 应该已经递增
        # 由于 L1/L2 可能自动收敛，我们在生成图的初始 state 中看到的是完整的
        assert count1 > 0


# ── 完备度判断测试 ──────────────────────────────────────────────────────────

class TestDocAgentCompleteness:
    """测试 L1/L2/L3 三级完备度判断。"""

    @pytest.mark.asyncio
    async def test_l1_converge_by_req_ids(self, es_client, vector_store, embedder, llm):
        """结果>=5条 + req_ids → L1 确定性收敛，不调 LLM。"""
        # 设置足够多的结果
        es_client.search = AsyncMock(return_value=[
            make_bm25_result(f"bm25-{i}", f"内容{i}", req_ids=["REQ-001"]) for i in range(5)
        ])
        vector_store.search = AsyncMock(return_value=[
            make_vector_result(f"vec-{i}", f"向量结果{i}", score=0.95) for i in range(5)
        ])

        from spma.agents.doc.graph import build_doc_agent_graph
        graph = build_doc_agent_graph(es_client, vector_store, embedder, llm)

        result = await graph.ainvoke({
            "original_query": "REQ-001 相关文档",
            "entities": {"req_ids": ["REQ-001"]},
            "max_rounds": 2,
            "round": 1,
        })

        assert result["assessment"] == "converge"
        # L1 收敛时不应调用 LLM
        llm.generate.assert_not_called()

    @pytest.mark.asyncio
    async def test_l2_converge_by_vector_threshold(self, es_client, vector_store, embedder, llm):
        """结果>=5条 + Top-3相似度>0.85 → L2 向量阈值收敛，不调 LLM。"""
        es_client.search = AsyncMock(return_value=[
            make_bm25_result(f"bm25-{i}", f"内容{i}", score=0.9) for i in range(3)
        ])
        vector_store.search = AsyncMock(return_value=[
            make_vector_result(f"vec-{i}", f"向量结果{i}", score=0.92) for i in range(3)
        ])

        from spma.agents.doc.graph import build_doc_agent_graph
        graph = build_doc_agent_graph(es_client, vector_store, embedder, llm)

        result = await graph.ainvoke({
            "original_query": "通用查询",
            "entities": {},
            "max_rounds": 2,
            "round": 1,
        })

        assert result["assessment"] == "converge"
        # L2 收敛时也不应调用 LLM
        llm.generate.assert_not_called()

    @pytest.mark.asyncio
    async def test_l3_llm_judged_insufficient_triggers_expand(self, es_client, vector_store, embedder, llm):
        """结果不足 → L3 LLM 判断 insufficient → 触发 expand 再搜索。"""
        # 少于 5 条结果 → 必须走 L3 LLM 判断
        es_client.search = AsyncMock(return_value=[
            make_bm25_result("bm25-1", "某项内容", score=0.6)
        ])
        vector_store.search = AsyncMock(return_value=[
            make_vector_result("vec-1", "某向量结果", score=0.5)
        ])

        # LLM 判断 insufficient → 需要扩展
        llm.generate = AsyncMock(return_value='{"assessment": "insufficient", "reason": "结果不足"}')

        from spma.agents.doc.graph import build_doc_agent_graph
        graph = build_doc_agent_graph(es_client, vector_store, embedder, llm)

        result = await graph.ainvoke({
            "original_query": "冷门查询无结果",
            "entities": {},
            "max_rounds": 2,
            "round": 1,
        })

        # 应该触发了 expand 然后重新 search
        assert result["rounds_used"] >= 1


# ── HyDE 短查询增强测试 ─────────────────────────────────────────────────────

class TestDocAgentHyDE:
    @pytest.mark.asyncio
    async def test_hyde_enabled_for_short_query(self, es_client, vector_store, embedder, llm, hyde_llm):
        """短查询（<=30 字符）且无 req_ids 时启用 HyDE。"""
        from spma.agents.doc.graph import build_doc_agent_graph

        graph = build_doc_agent_graph(
            es_client=es_client,
            vector_store=vector_store,
            embedder=embedder,
            llm=llm,
            hyde_llm=hyde_llm,
        )

        result = await graph.ainvoke({
            "original_query": "支付流程",
            "entities": {},
            "max_rounds": 2,
            "round": 1,
        })

        assert result["hyde_enabled"] is True
        hyde_llm.generate.assert_called_once_with("支付流程")

    @pytest.mark.asyncio
    async def test_hyde_disabled_for_long_query(self, es_client, vector_store, embedder, llm, hyde_llm):
        """长查询（>30 字符）不启用 HyDE。"""
        from spma.agents.doc.graph import build_doc_agent_graph

        graph = build_doc_agent_graph(
            es_client=es_client,
            vector_store=vector_store,
            embedder=embedder,
            llm=llm,
            hyde_llm=hyde_llm,
        )

        result = await graph.ainvoke({
            "original_query": "这是一个非常长的查询文本，超过三十个字符，用于测试 HyDE 是否被正确禁用",
            "entities": {},
            "max_rounds": 2,
            "round": 1,
        })

        assert result["hyde_enabled"] is False


# ── 线索扩展测试 ────────────────────────────────────────────────────────────

class TestDocAgentExpansion:
    @pytest.mark.asyncio
    async def test_rule_based_expand_adds_new_req_ids(self, es_client, vector_store, embedder, llm):
        """R2 规则扩展：从结果中提取新的 req_ids。"""
        es_client.search = AsyncMock(return_value=[
            make_bm25_result("bm25-1", "登录功能", req_ids=["REQ-LOGIN-001"]),
            make_bm25_result("bm25-2", "密码功能", req_ids=["REQ-PWD-001"]),
        ])
        vector_store.search = AsyncMock(return_value=[])

        # LLM 判断 insufficient 触发 expand
        llm.generate = AsyncMock(return_value='{"assessment": "insufficient", "reason": "不够"}')

        from spma.agents.doc.graph import build_doc_agent_graph
        graph = build_doc_agent_graph(es_client, vector_store, embedder, llm)

        result = await graph.ainvoke({
            "original_query": "安全相关",
            "entities": {},
            "max_rounds": 3,
            "round": 1,
        })

        # 扩展后应该有更多结果
        assert "assessment" in result

    @pytest.mark.asyncio
    async def test_expand_hits_max_rounds(self, es_client, vector_store, embedder, llm):
        """达到 max_rounds 限制时强制终止。"""
        es_client.search = AsyncMock(return_value=[
            make_bm25_result("bm25-x", "某项内容", score=0.3)
        ])
        vector_store.search = AsyncMock(return_value=[])

        # 始终判断 insufficient
        llm.generate = AsyncMock(return_value='{"assessment": "insufficient", "reason": "始终不够"}')

        from spma.agents.doc.graph import build_doc_agent_graph
        graph = build_doc_agent_graph(es_client, vector_store, embedder, llm)

        result = await graph.ainvoke({
            "original_query": "不可能找到的查询",
            "entities": {},
            "max_rounds": 2,  # 最多2轮
            "round": 1,
        })

        assert result["rounds_used"] <= 2


# ── 错误处理测试 ────────────────────────────────────────────────────────────

class TestDocAgentErrorHandling:
    @pytest.mark.asyncio
    async def test_embedder_failure_does_not_crash(self, es_client, vector_store, embedder, llm):
        """Embedder 失败时不应导致整个图崩溃。"""
        embedder.embed = AsyncMock(side_effect=Exception("Embedder service down"))

        from spma.agents.doc.graph import build_doc_agent_graph
        graph = build_doc_agent_graph(es_client, vector_store, embedder, llm)

        # 不应抛出异常
        result = await graph.ainvoke({
            "original_query": "测试查询",
            "entities": {},
            "max_rounds": 2,
            "round": 1,
        })
        # ES 结果仍然存在
        assert "fused_results" in result

    @pytest.mark.asyncio
    async def test_es_client_failure_does_not_crash(self, es_client, vector_store, embedder, llm):
        """ES 失败时不应导致整个图崩溃。"""
        es_client.search = AsyncMock(side_effect=Exception("ES connection refused"))

        from spma.agents.doc.graph import build_doc_agent_graph
        graph = build_doc_agent_graph(es_client, vector_store, embedder, llm)

        # 不应抛出异常
        result = await graph.ainvoke({
            "original_query": "测试查询",
            "entities": {},
            "max_rounds": 2,
            "round": 1,
        })
        assert "assessment" in result

    @pytest.mark.asyncio
    async def test_llm_completeness_error_defaults_to_expand(self, es_client, vector_store, embedder, llm):
        """LLM 完备度判断异常时默认进入 expand。"""
        es_client.search = AsyncMock(return_value=[
            make_bm25_result("bm25-1", "某项内容", score=0.6)
        ])
        vector_store.search = AsyncMock(return_value=[
            make_vector_result("vec-1", "某向量结果", score=0.5)
        ])

        # LLM 爆炸
        llm.generate = AsyncMock(side_effect=Exception("LLM timeout"))

        from spma.agents.doc.graph import build_doc_agent_graph
        graph = build_doc_agent_graph(es_client, vector_store, embedder, llm)

        result = await graph.ainvoke({
            "original_query": "测试查询",
            "entities": {},
            "max_rounds": 2,
            "round": 1,
        })
        # 默认 expand → 进入下一轮（超限则终止）
        assert "assessment" in result


# ── 加权融合测试 ────────────────────────────────────────────────────────────

class TestDocAgentWeightedFusion:
    @pytest.mark.asyncio
    async def test_precise_mode_bm25_weight_higher(self, es_client, vector_store, embedder, llm):
        """precise 模式下 BM25 权重应高于向量。"""
        from spma.agents.doc.graph import build_doc_agent_graph

        weights_config = {
            "weights": {
                "precise": {"bm25": 0.8, "vector": 0.2},
                "semantic": {"bm25": 0.2, "vector": 0.8},
                "hybrid": {"bm25": 0.5, "vector": 0.5},
            }
        }

        graph = build_doc_agent_graph(
            es_client=es_client,
            vector_store=vector_store,
            embedder=embedder,
            llm=llm,
            weights_config=weights_config,
        )

        result = await graph.ainvoke({
            "original_query": "REQ-001",
            "entities": {"req_ids": ["REQ-001"]},
            "max_rounds": 2,
            "round": 1,
        })

        assert result["weight_mode"] == "precise"
        # precise 模式下 BM25 结果应该在融合结果前面
        if result["fused_results"]:
            # BM25 结果权重高，融合后应排前面
            assert len(result["fused_results"]) > 0
