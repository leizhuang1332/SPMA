"""BGEM3EmbeddingAdapter 单元测试。"""

import pytest


class TestBGEM3EmbeddingAdapter:
    """测试 BGEM3Embedder → LlamaIndex BaseEmbedding 适配器。"""

    def test_class_name(self):
        """验证 class_name 返回正确标识符。"""
        from spma.agents.doc.llamaindex_embedding import BGEM3EmbeddingAdapter

        class MockEmbedder:
            async def embed(self, texts):
                return [[0.1] * 1024 for _ in texts]

        adapter = BGEM3EmbeddingAdapter(MockEmbedder())
        assert adapter.class_name() == "BGEM3EmbeddingAdapter"

    def test_model_name_propagated_to_super(self):
        """验证 model_name 正确传递给 BaseEmbedding。"""
        from spma.agents.doc.llamaindex_embedding import BGEM3EmbeddingAdapter

        class MockEmbedder:
            async def embed(self, texts):
                return [[0.1] * 1024 for _ in texts]

        adapter = BGEM3EmbeddingAdapter(MockEmbedder())
        assert adapter.model_name == "BAAI/bge-m3"

    @pytest.mark.asyncio
    async def test_aget_query_embedding_returns_correct_dim(self):
        """验证查询嵌入返回 1024 维向量。"""
        from spma.agents.doc.llamaindex_embedding import BGEM3EmbeddingAdapter

        class MockEmbedder:
            async def embed(self, texts):
                return [[0.1] * 1024 for _ in texts]

        adapter = BGEM3EmbeddingAdapter(MockEmbedder())
        result = await adapter._aget_query_embedding("测试查询")
        assert len(result) == 1024
        assert isinstance(result, list)
        assert all(isinstance(v, float) for v in result)

    @pytest.mark.asyncio
    async def test_aget_text_embeddings_batch(self):
        """验证批量文本嵌入返回正确数量。"""
        from spma.agents.doc.llamaindex_embedding import BGEM3EmbeddingAdapter

        class MockEmbedder:
            async def embed(self, texts):
                return [[0.1] * 1024 for _ in texts]

        adapter = BGEM3EmbeddingAdapter(MockEmbedder())
        results = await adapter._aget_text_embeddings(["文本1", "文本2", "文本3"])
        assert len(results) == 3
        assert all(len(r) == 1024 for r in results)

    def test_sync_methods_raise_not_implemented(self):
        """验证同步方法抛出 NotImplementedError（仅支持异步）。"""
        from spma.agents.doc.llamaindex_embedding import BGEM3EmbeddingAdapter

        class MockEmbedder:
            async def embed(self, texts):
                return [[0.1] * 1024 for _ in texts]

        adapter = BGEM3EmbeddingAdapter(MockEmbedder())
        with pytest.raises(NotImplementedError):
            adapter._get_query_embedding("test")
        with pytest.raises(NotImplementedError):
            adapter._get_text_embeddings(["test"])
