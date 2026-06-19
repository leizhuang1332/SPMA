"""深度集成 LlamaIndex 的文档检索管道。

设计原则：
1. 一个管道实例对应一个 PGVector 后端的 VectorStoreIndex
2. 检索模式通过 search() 的 mode 参数动态切换（不重新初始化）
3. ESClient 通过 ESBM25Retriever 适配注入
4. 保持与现有 graph.py 的接口兼容（输入 query + entities，输出 list[dict]）
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

from llama_index.core import (
    VectorStoreIndex,
    Settings,
    QueryBundle,
)
from llama_index.core.retrievers import VectorIndexRetriever

from spma.agents.doc.llamaindex_retrievers import (
    ESBM25Retriever,
    HybridRRFRetriever,
    RRFConfig,
)



@dataclass
class PipelineConfig:
    """管道配置——集中管理所有可调参数。"""

    dsn: str = "postgresql+asyncpg://spma:spma123@localhost:5433/spma"

    vector_top_k: int = 20
    bm25_top_k: int = 20
    hybrid_final_top_k: int = 15

    rrf_k: int = 60
    rrf_bm25_weight: float = 0.5
    rrf_vector_weight: float = 0.5

    mode_weights: dict = field(default_factory=lambda: {
        "precise":  {"bm25": 0.7, "vector": 0.3},
        "hybrid":   {"bm25": 0.5, "vector": 0.5},
        "semantic": {"bm25": 0.3, "vector": 0.7},
    })

    rerank_top_n: int = 10
    enable_rerank: bool = True

    hyde_max_query_len: int = 30
    hyde_top_k: int = 10



class AdvancedLlamaIndexPipeline:
    """方案三核心管道——完整封装 LlamaIndex 检索能力。

    使用方式：
        pipeline = AdvancedLlamaIndexPipeline(es_client, config)
        pipeline.initialize(embedder=embedder, hyde_llm=llm)
        results = await pipeline.search(query="xxx", mode="hybrid", entities={})
    """

    def __init__(
        self,
        es_client: Any,
        config: PipelineConfig | None = None,
    ):
        self._es_client = es_client
        self._config = config or PipelineConfig()
        self._index: VectorStoreIndex | None = None
        self._embedder = None
        self._hyde_llm = None

    def initialize(self, embedder, hyde_llm=None, reranker=None) -> None:
        """延迟初始化——在 graph.py 中调用。"""
        from llama_index.vector_stores.postgres import PGVectorStore as LlamaPGVectorStore
        from spma.agents.doc.llamaindex_embedding import BGEM3EmbeddingAdapter

        self._embedder = embedder
        Settings.embed_model = BGEM3EmbeddingAdapter(embedder)

        # 确保 DSN 使用正确的驱动：同步引擎用 psycopg2，异步引擎用 asyncpg
        dsn = self._config.dsn
        # 同步 connection_string 始终去掉 +asyncpg（如有）
        sync_dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")
        sync_dsn = sync_dsn.replace("postgres+asyncpg://", "postgres://")
        # 异步 async_connection_string 始终添加 +asyncpg（如无）
        if "+asyncpg" not in dsn:
            async_dsn = dsn.replace("postgresql://", "postgresql+asyncpg://")
            async_dsn = async_dsn.replace("postgres://", "postgresql+asyncpg://")
        else:
            async_dsn = dsn

        vector_store = LlamaPGVectorStore(
            connection_string=sync_dsn,
            async_connection_string=async_dsn,
            table_name="chunk_embeddings",
            schema_name="public",
            embed_dim=1024,
            hybrid_search=True,
            text_search_config="english",
            cache_ok=False,
            perform_setup=True,
            debug=False,
            use_jsonb=True,
            hnsw_kwargs=None,
        )
        self._index = VectorStoreIndex.from_vector_store(vector_store)
        self._hyde_llm = hyde_llm

        # Reranker 初始化（降级安全）
        self._reranker = None
        self._long_context_reorder = None
        if self._config.enable_rerank:
            try:
                if reranker is not None:
                    self._reranker = reranker
                else:
                    from spma.retrieval.reranker import BGEReranker
                    self._reranker = BGEReranker()
                from llama_index.core.postprocessor import LongContextReorder
                self._long_context_reorder = LongContextReorder()
            except Exception:
                logger.exception("Reranker 模型加载失败")
                logger.warning(
                    "Reranker 初始化失败，降级为无精排模式，检索结果将不做 Cross-Encoder 重排序"
                )
                self._config.enable_rerank = False
                self._reranker = None
                self._long_context_reorder = None

    async def search(
        self,
        query: str,
        mode: str = "hybrid",
        entities: dict | None = None,
        hyde_llm=None,
    ) -> List[dict]:
        """统一的检索入口——完全替代 search_node 中的检索逻辑。"""
        cfg = self._config
        entities = entities or {}

        query_embedding = await self._embedder.embed([query])
        query_bundle = QueryBundle(query_str=query, embedding=query_embedding[0])

        retriever = self._build_retriever(mode, entities)
        nodes = await retriever.aretrieve(query_bundle)

        # 后处理：复用已初始化的 postprocessor 实例
        if self._config.enable_rerank and mode != "precise" and self._reranker is not None:
            try:
                nodes = await self._reranker.rerank(
                    nodes=nodes,
                    query_bundle=query_bundle,
                    top_n=self._config.rerank_top_n,
                )
                if self._long_context_reorder is not None:
                    nodes = self._long_context_reorder.postprocess_nodes(nodes, query_bundle)
            except Exception:
                logger.exception("Reranker 调用失败")
                logger.warning("Reranker 调用失败，本次检索跳过精排，返回原始排序结果")

        hyde_nodes = []
        if self._should_use_hyde(query, entities) and (hyde_llm or self._hyde_llm):
            hyde_nodes = await self._hyde_search(query, hyde_llm or self._hyde_llm)

        seen_ids = set()
        all_results = []
        for n in list(nodes) + hyde_nodes:
            if n.node.node_id not in seen_ids:
                seen_ids.add(n.node.node_id)
                all_results.append(n)

        return [
            {
                "chunk_id": n.node.node_id,
                "source_id": n.node.metadata.get("source_id"),
                "source_type": n.node.metadata.get("source_type", mode),
                "content": n.node.get_content(),
                "score": float(n.score),
                "metadata": n.node.metadata or {},
            }
            for n in all_results[:20]
        ]

    def _build_retriever(self, mode: str, entities: dict) -> HybridRRFRetriever:
        """根据检索模式构建混合检索器。"""
        cfg = self._config
        mode_weights = cfg.mode_weights.get(mode, {"bm25": 0.5, "vector": 0.5})

        vector_retriever = VectorIndexRetriever(
            index=self._index,
            similarity_top_k=cfg.vector_top_k,
            vector_store_query_mode="default",
        )

        bm25_filters = None
        if mode == "precise" and entities.get("req_ids"):
            bm25_filters = {"req_ids": entities["req_ids"]}

        bm25_retriever = ESBM25Retriever(
            es_client=self._es_client, top_k=cfg.bm25_top_k, filters=bm25_filters,
        )

        return HybridRRFRetriever(
            vector_retriever=vector_retriever,
            bm25_retriever=bm25_retriever,
            config=RRFConfig(
                k=cfg.rrf_k,
                top_k=cfg.hybrid_final_top_k,
                bm25_weight=mode_weights.get("bm25", 0.5),
                vector_weight=mode_weights.get("vector", 0.5),
            ),
        )

    def _should_use_hyde(self, query: str, entities: dict) -> bool:
        return (
            len(query) <= self._config.hyde_max_query_len
            and not entities.get("req_ids")
        )

    async def _hyde_search(self, query: str, hyde_llm) -> List[Any]:
        try:
            hyde_obj = await hyde_llm.ainvoke(query)
            hyde_text = hyde_obj.content

            hyde_emb = await self._embedder.embed([hyde_text])
            query_bundle = QueryBundle(query_str=hyde_text, embedding=hyde_emb[0])

            vector_retriever = VectorIndexRetriever(
                index=self._index, similarity_top_k=self._config.hyde_top_k,
            )
            return await vector_retriever.aretrieve(query_bundle)
        except Exception:
            logger.exception("HyDE 检索失败，返回空结果")
            return []
