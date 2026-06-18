"""LlamaIndex 自定义 Retriever 单元测试。"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from llama_index.core import QueryBundle
from llama_index.core.schema import NodeWithScore


class TestESBM25Retriever:
    """测试 ESBM25Retriever——ES 客户端 → BaseRetriever 适配。"""

    @pytest.mark.asyncio
    async def test_aretrieve_converts_es_results_to_nodes(self):
        """验证 ES 返回的 dict 正确转换为 NodeWithScore 列表。"""
        from spma.agents.doc.llamaindex_retrievers import ESBM25Retriever

        mock_es = AsyncMock()
        mock_es.search = AsyncMock(return_value=[
            {
                "chunk_id": "chunk-1",
                "content": "需求文档内容片段A",
                "source_id": "doc-123",
                "source_type": "doc",
                "req_ids": ["REQ-001"],
                "score": 12.5,
                "metadata": {"page": 3},
            },
            {
                "chunk_id": "chunk-2",
                "content": "需求文档内容片段B",
                "source_id": "doc-456",
                "source_type": "doc",
                "req_ids": [],
                "score": 8.2,
            },
        ])

        retriever = ESBM25Retriever(es_client=mock_es, top_k=20)
        query_bundle = QueryBundle(query_str="用户登录需求")
        results = await retriever._aretrieve(query_bundle)

        assert len(results) == 2
        assert isinstance(results[0], NodeWithScore)
        assert results[0].node.node_id == "chunk-1"
        assert results[0].node.get_content() == "需求文档内容片段A"
        assert results[0].node.metadata["source_id"] == "doc-123"
        assert results[0].node.metadata["req_ids"] == ["REQ-001"]
        assert results[0].node.metadata["retrieval_source"] == "bm25"
        assert results[0].score == 12.5
        mock_es.search.assert_called_once_with(
            query="用户登录需求", top_k=20, filters=None
        )

    @pytest.mark.asyncio
    async def test_aretrieve_handles_empty_results(self):
        """验证空结果返回空列表不崩溃。"""
        from spma.agents.doc.llamaindex_retrievers import ESBM25Retriever

        mock_es = AsyncMock()
        mock_es.search = AsyncMock(return_value=[])

        retriever = ESBM25Retriever(es_client=mock_es, top_k=20)
        query_bundle = QueryBundle(query_str="不存在的查询")
        results = await retriever._aretrieve(query_bundle)

        assert results == []

    @pytest.mark.asyncio
    async def test_filters_passed_to_es_search(self):
        """验证 filters 参数透传到 ESClient.search。"""
        from spma.agents.doc.llamaindex_retrievers import ESBM25Retriever

        mock_es = AsyncMock()
        mock_es.search = AsyncMock(return_value=[])

        retriever = ESBM25Retriever(
            es_client=mock_es, top_k=10, filters={"req_ids": ["REQ-187"]}
        )
        query_bundle = QueryBundle(query_str="精确匹配查询")
        await retriever._aretrieve(query_bundle)

        mock_es.search.assert_called_once_with(
            query="精确匹配查询", top_k=10, filters={"req_ids": ["REQ-187"]}
        )

    @pytest.mark.asyncio
    async def test_set_filters_updates_runtime(self):
        """验证 set_filters 运行时更新过滤条件。"""
        from spma.agents.doc.llamaindex_retrievers import ESBM25Retriever

        mock_es = AsyncMock()
        mock_es.search = AsyncMock(return_value=[])

        retriever = ESBM25Retriever(es_client=mock_es, top_k=20)
        retriever.set_filters({"req_ids": ["REQ-NEW"]})

        query_bundle = QueryBundle(query_str="运行时过滤")
        await retriever._aretrieve(query_bundle)

        mock_es.search.assert_called_once_with(
            query="运行时过滤", top_k=20, filters={"req_ids": ["REQ-NEW"]}
        )

    def test_sync_retrieve_raises_not_implemented(self):
        """验证同步 _retrieve 抛出 NotImplementedError。"""
        from spma.agents.doc.llamaindex_retrievers import ESBM25Retriever

        mock_es = MagicMock()
        retriever = ESBM25Retriever(es_client=mock_es)
        with pytest.raises(NotImplementedError):
            retriever._retrieve(QueryBundle(query_str="test"))

    @pytest.mark.asyncio
    async def test_query_str_empty_uses_empty_string(self):
        """验证 query_str 为空时不崩溃。"""
        from spma.agents.doc.llamaindex_retrievers import ESBM25Retriever

        mock_es = AsyncMock()
        mock_es.search = AsyncMock(return_value=[])

        retriever = ESBM25Retriever(es_client=mock_es)
        query_bundle = QueryBundle(query_str="", embedding=[0.1] * 1024)
        results = await retriever._aretrieve(query_bundle)

        assert results == []
        mock_es.search.assert_called_once_with(
            query="", top_k=20, filters=None
        )


class TestHybridRRFRetriever:
    """测试 HybridRRFRetriever——并行 BM25+向量 + 加权 RRF 融合。"""

    def _make_node(self, node_id: str, text: str, retrieval_source: str, score: float):
        """构造 NodeWithScore 辅助方法。"""
        from llama_index.core.schema import TextNode, NodeWithScore
        node = TextNode(
            id_=node_id,
            text=text,
            metadata={"retrieval_source": retrieval_source},
        )
        return NodeWithScore(node=node, score=float(score))

    @pytest.mark.asyncio
    async def test_rrf_fusion_combines_both_sources(self):
        """验证两路结果正确融合，去重并排序。"""
        from spma.agents.doc.llamaindex_retrievers import (
            HybridRRFRetriever,
            RRFConfig,
        )

        mock_vector = AsyncMock()
        mock_vector.aretrieve = AsyncMock(return_value=[
            self._make_node("c1", "内容1", "vector", 0.95),
            self._make_node("c2", "内容2", "vector", 0.80),
        ])
        mock_bm25 = AsyncMock()
        mock_bm25.aretrieve = AsyncMock(return_value=[
            self._make_node("c2", "内容2", "bm25", 12.5),
            self._make_node("c3", "内容3", "bm25", 8.2),
        ])

        config = RRFConfig(k=60, top_k=10, bm25_weight=0.5, vector_weight=0.5)
        retriever = HybridRRFRetriever(
            vector_retriever=mock_vector,
            bm25_retriever=mock_bm25,
            config=config,
        )

        from llama_index.core import QueryBundle
        query_bundle = QueryBundle(query_str="测试查询")
        results = await retriever.aretrieve(query_bundle)

        assert len(results) == 3
        c2_result = next(r for r in results if r.node.node_id == "c2")
        assert results[0].node.node_id == "c2"
        assert c2_result.score > 0
        assert "rrf_score" in c2_result.node.metadata

    @pytest.mark.asyncio
    async def test_rrf_fusion_with_custom_weights(self):
        """验证自定义权重的 RRF 融合。"""
        from spma.agents.doc.llamaindex_retrievers import (
            HybridRRFRetriever,
            RRFConfig,
        )

        mock_vector = AsyncMock()
        mock_vector.aretrieve = AsyncMock(return_value=[
            self._make_node("c1", "向量结果", "vector", 0.9),
        ])
        mock_bm25 = AsyncMock()
        mock_bm25.aretrieve = AsyncMock(return_value=[
            self._make_node("c2", "BM25结果", "bm25", 10.0),
        ])

        config = RRFConfig(k=60, top_k=10, bm25_weight=0.8, vector_weight=0.2)
        retriever = HybridRRFRetriever(
            vector_retriever=mock_vector,
            bm25_retriever=mock_bm25,
            config=config,
        )

        from llama_index.core import QueryBundle
        query_bundle = QueryBundle(query_str="测试查询")
        results = await retriever.aretrieve(query_bundle)

        assert len(results) == 2
        assert results[0].node.node_id == "c2"

    @pytest.mark.asyncio
    async def test_rrf_truncates_to_top_k(self):
        """验证 RRF 融合按 top_k 截断。"""
        from spma.agents.doc.llamaindex_retrievers import (
            HybridRRFRetriever,
            RRFConfig,
        )

        vector_nodes = [
            self._make_node(f"v{i}", f"向量内容{i}", "vector", 0.9 - i * 0.05)
            for i in range(10)
        ]
        mock_vector = AsyncMock()
        mock_vector.aretrieve = AsyncMock(return_value=vector_nodes)
        mock_bm25 = AsyncMock()
        mock_bm25.aretrieve = AsyncMock(return_value=[])

        config = RRFConfig(k=60, top_k=3, bm25_weight=0.5, vector_weight=0.5)
        retriever = HybridRRFRetriever(
            vector_retriever=mock_vector,
            bm25_retriever=mock_bm25,
            config=config,
        )

        from llama_index.core import QueryBundle
        query_bundle = QueryBundle(query_str="测试查询")
        results = await retriever.aretrieve(query_bundle)

        assert len(results) == 3
        assert results[0].node.node_id == "v0"
        assert results[1].node.node_id == "v1"
        assert results[2].node.node_id == "v2"

    @pytest.mark.asyncio
    async def test_both_retrievers_called_concurrently(self):
        """验证两个子检索器被并行调用。"""
        import asyncio
        from spma.agents.doc.llamaindex_retrievers import (
            HybridRRFRetriever,
            RRFConfig,
        )

        order = []

        async def slow_vector(query_bundle):
            await asyncio.sleep(0.05)
            order.append("vector")
            return []

        async def slow_bm25(query_bundle):
            await asyncio.sleep(0.05)
            order.append("bm25")
            return []

        mock_vector = AsyncMock()
        mock_vector.aretrieve = slow_vector
        mock_bm25 = AsyncMock()
        mock_bm25.aretrieve = slow_bm25

        config = RRFConfig()
        retriever = HybridRRFRetriever(
            vector_retriever=mock_vector,
            bm25_retriever=mock_bm25,
            config=config,
        )

        from llama_index.core import QueryBundle
        query_bundle = QueryBundle(query_str="测试查询")
        await retriever.aretrieve(query_bundle)

        assert "vector" in order
        assert "bm25" in order

    def test_sync_retrieve_raises_not_implemented(self):
        """验证同步 _retrieve 抛出 NotImplementedError。"""
        from unittest.mock import MagicMock
        from spma.agents.doc.llamaindex_retrievers import (
            HybridRRFRetriever,
            RRFConfig,
        )

        mock_vector = MagicMock()
        mock_bm25 = MagicMock()
        retriever = HybridRRFRetriever(
            vector_retriever=mock_vector,
            bm25_retriever=mock_bm25,
            config=RRFConfig(),
        )
        from llama_index.core import QueryBundle
        with pytest.raises(NotImplementedError):
            retriever._retrieve(QueryBundle(query_str="test"))
