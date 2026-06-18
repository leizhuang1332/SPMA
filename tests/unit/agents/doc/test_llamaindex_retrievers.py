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
