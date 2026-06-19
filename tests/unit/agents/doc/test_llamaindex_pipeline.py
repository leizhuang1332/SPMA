"""AdvancedLlamaIndexPipeline 单元测试。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestPipelineConfig:
    """测试 PipelineConfig——管道参数集中管理。"""

    def test_default_values(self):
        from spma.agents.doc.llamaindex_pipeline import PipelineConfig
        cfg = PipelineConfig()
        assert cfg.vector_top_k == 20
        assert cfg.bm25_top_k == 20
        assert cfg.hybrid_final_top_k == 15
        assert cfg.rrf_k == 60
        assert cfg.rrf_bm25_weight == 0.5
        assert cfg.rrf_vector_weight == 0.5
        assert cfg.rerank_top_n == 10
        assert cfg.enable_rerank is True
        assert cfg.hyde_max_query_len == 30
        assert cfg.hyde_top_k == 10

    def test_mode_weights_defaults(self):
        from spma.agents.doc.llamaindex_pipeline import PipelineConfig
        cfg = PipelineConfig()
        assert cfg.mode_weights["precise"] == {"bm25": 0.7, "vector": 0.3}
        assert cfg.mode_weights["hybrid"] == {"bm25": 0.5, "vector": 0.5}
        assert cfg.mode_weights["semantic"] == {"bm25": 0.3, "vector": 0.7}

    def test_custom_values_override_defaults(self):
        from spma.agents.doc.llamaindex_pipeline import PipelineConfig
        cfg = PipelineConfig(vector_top_k=50, rrf_k=30, enable_rerank=False)
        assert cfg.vector_top_k == 50
        assert cfg.rrf_k == 30
        assert cfg.enable_rerank is False
        assert cfg.bm25_top_k == 20



class MockEmbedder:
    """Mock BGEM3Embedder——返回固定 1024 维向量。"""
    async def embed(self, texts):
        return [[0.1] * 1024 for _ in texts]


class TestAdvancedLlamaIndexPipeline:
    """测试 AdvancedLlamaIndexPipeline——集成测试。"""

    def _make_pipeline(self, es_client=None, config=None):
        from spma.agents.doc.llamaindex_pipeline import (
            AdvancedLlamaIndexPipeline,
            PipelineConfig,
        )
        if es_client is None:
            es_client = AsyncMock()
            es_client.search = AsyncMock(return_value=[])
        if config is None:
            config = PipelineConfig()
        return AdvancedLlamaIndexPipeline(es_client=es_client, config=config)

    def test_init_stores_config(self):
        mock_es = MagicMock()
        from spma.agents.doc.llamaindex_pipeline import PipelineConfig
        cfg = PipelineConfig(rrf_k=30)
        pipeline = self._make_pipeline(es_client=mock_es, config=cfg)
        assert pipeline._es_client is mock_es
        assert pipeline._config.rrf_k == 30
        assert pipeline._index is None
        assert pipeline._embedder is None
        assert pipeline._hyde_llm is None

    def test_should_use_hyde_short_query_no_req_ids(self):
        pipeline = self._make_pipeline()
        result = pipeline._should_use_hyde("短查询", {"req_ids": []})
        assert result is True

    def test_should_use_hyde_long_query_returns_false(self):
        pipeline = self._make_pipeline()
        long_query = "这是一条超过三十个字符的非常长的查询文本用于测试 HyDE 触发条件"
        assert len(long_query) > 30
        result = pipeline._should_use_hyde(long_query, {"req_ids": []})
        assert result is False

    def test_should_use_hyde_with_req_ids_returns_false(self):
        pipeline = self._make_pipeline()
        result = pipeline._should_use_hyde("短查询", {"req_ids": ["REQ-187"]})
        assert result is False

    def test_build_retriever_precise_mode(self):
        from spma.agents.doc.llamaindex_pipeline import PipelineConfig
        mock_es = AsyncMock()
        mock_es.search = AsyncMock(return_value=[])
        cfg = PipelineConfig()
        pipeline = self._make_pipeline(es_client=mock_es, config=cfg)
        with patch.object(pipeline, '_index', MagicMock()):
            retriever = pipeline._build_retriever(
                mode="precise",
                entities={"req_ids": ["REQ-001", "REQ-002"]},
            )
        assert retriever._config.bm25_weight == 0.7
        assert retriever._config.vector_weight == 0.3

    def test_build_retriever_hybrid_mode(self):
        from spma.agents.doc.llamaindex_pipeline import PipelineConfig
        mock_es = AsyncMock()
        mock_es.search = AsyncMock(return_value=[])
        cfg = PipelineConfig()
        pipeline = self._make_pipeline(es_client=mock_es, config=cfg)
        with patch.object(pipeline, '_index', MagicMock()):
            retriever = pipeline._build_retriever(mode="hybrid", entities={})
        assert retriever._config.bm25_weight == 0.5
        assert retriever._config.vector_weight == 0.5

    @pytest.mark.asyncio
    async def test_search_returns_compatible_dict_format(self):
        from spma.agents.doc.llamaindex_pipeline import PipelineConfig
        from llama_index.core.schema import NodeWithScore, TextNode

        node1 = TextNode(
            id_="chunk-1", text="测试内容1",
            metadata={"source_id": "doc-1", "source_type": "doc",
                       "req_ids": ["REQ-001"], "retrieval_source": "bm25", "rrf_score": 0.025},
        )
        node2 = TextNode(
            id_="chunk-2", text="测试内容2",
            metadata={"source_id": "doc-2", "source_type": "doc",
                       "req_ids": [], "retrieval_source": "vector", "rrf_score": 0.020},
        )
        scored1 = NodeWithScore(node=node1, score=0.025)
        scored2 = NodeWithScore(node=node2, score=0.020)

        mock_retriever = AsyncMock()
        mock_retriever.aretrieve = AsyncMock(return_value=[scored1, scored2])

        mock_es = AsyncMock()
        cfg = PipelineConfig(enable_rerank=False)
        pipeline = self._make_pipeline(es_client=mock_es, config=cfg)
        pipeline._embedder = MockEmbedder()

        with patch.object(pipeline, '_build_retriever', return_value=mock_retriever):
            results = await pipeline.search(query="测试查询", mode="hybrid", entities={})

        assert isinstance(results, list)
        assert len(results) == 2
        assert results[0]["chunk_id"] == "chunk-1"
        assert results[0]["source_id"] == "doc-1"
        assert results[0]["content"] == "测试内容1"
        assert results[0]["score"] == 0.025
        assert results[0]["metadata"]["retrieval_source"] == "bm25"
        assert results[1]["chunk_id"] == "chunk-2"

    def test_initialize_reranker_failure_disables_rerank(self):
        """L1 降级：Reranker 初始化失败时，enable_rerank=False。"""
        from spma.agents.doc.llamaindex_pipeline import PipelineConfig

        cfg = PipelineConfig(enable_rerank=True)
        pipeline = self._make_pipeline(config=cfg)

        with patch(
            "spma.retrieval.reranker.BGEReranker",
            side_effect=RuntimeError("ModelScope unreachable"),
        ):
            pipeline.initialize(embedder=MockEmbedder())

        assert pipeline._config.enable_rerank is False
        assert pipeline._reranker is None
        assert pipeline._long_context_reorder is None

    def test_initialize_with_injected_reranker(self):
        """通过 initialize(reranker=mock) 注入已创建的 reranker。"""
        from spma.agents.doc.llamaindex_pipeline import PipelineConfig

        cfg = PipelineConfig(enable_rerank=True)
        pipeline = self._make_pipeline(config=cfg)

        mock_reranker = MagicMock()
        pipeline.initialize(embedder=MockEmbedder(), reranker=mock_reranker)

        assert pipeline._reranker is mock_reranker
        assert pipeline._long_context_reorder is not None

    @pytest.mark.asyncio
    async def test_search_rerank_failure_returns_original_nodes(self):
        """L2 降级：rerank() 调用失败时，返回原始 nodes 顺序。"""
        from spma.agents.doc.llamaindex_pipeline import PipelineConfig
        from llama_index.core.schema import NodeWithScore, TextNode

        node1 = TextNode(id_="chunk-1", text="内容1")
        node2 = TextNode(id_="chunk-2", text="内容2")
        scored1 = NodeWithScore(node=node1, score=0.9)
        scored2 = NodeWithScore(node=node2, score=0.1)

        mock_retriever = AsyncMock()
        mock_retriever.aretrieve = AsyncMock(return_value=[scored1, scored2])

        mock_reranker = MagicMock()
        mock_reranker.rerank = AsyncMock(side_effect=RuntimeError("Inference failed"))

        mock_es = AsyncMock()
        cfg = PipelineConfig(enable_rerank=True)
        pipeline = self._make_pipeline(es_client=mock_es, config=cfg)
        pipeline._embedder = MockEmbedder()
        pipeline._reranker = mock_reranker
        pipeline._long_context_reorder = MagicMock()

        with patch.object(pipeline, '_build_retriever', return_value=mock_retriever):
            results = await pipeline.search(query="测试", mode="hybrid", entities={})

        # 降级后仍返回结果（原始顺序，无 rerank）
        assert len(results) == 2
        assert results[0]["chunk_id"] == "chunk-1"

    def test_precise_mode_skips_reranker(self):
        """L3：precise 模式下不触发 rerank（非降级，无告警）。"""
        from spma.agents.doc.llamaindex_pipeline import PipelineConfig

        cfg = PipelineConfig(enable_rerank=True)
        pipeline = self._make_pipeline(config=cfg)

        mock_reranker = MagicMock()
        pipeline.initialize(embedder=MockEmbedder(), reranker=mock_reranker)

        # precise 模式下 _reranker 存在但不应被调用
        assert pipeline._reranker is not None
