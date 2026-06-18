"""将现有 BGEM3Embedder 适配为 LlamaIndex BaseEmbedding 接口。

核心优势：共享同一个 BGEM3Embedder 实例，零额外内存开销。
BGE-M3 模型约 2.2GB，避免 LlamaIndex 和现有代码各自加载一份。
"""

from typing import Any, List

from llama_index.core.base.embeddings.base import BaseEmbedding


class BGEM3EmbeddingAdapter(BaseEmbedding):
    """适配 BGEM3Embedder → LlamaIndex BaseEmbedding。

    将现有的 BGEM3Embedder.embed() 方法桥接到 LlamaIndex 期望的
    _aget_query_embedding / _aget_text_embeddings 接口。
    """

    _embedder: Any  # BGEM3Embedder 实例

    def __init__(self, embedder: Any):
        super().__init__(
            model_name="BAAI/bge-m3",
            embed_batch_size=32,
        )
        self._embedder = embedder

    @classmethod
    def class_name(cls) -> str:
        return "BGEM3EmbeddingAdapter"

    async def _aget_query_embedding(self, query: str) -> List[float]:
        """获取单条查询的嵌入向量（1024 维）。"""
        embeddings = await self._embedder.embed([query])
        return embeddings[0]

    async def _aget_text_embeddings(self, texts: List[str]) -> List[List[float]]:
        """批量获取文本的嵌入向量。"""
        return await self._embedder.embed(texts)

    def _get_query_embedding(self, query: str) -> List[float]:
        raise NotImplementedError(
            "BGEM3EmbeddingAdapter 仅支持异步调用，请使用 _aget_query_embedding"
        )

    def _get_text_embedding(self, text: str) -> List[float]:
        raise NotImplementedError(
            "BGEM3EmbeddingAdapter 仅支持异步调用，请使用 _aget_text_embeddings"
        )

    def _get_text_embeddings(self, texts: List[str]) -> List[List[float]]:
        raise NotImplementedError(
            "BGEM3EmbeddingAdapter 仅支持异步调用，请使用 _aget_text_embeddings"
        )
