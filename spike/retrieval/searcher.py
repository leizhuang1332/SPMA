"""BM25 + embedding 混合检索。"""

import numpy as np
from sentence_transformers import SentenceTransformer

from spike.retrieval.corpus_loader import Document
from spike.retrieval.indexer import HybridIndexer


class HybridSearcher:
    """执行混合检索：加权融合 BM25 和 embedding 相似度分数。"""

    def __init__(
        self,
        indexer: HybridIndexer,
        embedding_model_name: str = "all-MiniLM-L6-v2",
        bm25_weight: float = 0.3,
        embedding_weight: float = 0.7,
    ) -> None:
        self.indexer = indexer
        self.embedding_model = SentenceTransformer(embedding_model_name)
        self.bm25_weight = bm25_weight
        self.embedding_weight = embedding_weight

    def search(self, query: str, top_k: int = 5) -> list[tuple[Document, float]]:
        """混合检索。

        Args:
            query: 查询文本
            top_k: 返回结果数量

        Returns:
            [(document, score), ...] 按分数降序排列
        """
        if self.indexer.bm25 is None or self.indexer.embeddings is None:
            raise RuntimeError("索引未构建，请先调用 indexer.index()")

        # BM25 分数
        tokenized_query = query.split()
        bm25_scores = np.array(self.indexer.bm25.get_scores(tokenized_query))

        # BM25 分数归一化
        bm25_max = bm25_scores.max()
        if bm25_max > 0:
            bm25_scores = bm25_scores / bm25_max

        # Embedding 相似度
        query_embedding = self.embedding_model.encode([query], convert_to_numpy=True)
        cosine_scores = np.dot(self.indexer.embeddings, query_embedding.T).flatten()

        # 混合分数
        combined_scores = (
            self.bm25_weight * bm25_scores + self.embedding_weight * cosine_scores
        )

        # 取 top_k
        top_indices = np.argsort(combined_scores)[::-1][:top_k]

        results = []
        for idx in top_indices:
            doc = self.indexer.documents[idx]
            score = float(combined_scores[idx])
            results.append((doc, score))

        return results
