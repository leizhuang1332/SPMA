"""自定义 LlamaIndex Retriever——将现有 ES BM25 客户端适配为 BaseRetriever 接口。

包含:
- RRFConfig: RRF 融合参数配置
- ESBM25Retriever: ESClient → BaseRetriever 适配
- HybridRRFRetriever: 并行 BM25 + 向量检索 + 加权 RRF 融合
"""

import asyncio
from dataclasses import dataclass
from typing import Any, List

from llama_index.core import QueryBundle
from llama_index.core.retrievers import BaseRetriever
from llama_index.core.schema import NodeWithScore, TextNode


@dataclass
class RRFConfig:
    """RRF 融合配置。

    Attributes:
        k: RRF 常数，默认 60（工业界标准值）
        top_k: 最终返回数量
        bm25_weight: BM25 侧权重
        vector_weight: 向量侧权重
    """
    k: int = 60
    top_k: int = 15
    bm25_weight: float = 0.5
    vector_weight: float = 0.5


class ESBM25Retriever(BaseRetriever):
    """将 ESClient BM25 搜索适配为 LlamaIndex BaseRetriever 接口。

    设计决策：
    - 不依赖 LlamaIndex 的 ES 集成（避免额外依赖冲突）
    - 直接包装现有 ESClient 实例，零破坏
    - _filters 支持构造时预设 + set_filters() 运行时覆盖
    """

    def __init__(
        self,
        es_client: Any,  # ESClient 实例
        top_k: int = 20,
        filters: dict | None = None,
    ):
        super().__init__()
        self._es_client = es_client
        self._top_k = top_k
        self._filters = filters

    def set_filters(self, filters: dict | None) -> None:
        """运行时更新过滤条件——用于 precise 模式的 req_ids 过滤。"""
        self._filters = filters

    async def _aretrieve(self, query_bundle: QueryBundle) -> List[NodeWithScore]:
        """异步 BM25 检索——直接委托给 ESClient.search。

        从 QueryBundle 提取 query_str，调用现有 ESClient.search，
        将返回的 dict 列表转换为 LlamaIndex NodeWithScore 列表。
        """
        query_str = query_bundle.query_str or ""

        raw_results = await self._es_client.search(
            query=query_str,
            top_k=self._top_k,
            filters=self._filters,
        )

        nodes = []
        for r in raw_results:
            node = TextNode(
                id_=r.get("chunk_id", ""),
                text=r.get("content", ""),
                metadata={
                    "source_id": r.get("source_id"),
                    "source_type": r.get("source_type", "bm25"),
                    "req_ids": r.get("req_ids", []),
                    "retrieval_source": "bm25",
                    **(r.get("metadata") or {}),
                },
            )
            nodes.append(NodeWithScore(node=node, score=r.get("score", 0.0)))

        return nodes

    def _retrieve(self, query_bundle: QueryBundle) -> List[NodeWithScore]:
        raise NotImplementedError(
            "ESBM25Retriever 仅支持异步检索，请使用 aretrieve()"
        )


from llama_index.core.retrievers import VectorIndexRetriever


class HybridRRFRetriever(BaseRetriever):
    """混合检索器——并行 BM25 + 向量检索，加权 RRF 融合。

    核心流程：
    1. 并行调用 ESBM25Retriever 和 VectorIndexRetriever
    2. 对两路结果按排名执行加权 RRF 融合
    3. 用 RRF 分数替换原始检索分数
    4. 返回融合排序后的 top_k 节点

    与现有 graph.py search_node 的融合逻辑等价，但封装为独立的可测试单元。
    """

    def __init__(
        self,
        vector_retriever: VectorIndexRetriever,
        bm25_retriever: "ESBM25Retriever",
        config: RRFConfig | None = None,
    ):
        super().__init__()
        self._vector_retriever = vector_retriever
        self._bm25_retriever = bm25_retriever
        self._config = config or RRFConfig()

    async def _aretrieve(self, query_bundle: QueryBundle) -> List[NodeWithScore]:
        cfg = self._config

        # Step 1: 并行执行 BM25 和向量检索
        bm25_future = self._bm25_retriever.aretrieve(query_bundle)
        vector_future = self._vector_retriever.aretrieve(query_bundle)
        bm25_nodes, vector_nodes = await asyncio.gather(bm25_future, vector_future)

        # Step 2: 加权 RRF 融合
        rrf_scores: dict[str, float] = {}
        best_node: dict[str, NodeWithScore] = {}

        for rank, node_with_score in enumerate(bm25_nodes):
            node_id = node_with_score.node.node_id
            rrf_scores[node_id] = rrf_scores.get(node_id, 0.0) + \
                cfg.bm25_weight / (cfg.k + rank)
            if node_id not in best_node:
                best_node[node_id] = node_with_score

        for rank, node_with_score in enumerate(vector_nodes):
            node_id = node_with_score.node.node_id
            rrf_scores[node_id] = rrf_scores.get(node_id, 0.0) + \
                cfg.vector_weight / (cfg.k + rank)
            if node_id not in best_node:
                best_node[node_id] = node_with_score

        # Step 3: 按 RRF 分数降序排列
        sorted_ids = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
        fused = []
        for node_id, rrf_score in sorted_ids[:cfg.top_k]:
            node = best_node[node_id]
            node.score = rrf_score
            node.node.metadata["rrf_score"] = rrf_score
            fused.append(node)

        return fused

    def _retrieve(self, query_bundle: QueryBundle) -> List[NodeWithScore]:
        raise NotImplementedError("仅支持异步检索，请使用 aretrieve()")
