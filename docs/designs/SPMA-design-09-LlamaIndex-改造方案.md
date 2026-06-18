# SPMA Design 09: LlamaIndex 深度集成方案（方案三）

## 1. 背景与目标

### 1.1 当前架构

SPMA 的向量检索模块基于以下组件构建：

| 组件 | 当前实现 | 职责 |
|------|---------|------|
| **向量存储** | `PGVectorStore` (asyncpg + pgvector) | HNSW 索引、cosine 距离搜索 |
| **BM25 检索** | `ESClient` (AsyncElasticsearch) | 关键词全文检索 |
| **嵌入模型** | `BGEM3Embedder` (sentence-transformers) | BAAI/bge-m3 本地推理，1024 维 |
| **融合算法** | `weighted_fusion` (加权 RRF, k=60) | BM25 + 向量结果融合 |
| **图编排** | LangGraph StateGraph | 多轮检索、扩展、完备度评估 |
| **HyDE** | 内联在 search_node | LLM 生成假设文档 + 额外向量检索 |

### 1.2 改造目标

选择**方案三（深度集成）**——使用 LlamaIndex 的完整能力重构检索管道，获得以下优势：

- **可测试性**：每个检索组件（Retriever、Postprocessor、RRF Fusion）可独立单元测试
- **可组合性**：通过 LlamaIndex 的 Retriever/Postprocessor 抽象，正交组合不同策略
- **生态复用**：直接使用 SentenceTransformerRerank、LongContextReorder 等成熟实现
- **扩展便利**：接入新检索技术（ColBERT、Multi-Vector）时只需实现对应接口

### 1.3 设计原则

1. **最小侵入 LangGraph**：graph.py 除 search_node 外其余节点保持不变
2. **共享嵌入模型**：复用现有 `BGEM3Embedder`，零额外内存开销
3. **适配而非替换**：`ESClient` 和 `PGVectorStore` 通过适配器接入，不修改原有实现
4. **保持接口兼容**：检索结果仍为 `list[dict]`，与现有 aggregate/assess/expand 节点无缝对接

---

## 2. LlamaIndex 检索管道架构

### 2.1 管道分层

LlamaIndex 的检索管道是严格分层的，每层有明确的接口契约：

```
QueryBundle (query_str + embedding)
        │
        ▼
┌──────────────────────────────────────────────┐
│              BaseRetriever                    │  ← 检索层
│  _retrieve(query_bundle) → List[NodeWithScore]│
│  └─ aretrieve() 异步版本                      │
└──────────────────┬───────────────────────────┘
                   │ List[NodeWithScore]
                   ▼
┌──────────────────────────────────────────────┐
│          BaseNodePostprocessor                │  ← 后处理层
│  postprocess_nodes(nodes, query_bundle)       │     过滤/重排/去重
│       → List[NodeWithScore]                  │
└──────────────────┬───────────────────────────┘
                   │ List[NodeWithScore]
                   ▼
┌──────────────────────────────────────────────┐
│            BaseSynthesizer                    │  ← 合成层（检索场景不使用）
│  synthesize(query, nodes) → Response          │
└──────────────────────────────────────────────┘
```

**RetrieverQueryEngine** 是管道编排器，按顺序调用 Retriever → Postprocessors → Synthesizer。

### 2.2 RetrieverQueryEngine 真实行为

```python
# llama_index/core/query_engine/retriever_query_engine.py（简化）
class RetrieverQueryEngine(BaseQueryEngine):
    def __init__(
        self,
        retriever: BaseRetriever,
        response_synthesizer: BaseSynthesizer,
        node_postprocessors: list[BaseNodePostprocessor] | None = None,
    ):
        self._retriever = retriever
        self._response_synthesizer = response_synthesizer
        self._node_postprocessors = node_postprocessors or []

    async def aquery(self, query_bundle_or_str):
        # Step 1: 字符串 → QueryBundle
        if isinstance(query_bundle_or_str, str):
            query_bundle = QueryBundle(query_str=query_bundle_or_str)
        else:
            query_bundle = query_bundle_or_str

        # Step 2: 检索
        nodes = await self._retriever.aretrieve(query_bundle)

        # Step 3: 依次执行所有 postprocessor
        for postprocessor in self._node_postprocessors:
            nodes = postprocessor.postprocess_nodes(nodes, query_bundle)

        # Step 4: 合成响应
        response = await self._response_synthesizer.asynthesize(
            query_bundle.query_str, nodes
        )
        return response
```

**关键点**：`response.source_nodes` 包含所有经过后处理的节点。SPMA 的检索场景不需要 LLM 合成回答，因此使用 `response_mode="no_text"` 来跳过 LLM 调用，直接从 `response.source_nodes` 提取结果。

---

## 3. 总体架构设计

### 3.1 组件全景图

```
                        query.py（依赖注入）
                              │
              ┌───────────────┼───────────────┐
              │               │               │
              ▼               ▼               ▼
        ESClient       BGEM3Embedder      HyDE LLM
              │               │               │
              │     ┌─────────┴─────────┐     │
              │     │ BGEM3EmbeddingAdapter │  │
              │     │  (LlamaIndex 适配)    │  │
              │     └─────────┬─────────┘     │
              │               │               │
              ▼               ▼               ▼
   ┌──────────────────────────────────────────────────┐
   │           AdvancedLlamaIndexPipeline             │
   │                                                   │
   │  ┌─────────────────────────────────────────────┐ │
   │  │          HybridRRFRetriever                  │ │
   │  │  ┌───────────────────┐ ┌──────────────────┐ │ │
   │  │  │ ESBM25Retriever   │ │ VectorIndexRetriever│ │
   │  │  │ (ESClient 适配)   │ │ (PGVector 后端)   │ │ │
   │  │  └───────────────────┘ └──────────────────┘ │ │
   │  │         ↓ RRF k=60, weights 可配             │ │
   │  │         15 nodes                             │ │
   │  └──────────────────────┬──────────────────────┘ │
   │                         ▼                         │
   │  ┌─────────────────────────────────────────────┐ │
   │  │  SentenceTransformerRerank (BGE-Reranker)    │ │
   │  │  top_n=10, Cross-Encoder 精排                │ │
   │  └──────────────────────┬──────────────────────┘ │
   │                         ▼                         │
   │  ┌─────────────────────────────────────────────┐ │
   │  │  LongContextReorder                          │ │
   │  └──────────────────────┬──────────────────────┘ │
   │                         ▼                         │
   │  ┌─────────────────────────────────────────────┐ │
   │  │  + HyDE 扩展结果 (条件触发, 额外 top_k=10)     │ │
   │  └──────────────────────┬──────────────────────┘ │
   │                         ▼                         │
   │              最终结果: List[dict]                  │
   └──────────────────────────┬───────────────────────┘
                              │
                              ▼
                    graph.py search_node
                    (15 行委托调用)
                              │
                              ▼
                 aggregate → assess → expand (不变)
```

### 3.2 新增/修改文件清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/spma/agents/doc/llamaindex_pipeline.py` | **新增** | 核心管道，封装检索+后处理+HyDE |
| `src/spma/agents/doc/llamaindex_retrievers.py` | **新增** | 自定义 Retriever（ESBM25Retriever、HybridRRFRetriever） |
| `src/spma/agents/doc/llamaindex_embedding.py` | **新增** | BGEM3Embedder → LlamaIndex BaseEmbedding 适配 |
| `src/spma/agents/doc/graph.py` | **修改** | search_node 简化为管道委托（约 15 行替换） |
| `src/spma/api/routes/query.py` | **修改** | 初始化管道并注入依赖（约 5 行新增） |
| `pyproject.toml` | **修改** | 新增 llama-index 相关依赖 |

---

## 4. Layer 1：自定义 Retriever 实现

### 4.1 ESBM25Retriever —— ES 客户端适配

**设计问题**：LlamaIndex 的 `BaseRetriever` 接口要求接收 `QueryBundle` 并返回 `List[NodeWithScore]`，而现有 `ESClient.search()` 接收字符串 query 和 dict filters。需要适配层。

```python
# src/spma/agents/doc/llamaindex_retrievers.py

"""自定义 LlamaIndex Retriever——将现有 ES BM25 客户端适配为 BaseRetriever 接口。"""

from typing import Any, List

from llama_index.core import QueryBundle
from llama_index.core.retrievers import BaseRetriever
from llama_index.core.schema import NodeWithScore, TextNode


class ESBM25Retriever(BaseRetriever):
    """将 ESClient BM25 搜索适配为 LlamaIndex BaseRetriever 接口。

    设计决策：
    - 不依赖 LlamaIndex 的 ES 集成（避免额外依赖冲突）
    - 直接包装现有 ESClient 实例，零破坏
    - _filters 支持构造时预设 + 运行时覆盖
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
        """异步 BM25 检索——直接委托给 ESClient.search。"""
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
                    "retrieval_source": "bm25",  # 标记来源，便于区分和日志
                    **(r.get("metadata") or {}),
                },
            )
            nodes.append(NodeWithScore(node=node, score=r.get("score", 0.0)))

        return nodes

    def _retrieve(self, query_bundle: QueryBundle) -> List[NodeWithScore]:
        raise NotImplementedError(
            "ESBM25Retriever 仅支持异步检索，请使用 aretrieve()"
        )
```

### 4.2 HybridRRFRetriever —— 混合检索 + RRF 融合

**设计问题**：LlamaIndex 的 `QueryFusionRetriever` 是对同一条 query 的多个改写版本做融合，不适合 BM25+向量这种"同一 query，不同检索方式"的场景。需要自定义 Retriever 在内部编排并行检索 + RRF 融合。

```python
# 接上文 src/spma/agents/doc/llamaindex_retrievers.py

import asyncio
from dataclasses import dataclass

from llama_index.core.retrievers import VectorIndexRetriever


@dataclass
class RRFConfig:
    """RRF 融合配置。"""
    k: int = 60                # RRF 常数（工业界标准值）
    top_k: int = 15            # 最终返回数量
    bm25_weight: float = 0.5   # BM25 侧权重
    vector_weight: float = 0.5 # 向量侧权重


class HybridRRFRetriever(BaseRetriever):
    """混合检索器——并行 BM25 + 向量检索，加权 RRF 融合。

    核心流程：
    1. 并行调用 ESBM25Retriever 和 VectorIndexRetriever
    2. 对两路结果执行加权 RRF 融合
    3. 用 RRF 分数替换原始检索分数
    4. 返回融合排序后的 top_k 节点

    与现有 search_node 的融合逻辑等价，但封装为独立的可测试单元。
    """

    def __init__(
        self,
        vector_retriever: VectorIndexRetriever,
        bm25_retriever: ESBM25Retriever,
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

        # BM25 侧——按排名施加权重
        for rank, node_with_score in enumerate(bm25_nodes):
            node_id = node_with_score.node.node_id
            rrf_scores[node_id] = rrf_scores.get(node_id, 0.0) + \
                cfg.bm25_weight / (cfg.k + rank)
            if node_id not in best_node:
                best_node[node_id] = node_with_score

        # 向量侧——按排名施加权重
        for rank, node_with_score in enumerate(vector_nodes):
            node_id = node_with_score.node.node_id
            rrf_scores[node_id] = rrf_scores.get(node_id, 0.0) + \
                cfg.vector_weight / (cfg.k + rank)
            if node_id not in best_node:
                best_node[node_id] = node_with_score

        # Step 3: 按 RRF 分数排序，标记融合来源
        sorted_ids = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
        fused = []
        for node_id, rrf_score in sorted_ids[:cfg.top_k]:
            node = best_node[node_id]
            node.score = rrf_score  # 用 RRF 分数替换原始分数
            node.node.metadata["rrf_score"] = rrf_score
            fused.append(node)

        return fused

    def _retrieve(self, query_bundle: QueryBundle) -> List[NodeWithScore]:
        raise NotImplementedError("仅支持异步检索，请使用 aretrieve()")
```

### 4.3 与现有融合逻辑的等价性

| 维度 | 现有 search_node | HybridRRFRetriever |
|------|-----------------|-------------------|
| 融合算法 | `weighted_fusion()` | 内联实现，逻辑等价 |
| RRF 常数 k | 60（可配） | 60（可配） |
| 权重配置 | `wc.get("weights", {})` | `RRFConfig` 构造参数 |
| 并行检索 | `asyncio.gather` 在 graph.py | `asyncio.gather` 在 Retriever 内部 |
| 分数字段 | `rrf_score` 额外字段 | `node.score` 直接替换 |
| 可测试性 | 只能端到端测试 | 可独立单元测试 |
| 可组合性 | 无法组合 | 可被 RetrieverQueryEngine/FusionRetriever 组合 |

---

## 5. Layer 2：NodePostprocessor 链

### 5.1 组件选择

| Postprocessor | 用途 | 适用模式 | 性能影响 |
|--------------|------|---------|---------|
| `SentenceTransformerRerank` | Cross-Encoder 精排，对每对 (query, doc) 打分 | hybrid, semantic | +200-500ms (CPU) |
| `LongContextReorder` | 长上下文重排序，防止重要信息被截断 | hybrid, semantic | 极小 |
| （不使用 SimilarityPostprocessor） | RRF 融合后的分数不再是 0-1 余弦相似度 | — | — |

### 5.2 为什么不使用 SimilarityPostprocessor

RRF 融合后的分数不再落在 0-1 区间。在 k=60 且两路等权的情况下，最高 RRF 分数约为 `1/60 + 1/60 ≈ 0.033`——远低于 `similarity_cutoff` 的典型值 0.5。因此相似度过滤应在向量检索阶段通过 `similarity_top_k` 完成，而非在 Postprocessor 中。

### 5.3 Postprocessor 链构建

```python
# src/spma/agents/doc/llamaindex_pipeline.py（postprocessor 部分）

from typing import List

from llama_index.core import QueryBundle
from llama_index.core.postprocessor import (
    BaseNodePostprocessor,
    SentenceTransformerRerank,
    LongContextReorder,
)
from llama_index.core.schema import NodeWithScore


def build_postprocessor_chain(
    mode: str = "hybrid",
    rerank_model: str = "BAAI/bge-reranker-v2-m3",
    rerank_top_n: int = 10,
) -> list[BaseNodePostprocessor]:
    """根据检索模式构建后处理链。

    Args:
        mode: "precise" | "hybrid" | "semantic"
        rerank_model: 重排序模型名称
        rerank_top_n: 重排序后保留的节点数

    Returns:
        按顺序执行的 NodePostprocessor 列表
    """
    if mode == "precise":
        # precise 模式跳过语义重排，保持精确匹配的排序
        return []

    chain: list[BaseNodePostprocessor] = [
        SentenceTransformerRerank(
            model=rerank_model,
            top_n=rerank_top_n,
        ),
        LongContextReorder(),
    ]
    return chain
```

### 5.4 SentenceTransformerRerank 工作原理

```python
# 实际源码行为（简化）
class SentenceTransformerRerank(BaseNodePostprocessor):
    """用 Cross-Encoder 对每对 (query, document) 重新打分。

    流程：
    1. 构造 (query, node_text) 对列表
    2. Cross-Encoder 批量推理打分
    3. 用新分数替换原始检索分数
    4. 按新分数降序排列，取 top_n
    """

    def __init__(self, model: str, top_n: int = 5):
        self.model = model        # 如 "BAAI/bge-reranker-v2-m3"
        self.top_n = top_n

    def _postprocess_nodes(self, nodes, query_bundle):
        if not nodes:
            return []

        pairs = [
            (query_bundle.query_str, node.node.get_content())
            for node in nodes
        ]
        scores = self._cross_encoder.predict(pairs)

        for node, score in zip(nodes, scores):
            node.score = float(score)

        nodes = sorted(nodes, key=lambda x: x.score or 0.0, reverse=True)
        return nodes[:self.top_n]
```

**性能考量**：Cross-Encoder 的时间复杂度 O(N)，对 20 个候选节点 BGE-Reranker v2 M3 的 CPU 推理延迟约 200-500ms。在 precise 模式下跳过此步骤以降低延迟。

---

## 6. Layer 3：嵌入模型共享

### 6.1 问题

LlamaIndex 需要自己的 `Settings.embed_model`（类型为 `BaseEmbedding`），而现有架构使用独立的 `BGEM3Embedder`。如果两边都加载 BGE-M3 模型，会浪费一倍内存（BGE-M3 约 2.2GB）。

### 6.2 解决方案：BGEM3EmbeddingAdapter

```python
# src/spma/agents/doc/llamaindex_embedding.py

"""将现有 BGEM3Embedder 适配为 LlamaIndex BaseEmbedding 接口。"""

from typing import Any, List

from llama_index.core.base.embeddings.base import BaseEmbedding


class BGEM3EmbeddingAdapter(BaseEmbedding):
    """适配 BGEM3Embedder → LlamaIndex BaseEmbedding。

    核心优势：共享同一个 BGEM3Embedder 实例，零额外内存开销。
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
        embeddings = await self._embedder.embed([query])
        return embeddings[0]

    async def _aget_text_embeddings(self, texts: List[str]) -> List[List[float]]:
        return await self._embedder.embed(texts)

    def _get_query_embedding(self, query: str) -> List[float]:
        raise NotImplementedError("同步调用不受支持，请使用异步版本")

    def _get_text_embeddings(self, texts: List[str]) -> List[List[float]]:
        raise NotImplementedError("同步调用不受支持，请使用异步版本")
```

---

## 7. 核心管道：AdvancedLlamaIndexPipeline

### 7.1 设计概述

`AdvancedLlamaIndexPipeline` 是方案三的入口类，封装了：

- 检索模式路由（precise / hybrid / semantic）
- Retriever 构建（根据模式配置权重和过滤器）
- Postprocessor 链构建
- HyDE 扩展搜索（条件触发）
- 结果合并与去重
- 与现有 `graph.py` 的接口兼容

### 7.2 完整实现

```python
# src/spma/agents/doc/llamaindex_pipeline.py

"""深度集成 LlamaIndex 的文档检索管道。

设计原则：
1. 一个管道实例对应一个 PGVector 后端的 VectorStoreIndex
2. 检索模式通过 search() 的 mode 参数动态切换（不重新初始化）
3. ESClient 通过 ESBM25Retriever 适配注入
4. 保持与现有 graph.py 的接口兼容（输入 query + entities，输出 list[dict]）
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from llama_index.core import (
    VectorStoreIndex,
    Settings,
    QueryBundle,
)
from llama_index.core.retrievers import VectorIndexRetriever
from llama_index.core.postprocessor import BaseNodePostprocessor

from spma.agents.doc.llamaindex_retrievers import (
    ESBM25Retriever,
    HybridRRFRetriever,
    RRFConfig,
)


@dataclass
class PipelineConfig:
    """管道配置——集中管理所有可调参数。"""

    # 数据库
    dsn: str = "postgresql://spma:spma123@localhost:5433/spma"

    # 检索参数
    vector_top_k: int = 20
    bm25_top_k: int = 20
    hybrid_final_top_k: int = 15

    # RRF 参数
    rrf_k: int = 60
    rrf_bm25_weight: float = 0.5
    rrf_vector_weight: float = 0.5

    # 模式权重覆盖
    mode_weights: dict = field(default_factory=lambda: {
        "precise":  {"bm25": 0.7, "vector": 0.3},
        "hybrid":   {"bm25": 0.5, "vector": 0.5},
        "semantic": {"bm25": 0.3, "vector": 0.7},
    })

    # Postprocessor
    rerank_model: str = "BAAI/bge-reranker-v2-m3"
    rerank_top_n: int = 10
    enable_rerank: bool = True

    # HyDE
    hyde_max_query_len: int = 30  # 短查询触发 HyDE
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
        self._embedder = None    # BGEM3Embedder 引用
        self._hyde_llm = None    # HyDE LLM 引用

    def initialize(self, embedder, hyde_llm=None) -> None:
        """延迟初始化——支持依赖注入，在 query.py 中调用。

        设计决策：不在 __init__ 中自动初始化，因为 embedder 的加载是异步的
        （需要从 ModelScope 下载模型），应由调用方控制时机。
        """
        from llama_index.vector_stores.postgres import PGVectorStore as LlamaPGVectorStore
        from spma.agents.doc.llamaindex_embedding import BGEM3EmbeddingAdapter

        cfg = self._config

        # 复用现有 BGEM3Embedder，避免重复加载模型
        self._embedder = embedder
        Settings.embed_model = BGEM3EmbeddingAdapter(embedder)

        # 创建 PGVector 后端 + VectorStoreIndex
        vector_store = LlamaPGVectorStore.from_uri(cfg.dsn)
        self._index = VectorStoreIndex.from_vector_store(vector_store)

        # 保存 HyDE LLM 引用
        self._hyde_llm = hyde_llm

    async def search(
        self,
        query: str,
        mode: str = "hybrid",
        entities: dict | None = None,
        hyde_llm=None,
    ) -> List[dict]:
        """统一的检索入口——完全替代 search_node 中的检索逻辑。

        Args:
            query: 查询文本
            mode: "precise" | "hybrid" | "semantic"
            entities: 实体信息（req_ids 用于过滤）
            hyde_llm: HyDE 的 LLM（可选，覆盖初始化时的设置）

        Returns:
            检索结果列表 [{chunk_id, source_id, source_type, content, score, metadata}, ...]
        """
        cfg = self._config
        entities = entities or {}

        # Step 1: 构建 QueryBundle（含 embedding）
        query_embedding = await self._embedder.embed([query])
        query_bundle = QueryBundle(
            query_str=query,
            embedding=query_embedding[0],
        )

        # Step 2: 根据模式构建检索器
        retriever = self._build_retriever(mode, entities)

        # Step 3: 执行检索
        nodes = await retriever.aretrieve(query_bundle)

        # Step 4: 后处理
        postprocessors = build_postprocessor_chain(
            mode=mode,
            rerank_model=cfg.rerank_model,
            rerank_top_n=cfg.rerank_top_n,
        ) if cfg.enable_rerank else []
        for pp in postprocessors:
            nodes = pp.postprocess_nodes(nodes, query_bundle)

        # Step 5: HyDE 扩展搜索（条件触发）
        hyde_nodes = []
        if self._should_use_hyde(query, entities) and (hyde_llm or self._hyde_llm):
            hyde_nodes = await self._hyde_search(query, hyde_llm or self._hyde_llm)

        # Step 6: 合并 HyDE 结果并去重
        seen_ids = set()
        all_results = []
        for n in list(nodes) + hyde_nodes:
            if n.node.node_id not in seen_ids:
                seen_ids.add(n.node.node_id)
                all_results.append(n)

        # Step 7: 转换为兼容格式
        return [
            {
                "chunk_id": n.node.node_id,
                "source_id": n.node.metadata.get("source_id"),
                "source_type": n.node.metadata.get("source_type", mode),
                "content": n.node.get_content(),
                "score": n.score,
                "metadata": n.node.metadata or {},
            }
            for n in all_results[:20]
        ]

    def _build_retriever(self, mode: str, entities: dict) -> HybridRRFRetriever:
        """根据检索模式构建混合检索器。

        三种模式的差异体现在：
        1. BM25 过滤器（precise 模式按 req_ids 过滤）
        2. RRF 权重（不同模式侧重不同）
        """
        cfg = self._config
        mode_weights = cfg.mode_weights.get(mode, {"bm25": 0.5, "vector": 0.5})

        # 构建向量检索器
        vector_retriever = VectorIndexRetriever(
            index=self._index,
            similarity_top_k=cfg.vector_top_k,
            vector_store_query_mode="default",
        )

        # 构建 BM25 检索器（带过滤条件）
        bm25_filters = None
        if mode == "precise" and entities.get("req_ids"):
            bm25_filters = {"req_ids": entities["req_ids"]}

        bm25_retriever = ESBM25Retriever(
            es_client=self._es_client,
            top_k=cfg.bm25_top_k,
            filters=bm25_filters,
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
        """判断是否启用 HyDE。

        条件（与现有实现一致）：
        1. query 长度 <= hyde_max_query_len（默认 30 字符）
        2. 没有精确的 req_ids 匹配需求
        """
        return (
            len(query) <= self._config.hyde_max_query_len
            and not entities.get("req_ids")
        )

    async def _hyde_search(
        self, query: str, hyde_llm
    ) -> List[Any]:
        """执行 HyDE 扩展搜索。

        保留现有的两阶段逻辑：
        1. LLM 生成假设文档
        2. 对假设文档 embedding → 向量检索
        """
        try:
            hyde_obj = await hyde_llm.ainvoke(query)
            hyde_text = hyde_obj.content

            hyde_emb = await self._embedder.embed([hyde_text])
            query_bundle = QueryBundle(
                query_str=hyde_text,
                embedding=hyde_emb[0],
            )

            vector_retriever = VectorIndexRetriever(
                index=self._index,
                similarity_top_k=self._config.hyde_top_k,
            )
            return await vector_retriever.aretrieve(query_bundle)
        except Exception:
            return []
```

---

## 8. LangGraph 集成

### 8.1 graph.py 改造（最小侵入）

```python
# src/spma/agents/doc/graph.py（改造后）

"""Doc Agent 的 LangGraph StateGraph 定义——方案三改造版。

节点: route → search → aggregate → assess ──→ expand → search
                                      └──→ END

改动范围：仅 search_node 替换为管道委托，其余节点完全不变。
"""

from typing import Literal

from langgraph.graph import StateGraph, END

from spma.agents.doc.state import DocAgentState
from spma.agents.doc.retriever import route_retrieval_mode
from spma.agents.doc.completeness import assess_completeness
from spma.agents.doc.clue_expander import rule_based_expand, llm_based_expand


def build_doc_agent_graph(
    es_client, vector_store, embedder, llm,
    hyde_llm=None, weights_config=None
):
    """构建 Doc Agent 的 LangGraph StateGraph——方案三深度集成 LlamaIndex。"""
    wc = weights_config or {}

    # ========== 方案三：初始化 LlamaIndex 管道 ==========
    from spma.agents.doc.llamaindex_pipeline import (
        AdvancedLlamaIndexPipeline,
        PipelineConfig,
    )

    pipeline_config = PipelineConfig(
        dsn=(
            vector_store._dsn
            if hasattr(vector_store, "_dsn")
            else "postgresql://spma:spma123@localhost:5433/spma"
        ),
        rrf_k=wc.get("rrf", {}).get("k", 60),
        rrf_bm25_weight=wc.get("weights", {})
        .get("hybrid", {})
        .get("bm25", 0.5),
        rrf_vector_weight=wc.get("weights", {})
        .get("hybrid", {})
        .get("vector", 0.5),
    )
    llama_pipeline = AdvancedLlamaIndexPipeline(
        es_client=es_client,
        config=pipeline_config,
    )
    llama_pipeline.initialize(embedder=embedder, hyde_llm=hyde_llm)

    # ========== 路由节点（不变）==========
    async def route_node(state: DocAgentState) -> dict:
        entities = state.get("entities", {})
        mode = route_retrieval_mode(entities)
        state["weight_mode"] = mode
        query = state.get("original_query", "")
        hyde_enabled = (
            len(query) <= 30
            and not entities.get("req_ids")
            and hyde_llm is not None
        )
        state["hyde_enabled"] = hyde_enabled
        return state

    # ========== 搜索节点（方案三改造：委托给管道）==========
    async def search_node(state: DocAgentState) -> dict:
        query = state.get("current_query", state.get("original_query", ""))
        entities = state.get("entities", {})
        mode = state.get("weight_mode", "hybrid")

        try:
            fused = await llama_pipeline.search(
                query=query,
                mode=mode,
                entities=entities,
                hyde_llm=hyde_llm if state.get("hyde_enabled") else None,
            )
        except Exception:
            fused = []

        # 保持与现有状态接口兼容
        for r in fused:
            r["source_type"] = r.get("source_type", mode)

        state["bm25_candidates"] = [
            r for r in fused
            if r.get("metadata", {}).get("retrieval_source") == "bm25"
        ][:20]
        state["vector_candidates"] = [
            r for r in fused
            if r.get("metadata", {}).get("retrieval_source") != "bm25"
        ][:20]
        state["fused_results"] = fused
        return state

    # ========== 以下节点完全不变 ==========

    async def aggregate_node(state: DocAgentState) -> dict:
        prev = state.get("accumulated_results", [])
        current = state.get("fused_results", [])
        seen_ids = {r["chunk_id"] for r in prev}
        for r in current:
            if r["chunk_id"] not in seen_ids:
                prev.append(r)
                seen_ids.add(r["chunk_id"])
        state["accumulated_results"] = prev
        return state

    async def assess_node(state: DocAgentState) -> dict:
        results = state.get("accumulated_results", [])
        entities = state.get("entities", {})
        thresholds = wc.get("thresholds", {})
        outcome = await assess_completeness(
            results=results,
            entities=entities,
            llm=llm,
            min_results=thresholds.get("min_results_converge", 5),
            vector_threshold=thresholds.get(
                "vector_similarity_converge", 0.85
            ),
        )
        state["assessment"] = outcome.verdict
        state["convergence_reason"] = f"{outcome.level}:{outcome.reason}"

        round_num = state.get("round", 1)
        max_rounds = state.get("max_rounds", 3)
        if outcome.verdict == "converge" or round_num >= max_rounds:
            state["rounds_used"] = round_num
            state["final_results"] = state.get("accumulated_results", [])

        return state

    async def expand_node(state: DocAgentState) -> dict:
        round_num = state.get("round", 1)
        original_query = state.get("original_query", "")
        results = state.get("accumulated_results", [])
        known_req_ids = set()
        for r in results:
            for rid in r.get("req_ids", []):
                known_req_ids.add(rid)
        if round_num <= 2:
            new_query = rule_based_expand(
                original_query, results, known_req_ids
            )
        else:
            new_query = await llm_based_expand(
                original_query, results, llm
            )
        state["current_query"] = new_query
        state["round"] = round_num + 1
        return state

    def should_continue(state: DocAgentState) -> Literal["expand", "END"]:
        if state.get("final_results") is not None:
            return "END"
        return "expand"

    # Graph 组装（不变）
    graph = StateGraph(DocAgentState)
    graph.add_node("route", route_node)
    graph.add_node("search", search_node)
    graph.add_node("aggregate", aggregate_node)
    graph.add_node("assess", assess_node)
    graph.add_node("expand", expand_node)
    graph.set_entry_point("route")
    graph.add_edge("route", "search")
    graph.add_edge("search", "aggregate")
    graph.add_edge("aggregate", "assess")
    graph.add_conditional_edges(
        "assess", should_continue, {"expand": "expand", "END": END}
    )
    graph.add_edge("expand", "search")
    return graph.compile()
```

### 8.2 改动量统计

| 改动项 | 改动量 | 性质 |
|--------|--------|------|
| search_node | ~45 行 → ~20 行 | 简化（内联逻辑 → 管道委托） |
| route_node | 0 行 | 不变 |
| aggregate_node | 0 行 | 不变 |
| assess_node | 0 行 | 不变 |
| expand_node | 0 行 | 不变 |
| should_continue | 0 行 | 不变 |
| Graph 组装 | 0 行 | 不变 |
| **新增文件** | 3 个 | pipeline + retrievers + embedding 适配 |

---

## 9. query.py 集成改动

```python
# src/spma/api/routes/query.py（改动点）

# === 原有初始化（第 93-109 行）===
# es_client = ESClient()
# vector_store = PGVectorStore()
# embedder = await BGEM3Embedder.create()

# === 方案三改造 ===
es_client = ESClient()
vector_store = PGVectorStore()
embedder = await BGEM3Embedder.create()

# vector_store 仅用于传递 dsn 给 pipeline
# （实际向量操作由 LlamaIndex 的 PGVectorStore 接管；
#  Settings.embed_model 由 pipeline.initialize() 内部设置）

# ...（其余逻辑不变）

# === 构建 Doc Agent Graph 时 ===
doc_graph = build_doc_agent_graph(
    es_client=es_client,
    vector_store=vector_store,
    embedder=embedder,
    llm=llm,
    hyde_llm=hyde_llm,
    weights_config=weights_config,
)
```

---

## 10. 依赖管理

### 10.1 pyproject.toml 新增依赖

```toml
[project]
dependencies = [
    # ... 现有依赖 ...

    # LlamaIndex 核心
    "llama-index-core>=0.12.0",
    "llama-index-vector-stores-postgres>=0.4.0",

    # 嵌入模型适配
    "llama-index-embeddings-langchain>=0.3.0",

    # 重排序
    "llama-index-postprocessor-sbert-rerank>=0.2.0",

    # HyDE 查询变换（可选）
    "llama-index-core[query-transformations]>=0.12.0",
]
```

### 10.2 依赖关系图

```
llama-index-core
├── llama-index-vector-stores-postgres  (PGVector 后端)
├── llama-index-embeddings-langchain    (LangchainEmbedding 桥接)
└── llama-index-postprocessor-sbert-rerank  (BGE-Reranker)
```

---

## 11. 性能分析

### 11.1 各阶段延迟预估

| 阶段 | 现有实现 | 方案三 | 变化 |
|------|---------|--------|------|
| BM25 检索 | 10-50ms (ES) | 10-50ms (ESBM25Retriever) | 无变化 |
| 向量检索 | 5-30ms (PGVector) | 5-50ms (VectorIndexRetriever) | +0-20ms (封装开销) |
| RRF 融合 | 内存计算 (< 1ms) | 内存计算 (< 1ms) | 无变化 |
| **重排序** | **无** | **200-500ms (BGE-Reranker)** | **新增** |
| HyDE LLM 调用 | 1-2s | 1-2s | 无变化 |
| **总计** | **~50-100ms** | **~300-600ms** | **主要在 Reranker** |

### 11.2 优化策略

1. **precise 模式跳过 Reranker**：当有精确 req_ids 匹配时，不需要语义精排
2. **Reranker 结果缓存**：相同 (query, doc_id) 对 5 分钟内返回缓存分数
3. **GPU 推理**：将 `SentenceTransformerRerank` 的 device 设置为 `cuda`，延迟可降至 50-100ms
4. **可配置开关**：通过 `PipelineConfig.enable_rerank = False` 完全禁用

---

## 12. 错误处理与回退

### 12.1 管道初始化回退

```python
# query.py 中的初始化保护
try:
    from spma.agents.doc.llamaindex_pipeline import (
        AdvancedLlamaIndexPipeline,
        PipelineConfig,
    )
    USE_LLAMAINDEX = True
except ImportError as e:
    logger.warning("LlamaIndex 不可用，回退到原有实现: %s", e)
    USE_LLAMAINDEX = False
```

### 12.2 运行时回退

```python
# search_node 中的异常保护（已在 graph.py 中包含）
try:
    fused = await llama_pipeline.search(...)
except Exception:
    fused = []
```

### 12.3 熔断器保持

现有的 `@circuit_breaker("elasticsearch")` 和 `@circuit_breaker("pgvector")` 装饰器继续生效——`ESBM25Retriever` 内部调用的 `ESClient.search()` 和 `VectorIndexRetriever` 内部调用的 PGVector 操作仍受熔断保护。

---

## 13. 测试策略

### 13.1 新增单元测试

| 测试对象 | 测试文件 | 测试内容 |
|---------|---------|---------|
| `ESBM25Retriever` | `test_llamaindex_retrievers.py` | mock ESClient 验证 NodeWithScore 转换正确性 |
| `HybridRRFRetriever` | `test_llamaindex_retrievers.py` | 多路检索 + RRF 分数计算正确性 |
| `BGEM3EmbeddingAdapter` | `test_llamaindex_embedding.py` | 嵌入向量维度/归一化验证 |
| `build_postprocessor_chain` | `test_llamaindex_pipeline.py` | 不同模式的 postprocessor 组合 |
| `AdvancedLlamaIndexPipeline.search` | `test_llamaindex_pipeline.py` | 端到端检索 (precise/hybrid/semantic) |

### 13.2 现有测试保护

- `graph.py` 其余节点（route/aggregate/assess/expand）的测试保持不变
- 集成测试验证 `search_node` 输出格式兼容性

---

## 14. 迁移路径

```
Phase 1: 基础设施（1-2天）
├── 安装 LlamaIndex 依赖
├── 实现 BGEM3EmbeddingAdapter
├── 实现 ESBM25Retriever
└── 单元测试

Phase 2: 核心管道（2-3天）
├── 实现 HybridRRFRetriever
├── 实现 AdvancedLlamaIndexPipeline
├── 实现 Postprocessor 链
└── 集成测试

Phase 3: 集成切换（1-2天）
├── 改造 graph.py search_node
├── 改造 query.py 初始化
├── 端到端回归测试
└── 性能基准对比

Phase 4: 优化（1-2天）
├── Reranker 缓存
├── GPU 推理选项
└── 生产环境验证
```

---

## 15. 附录：设计文档原方案三的问题总结

原设计文档中的方案三示例代码存在以下与真实 LlamaIndex API 不兼容的问题：

| # | 问题 | 原代码 | 正确做法 |
|---|------|--------|---------|
| 1 | `FusionRetriever` 语义不对 | 用作文本+向量融合 | 应使用自定义 `HybridRRFRetriever`；`FusionRetriever` 是 query 变体融合 |
| 2 | `KeywordTableSimpleRetriever` 无法从 VectorStoreIndex 构建 | 直接传入 `self._index` | 需要独立的 `KeywordTableIndex`，不适合本场景 |
| 3 | `HyDEQueryEngine` 不存在 | 作为 QueryEngine 包装 | HyDE 通过 `HyDEQueryTransform` + 标准 Retriever 实现 |
| 4 | `KeywordNodePostprocessor` 空列表无意义 | `required_keywords=[]` | 移除；关键词过滤应在 BM25 检索阶段完成 |
| 5 | `SimilarityPostprocessor` 不适配 RRF 分数 | `similarity_cutoff=0.7` | RRF 分数量级不同（约 0.03），应在向量检索阶段通过 top_k 控制 |
