# SPMA Design 09: LlamaIndex 改造方案

## 1. 背景与动机

### 1.1 当前架构分析

SPMA 当前的向量检索模块基于以下组件构建：

| 组件 | 当前实现 | 职责 |
|------|---------|------|
| **向量存储** | `PGVectorStore` (asyncpg + pgvector) | 向量写入、相似度搜索 |
| **嵌入模型** | `BGEM3Embedder` (sentence-transformers) | 文本向量化 |
| **检索逻辑** | 自定义混合检索 + RRF Fusion | BM25 + 向量融合 |
| **图编排** | LangGraph | 多轮检索、扩展、评估 |

### 1.2 改造目标

引入 LlamaIndex 作为向量搜索模块的核心引擎，以获得以下优势：

- **统一检索抽象**：LlamaIndex 提供完整的检索管道抽象
- **丰富的检索策略**：支持 Hybrid Search、HyDE、Reranking 等
- **生态集成**：与 LangChain、各种向量数据库无缝对接
- **可扩展性**：便于未来接入更多高级检索特性

---

## 2. 改造方案

### 方案一：最小侵入式封装（推荐起步）

#### 2.1.1 核心思路

创建 LlamaIndex 适配器，**保持现有接口不变**，仅替换底层实现。

#### 2.1.2 改造步骤

**步骤 1：创建 LlamaIndex 向量存储适配器**

```python
# src/spma/retrieval/llamaindex_store.py
"""LlamaIndex 向量存储适配器——适配现有 PGVectorStore 接口。"""

import logging
from typing import Any

from llama_index.core import VectorStoreIndex, Settings
from llama_index.vector_stores.postgres import PGVectorStore as LlamaPGVectorStore
from llama_index.embeddings.langchain import LangchainEmbedding
from langchain.embeddings import HuggingFaceEmbeddings

logger = logging.getLogger(__name__)


class LlamaIndexVectorStore:
    """适配现有 PGVectorStore 接口的 LlamaIndex 封装。"""

    def __init__(self, dsn: str | None = None):
        self._dsn = dsn or "postgresql://spma:spma123@localhost:5433/spma"
        self._index = None
        self._vector_store = None

    async def _ensure_index(self) -> VectorStoreIndex:
        if self._index is None:
            Settings.chunk_size = 512
            Settings.chunk_overlap = 64
            
            Settings.embed_model = LangchainEmbedding(
                HuggingFaceEmbeddings(
                    model_name="BAAI/bge-m3",
                    model_kwargs={"device": "cpu"},
                    encode_kwargs={"normalize_embeddings": True}
                )
            )
            
            self._vector_store = LlamaPGVectorStore.from_uri(self._dsn)
            self._index = VectorStoreIndex.from_vector_store(self._vector_store)
        
        return self._index

    async def search(
        self,
        embedding: list[float],
        top_k: int = 20,
        table: str = "chunk_embeddings",
    ) -> list[dict]:
        """向量相似度搜索——保持与 PGVectorStore 相同接口。"""
        index = await self._ensure_index()
        
        query_engine = index.as_query_engine(
            similarity_top_k=top_k,
            vector_store_query_mode="default"
        )
        
        from llama_index.core import QueryBundle
        
        result = await query_engine.aquery(
            QueryBundle(
                query_str="",
                embedding=embedding
            )
        )
        
        return [
            {
                "chunk_id": node.node_id,
                "source_id": node.metadata.get("source_id"),
                "source_type": node.metadata.get("source_type"),
                "content": node.text,
                "score": node.score,
                "metadata": node.metadata or {},
            }
            for node in result.source_nodes
        ]

    async def upsert(
        self,
        chunk_id: str,
        source_id: str | None,
        source_type: str | None,
        content: str,
        embedding: list[float],
        metadata: dict | None = None,
    ) -> None:
        """插入或更新向量记录——保持与 PGVectorStore 相同接口。"""
        index = await self._ensure_index()
        
        from llama_index.core.schema import TextNode
        
        node = TextNode(
            id_=chunk_id,
            text=content,
            embedding=embedding,
            metadata={
                "source_id": source_id,
                "source_type": source_type,
                **(metadata or {})
            }
        )
        
        await index.insert_nodes([node])

    async def health_check(self) -> bool:
        try:
            await self._ensure_index()
            return True
        except Exception as e:
            logger.warning(f"LlamaIndex health check 失败: {e}")
            return False
```

**步骤 2：修改 `graph.py` 中的搜索节点**

```python
async def search_node(state: DocAgentState) -> dict:
    query = state.get("current_query", state.get("original_query", ""))
    mode = state.get("weight_mode", "semantic")
    entities = state.get("entities", {})

    es_filters = None
    if mode == "precise" and entities.get("req_ids"):
        es_filters = {"req_ids": entities["req_ids"]}

    bm25_results: list[dict] = []
    vector_results: list[dict] = []

    try:
        es_future = es_client.search(query, top_k=20, filters=es_filters)
        # LlamaIndex 模式：内部自动处理嵌入
        if isinstance(vector_store, LlamaIndexVectorStore):
            query_embedding = await embedder.embed([query])
            vector_future = vector_store.search(query_embedding[0], top_k=20)
        else:
            query_embedding = await embedder.embed([query])
            vector_future = vector_store.search(embedding=query_embedding[0], top_k=20, table="chunk_embeddings")
        bm25_results, vector_results = await asyncio.gather(es_future, vector_future)
    except Exception:
        pass
    # ... 其余逻辑保持不变
```

**步骤 3：在 `query.py` 中切换实现**

```python
# 替换原有初始化
try:
    from spma.retrieval.llamaindex_store import LlamaIndexVectorStore
    vector_store = LlamaIndexVectorStore()
except Exception as e:
    logger.warning("LlamaIndex 初始化失败，回退到 PGVector: %s", e)
    from spma.retrieval.vector_store import PGVectorStore
    vector_store = PGVectorStore()
```

#### 2.1.3 方案特点

| 维度 | 描述 |
|------|------|
| **侵入性** | 极低，接口完全兼容 |
| **改动量** | 最小，仅需新增适配器文件 |
| **风险** | 低，可快速回退 |
| **适用场景** | 快速验证、小规模试用 |

---

### 方案二：中等改造 - 集成 LlamaIndex Query Engine

#### 2.2.1 核心思路

利用 LlamaIndex 的 Query Engine 能力，**重构检索流程**，保留现有 BM25 检索。

#### 2.2.2 改造步骤

**步骤 1：创建 LlamaIndex 混合检索器**

```python
# src/spma/agents/doc/llamaindex_retriever.py
"""基于 LlamaIndex 的文档检索器——替换原有 search_node。"""

import asyncio
from typing import Any, Dict, List

from llama_index.core import VectorStoreIndex, Settings
from llama_index.core.retrievers import BaseRetriever
from llama_index.core.query_engine import RetrieverQueryEngine
from llama_index.core.schema import NodeWithScore
from llama_index.vector_stores.postgres import PGVectorStore as LlamaPGVectorStore


class LlamaIndexHybridRetriever(BaseRetriever):
    """LlamaIndex 混合检索器——结合 BM25 和向量检索。"""

    def __init__(self, vector_index: VectorStoreIndex, bm25_retriever: Any):
        self._vector_index = vector_index
        self._bm25_retriever = bm25_retriever
        self._vector_retriever = vector_index.as_retriever(similarity_top_k=15)

    async def _aretrieve(self, query_bundle: Any) -> List[NodeWithScore]:
        """异步混合检索——BM25 + 向量。"""
        vector_future = self._vector_retriever.aretrieve(query_bundle)
        bm25_future = asyncio.to_thread(
            self._bm25_retriever.search,
            query_bundle.query_str,
            top_k=15
        )
        
        vector_nodes, bm25_results = await asyncio.gather(vector_future, bm25_future)
        
        seen_ids = set()
        merged = []
        
        for node in vector_nodes:
            if node.node_id not in seen_ids:
                seen_ids.add(node.node_id)
                merged.append(node)
        
        for result in bm25_results:
            chunk_id = result.get("chunk_id")
            if chunk_id not in seen_ids:
                seen_ids.add(chunk_id)
                from llama_index.core.schema import TextNode
                node = TextNode(
                    id_=chunk_id,
                    text=result.get("content", ""),
                    metadata={
                        "source_id": result.get("source_id"),
                        "source_type": result.get("source_type"),
                        "score": result.get("score"),
                    }
                )
                merged.append(NodeWithScore(node=node, score=result.get("score", 0.5)))
        
        return merged[:20]


def build_llamaindex_retriever(es_client, dsn: str = None):
    """构建 LlamaIndex 检索器。"""
    from llama_index.embeddings.langchain import LangchainEmbedding
    from langchain.embeddings import HuggingFaceEmbeddings
    
    Settings.embed_model = LangchainEmbedding(
        HuggingFaceEmbeddings(
            model_name="BAAI/bge-m3",
            model_kwargs={"device": "cpu"},
            encode_kwargs={"normalize_embeddings": True}
        )
    )
    
    vector_store = LlamaPGVectorStore.from_uri(dsn or "postgresql://spma:spma123@localhost:5433/spma")
    index = VectorStoreIndex.from_vector_store(vector_store)
    
    retriever = LlamaIndexHybridRetriever(
        vector_index=index,
        bm25_retriever=es_client
    )
    
    query_engine = RetrieverQueryEngine.from_args(retriever)
    
    return query_engine
```

**步骤 2：重构 `graph.py` 的 search_node**

```python
async def search_node(state: DocAgentState) -> dict:
    query = state.get("current_query", state.get("original_query", ""))
    
    query_engine = state.get("llamaindex_query_engine")
    response = await query_engine.aquery(query)
    
    vector_results = [
        {
            "chunk_id": node.node_id,
            "source_id": node.metadata.get("source_id"),
            "source_type": node.metadata.get("source_type"),
            "content": node.node.text,
            "score": node.score,
            "metadata": node.node.metadata or {},
            "source_type": "hybrid",
        }
        for node in response.source_nodes
    ]
    
    state["fused_results"] = vector_results[:10]
    return state
```

#### 2.2.3 方案特点

| 维度 | 描述 |
|------|------|
| **侵入性** | 中等，需要重构检索节点 |
| **改动量** | 中等，新增检索器文件 |
| **风险** | 中等，需测试验证 |
| **适用场景** | 生产环境升级 |

---

### 方案三：深度集成 - 全流程 LlamaIndex 化

#### 2.3.1 核心思路

使用 LlamaIndex 的**完整能力**，包括 Query Engine、Reranker、HyDE、Fusion Retriever 等。

#### 2.3.2 改造步骤

**步骤 1：创建高级 LlamaIndex 管道**

```python
# src/spma/agents/doc/llamaindex_pipeline.py
"""深度集成 LlamaIndex 的文档检索管道。"""

from typing import Any, Dict, List

from llama_index.core import (
    VectorStoreIndex,
    Settings,
    QueryBundle,
    get_response_synthesizer,
)
from llama_index.core.query_engine import RetrieverQueryEngine
from llama_index.core.retrievers import (
    BaseRetriever,
    VectorIndexRetriever,
    KeywordTableSimpleRetriever,
    FusionRetriever,
)
from llama_index.core.postprocessor import (
    SimilarityPostprocessor,
    KeywordNodePostprocessor,
)
from llama_index.core.schema import NodeWithScore
from llama_index.vector_stores.postgres import PGVectorStore as LlamaPGVectorStore


class AdvancedLlamaIndexPipeline:
    """高级 LlamaIndex 检索管道——集成多种检索策略。"""

    def __init__(self, dsn: str = None):
        self._dsn = dsn or "postgresql://spma:spma123@localhost:5433/spma"
        self._index = None
        self._query_engine = None
        self._initialize()

    def _initialize(self):
        """初始化 LlamaIndex 组件。"""
        from llama_index.embeddings.langchain import LangchainEmbedding
        from langchain.embeddings import HuggingFaceEmbeddings
        
        Settings.embed_model = LangchainEmbedding(
            HuggingFaceEmbeddings(
                model_name="BAAI/bge-m3",
                model_kwargs={"device": "cpu"},
                encode_kwargs={"normalize_embeddings": True}
            )
        )
        
        vector_store = LlamaPGVectorStore.from_uri(self._dsn)
        self._index = VectorStoreIndex.from_vector_store(vector_store)
        
        vector_retriever = VectorIndexRetriever(
            index=self._index,
            similarity_top_k=20,
        )
        
        keyword_retriever = KeywordTableSimpleRetriever(
            index=self._index,
            keyword_extraction_model="local"
        )
        
        self._retriever = FusionRetriever(
            [vector_retriever, keyword_retriever],
            retriever_weights=[0.7, 0.3],
            similarity_top_k=15,
        )
        
        postprocessors = [
            SimilarityPostprocessor(similarity_cutoff=0.7),
            KeywordNodePostprocessor(required_keywords=[]),
        ]
        
        response_synthesizer = get_response_synthesizer(
            response_mode="no_text",
            use_async=True,
        )
        
        self._query_engine = RetrieverQueryEngine(
            retriever=self._retriever,
            response_synthesizer=response_synthesizer,
            node_postprocessors=postprocessors,
        )

    async def search(self, query: str, mode: str = "hybrid") -> List[Dict]:
        """执行检索——支持多种模式。"""
        response = await self._query_engine.aquery(query)
        
        return [
            {
                "chunk_id": node.node_id,
                "source_id": node.node.metadata.get("source_id"),
                "source_type": node.node.metadata.get("source_type"),
                "content": node.node.text,
                "score": node.score,
                "metadata": node.node.metadata or {},
            }
            for node in response.source_nodes
        ]

    async def search_with_hyde(self, query: str) -> List[Dict]:
        """使用 HyDE 增强检索。"""
        from llama_index.core.query_engine import HyDEQueryEngine
        
        hyde_query_engine = HyDEQueryEngine(
            query_engine=self._query_engine,
            hyde_prompt=None,
        )
        
        response = await hyde_query_engine.aquery(query)
        
        return [
            {
                "chunk_id": node.node_id,
                "content": node.node.text,
                "score": node.score,
                "metadata": node.node.metadata or {},
            }
            for node in response.source_nodes
        ]
```

**步骤 2：集成到 `graph.py`**

```python
def build_doc_agent_graph(es_client, vector_store, embedder, llm, hyde_llm=None, weights_config=None):
    """构建 Doc Agent 的 LangGraph StateGraph——深度集成 LlamaIndex。"""
    
    llama_pipeline = AdvancedLlamaIndexPipeline()
    
    async def search_node(state: DocAgentState) -> dict:
        query = state.get("current_query", state.get("original_query", ""))
        
        if state.get("hyde_enabled"):
            results = await llama_pipeline.search_with_hyde(query)
        else:
            results = await llama_pipeline.search(query)
        
        state["fused_results"] = results[:10]
        return state
    
    # ... 其余节点保持不变
```

#### 2.3.3 方案特点

| 维度 | 描述 |
|------|------|
| **侵入性** | 高，全流程替换 |
| **改动量** | 大，需重构检索逻辑 |
| **风险** | 高，需充分测试 |
| **适用场景** | 需要 LlamaIndex 高级特性 |

---

## 3. 方案对比

| 维度 | 方案一（封装适配） | 方案二（中等改造） | 方案三（深度集成） |
|------|-------------------|-------------------|-------------------|
| **侵入性** | 低（接口不变） | 中（部分重构） | 高（全流程替换） |
| **LlamaIndex 能力利用** | 基础 | 中等 | 完整 |
| **代码改动量** | 小 | 中 | 大 |
| **迁移风险** | 低 | 中 | 高 |
| **推荐场景** | 快速验证 | 生产环境 | 高级特性需求 |

---

## 4. 推荐迁移路径

```
┌─────────────────────────────────────────────────────────────┐
│                    方案一（快速验证）                        │
│  创建适配器 → 验证基础功能 → 确认环境兼容性                   │
└─────────────────────────┬───────────────────────────────────┘
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                    方案二（生产升级）                        │
│  重构检索节点 → 集成混合检索 → 性能调优                       │
└─────────────────────────┬───────────────────────────────────┘
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                    方案三（深度集成）                        │
│  接入 Fusion Retriever → HyDE → Reranker                   │
└─────────────────────────────────────────────────────────────┘
```

---

## 5. 依赖要求

```bash
# LlamaIndex 核心依赖
pip install llama-index llama-index-vector-stores-postgres

# 嵌入模型相关
pip install llama-index-embeddings-langchain langchain sentence-transformers

# 如果需要其他向量存储（可选）
pip install llama-index-vector-stores-pinecone
pip install llama-index-vector-stores-chroma
```

---

## 6. 注意事项

1. **数据迁移**：确保现有 PGVector 数据与 LlamaIndex 格式兼容
2. **性能测试**：对比三种方案的检索性能和质量
3. **错误处理**：保留原有异常处理逻辑，确保可回退
4. **配置管理**：将数据库连接信息纳入配置文件管理
5. **日志追踪**：增加 LlamaIndex 检索过程的日志记录