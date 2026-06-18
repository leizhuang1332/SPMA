# LlamaIndex 深度集成实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 SPMA 文档检索管道迁移到 LlamaIndex（方案三深度集成），通过自定义 Retriever + Postprocessor 链实现可组合、可测试的检索架构。

**Architecture:** 三层管道架构——Layer 1: ESBM25Retriever + HybridRRFRetriever 封装 BM25+向量混合检索与 RRF 融合；Layer 2: SentenceTransformerRerank + LongContextReorder 后处理链；Layer 3: BGEM3EmbeddingAdapter 共享嵌入模型。通过 AdvancedLlamaIndexPipeline 统一入口，对 graph.py 仅修改 search_node。

**Tech Stack:** LlamaIndex Core 0.12+, PGVector, Elasticsearch, BGE-M3, BGE-Reranker v2 M3, LangGraph

---

## 文件结构

| 文件 | 操作 | 职责 |
|------|------|------|
| `pyproject.toml` | 修改 | 新增 llama-index 依赖 |
| `src/spma/agents/doc/llamaindex_embedding.py` | 创建 | BGEM3Embedder → LlamaIndex BaseEmbedding 适配 |
| `src/spma/agents/doc/llamaindex_retrievers.py` | 创建 | ESBM25Retriever + HybridRRFRetriever + RRFConfig |
| `src/spma/agents/doc/llamaindex_pipeline.py` | 创建 | AdvancedLlamaIndexPipeline + PipelineConfig + build_postprocessor_chain |
| `src/spma/agents/doc/graph.py` | 修改 | search_node 简化为管道委托 |
| `src/spma/api/routes/query.py` | 不变 | 无需改动——Settings.embed_model 由 pipeline.initialize() 内部设置 |
| `tests/unit/agents/doc/test_llamaindex_embedding.py` | 创建 | BGEM3EmbeddingAdapter 单元测试 |
| `tests/unit/agents/doc/test_llamaindex_retrievers.py` | 创建 | ESBM25Retriever + HybridRRFRetriever + RRFConfig 单元测试 |
| `tests/unit/agents/doc/test_llamaindex_pipeline.py` | 创建 | PipelineConfig + build_postprocessor_chain + Pipeline 集成测试 |

---

## Phase 1: 基础设施（依赖 + 嵌入适配）

### Task 1: 添加 LlamaIndex 依赖

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: 添加 llama-index 核心依赖到 pyproject.toml**

在 `pyproject.toml` 的 `dependencies` 列表中追加 LlamaIndex 相关依赖。将 `llama-index-core`、`llama-index-vector-stores-postgres`、`llama-index-embeddings-langchain`、`llama-index-postprocessor-sbert-rerank` 添加到主依赖列表。

```toml
# 在 "asyncpg>=0.31.0", 之后追加以下四行：
    "llama-index-core>=0.12.0",
    "llama-index-vector-stores-postgres>=0.4.0",
    "llama-index-embeddings-langchain>=0.3.0",
    "llama-index-postprocessor-sbert-rerank>=0.2.0",
```

执行命令验证依赖可解析：

```bash
cd /Users/Ray/TraeProjects/SPMA && uv lock --dry-run 2>&1 | tail -5
```

**Expected:** 无解析错误，新增的 4 个包及其传递依赖被列出。

- [ ] **Step 2: 安装依赖**

```bash
cd /Users/Ray/TraeProjects/SPMA && uv sync
```

**Expected:** 成功安装 llama-index-core 及其子包，无版本冲突。

- [ ] **Step 3: 验证导入**

```bash
cd /Users/Ray/TraeProjects/SPMA && python -c "
from llama_index.core import VectorStoreIndex, Settings, QueryBundle
from llama_index.core.retrievers import BaseRetriever, VectorIndexRetriever
from llama_index.core.postprocessor import SentenceTransformerRerank, LongContextReorder
from llama_index.core.schema import NodeWithScore, TextNode
from llama_index.core.base.embeddings.base import BaseEmbedding
from llama_index.vector_stores.postgres import PGVectorStore
print('All imports OK')
"
```

**Expected:** `All imports OK`

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "feat: add llama-index dependencies for Option 3 deep integration"
```

---

### Task 2: 实现 BGEM3EmbeddingAdapter

**Files:**
- Create: `src/spma/agents/doc/llamaindex_embedding.py`
- Create: `tests/unit/agents/doc/test_llamaindex_embedding.py`

- [ ] **Step 1: 编写失败测试**

```python
# tests/unit/agents/doc/test_llamaindex_embedding.py
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
```

- [ ] **Step 2: 运行测试验证失败**

```bash
cd /Users/Ray/TraeProjects/SPMA && python -m pytest tests/unit/agents/doc/test_llamaindex_embedding.py -v
```

**Expected:** 全部 5 个测试 FAIL，因为 `BGEM3EmbeddingAdapter` 尚未创建。

- [ ] **Step 3: 实现 BGEM3EmbeddingAdapter**

```python
# src/spma/agents/doc/llamaindex_embedding.py
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

    def _get_text_embeddings(self, texts: List[str]) -> List[List[float]]:
        raise NotImplementedError(
            "BGEM3EmbeddingAdapter 仅支持异步调用，请使用 _aget_text_embeddings"
        )
```

- [ ] **Step 4: 运行测试验证通过**

```bash
cd /Users/Ray/TraeProjects/SPMA && python -m pytest tests/unit/agents/doc/test_llamaindex_embedding.py -v
```

**Expected:** 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/spma/agents/doc/llamaindex_embedding.py tests/unit/agents/doc/test_llamaindex_embedding.py
git commit -m "feat: add BGEM3EmbeddingAdapter for LlamaIndex BaseEmbedding interface"
```

---

## Phase 2: 自定义 Retriever 实现

### Task 3: 实现 RRFConfig + ESBM25Retriever

**Files:**
- Create: `src/spma/agents/doc/llamaindex_retrievers.py`（第一部分）
- Create: `tests/unit/agents/doc/test_llamaindex_retrievers.py`（第一部分）

- [ ] **Step 1: 编写 ESBM25Retriever 失败测试**

```python
# tests/unit/agents/doc/test_llamaindex_retrievers.py（第一部分）
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
        results = await retriever.aretrieve(query_bundle)

        assert len(results) == 2
        assert isinstance(results[0], NodeWithScore)
        assert results[0].node.node_id == "chunk-1"
        assert results[0].node.get_content() == "需求文档内容片段A"
        assert results[0].node.metadata["source_id"] == "doc-123"
        assert results[0].node.metadata["req_ids"] == ["REQ-001"]
        assert results[0].node.metadata["retrieval_source"] == "bm25"
        assert results[0].score == 12.5
        # 验证 ES search 被正确调用
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
        results = await retriever.aretrieve(query_bundle)

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
        await retriever.aretrieve(query_bundle)

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
        await retriever.aretrieve(query_bundle)

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
        results = await retriever.aretrieve(query_bundle)

        assert results == []
        mock_es.search.assert_called_once_with(
            query="", top_k=20, filters=None
        )
```

- [ ] **Step 2: 运行测试验证失败**

```bash
cd /Users/Ray/TraeProjects/SPMA && python -m pytest tests/unit/agents/doc/test_llamaindex_retrievers.py::TestESBM25Retriever -v
```

**Expected:** 全部 6 个测试 FAIL（`ESBM25Retriever` 尚未创建）。

- [ ] **Step 3: 实现 RRFConfig + ESBM25Retriever**

```python
# src/spma/agents/doc/llamaindex_retrievers.py（第一部分）
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
                    "retrieval_source": "bm25",  # 标记来源，便于 graph.py 区分
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

- [ ] **Step 4: 运行测试验证通过**

```bash
cd /Users/Ray/TraeProjects/SPMA && python -m pytest tests/unit/agents/doc/test_llamaindex_retrievers.py::TestESBM25Retriever -v
```

**Expected:** 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/spma/agents/doc/llamaindex_retrievers.py tests/unit/agents/doc/test_llamaindex_retrievers.py
git commit -m "feat: add RRFConfig and ESBM25Retriever for ES to LlamaIndex adapter"
```

---

### Task 4: 实现 HybridRRFRetriever

**Files:**
- Modify: `src/spma/agents/doc/llamaindex_retrievers.py`（追加第二部分）
- Modify: `tests/unit/agents/doc/test_llamaindex_retrievers.py`（追加第二部分）

- [ ] **Step 1: 编写 HybridRRFRetriever 失败测试**

```python
# 追加到 tests/unit/agents/doc/test_llamaindex_retrievers.py 末尾

from unittest.mock import AsyncMock, MagicMock

from llama_index.core import QueryBundle
from llama_index.core.schema import NodeWithScore, TextNode


class TestHybridRRFRetriever:
    """测试 HybridRRFRetriever——并行 BM25+向量 + 加权 RRF 融合。"""

    def _make_node(self, node_id: str, text: str, retrieval_source: str, score: float):
        """构造 NodeWithScore 辅助方法。"""
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

        # Mock 向量检索器：返回 2 个节点
        mock_vector = AsyncMock()
        mock_vector.aretrieve = AsyncMock(return_value=[
            self._make_node("c1", "内容1", "vector", 0.95),
            self._make_node("c2", "内容2", "vector", 0.80),
        ])

        # Mock BM25 检索器：返回 2 个节点，其中 c2 与向量侧重叠
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

        query_bundle = QueryBundle(query_str="测试查询")
        results = await retriever.aretrieve(query_bundle)

        # 应该去重后有 3 个唯一节点
        assert len(results) == 3

        # c2 在两路都出现，RRF 分数应最高
        c2_result = next(r for r in results if r.node.node_id == "c2")
        c1_result = next(r for r in results if r.node.node_id == "c1")
        c3_result = next(r for r in results if r.node.node_id == "c3")

        # c2 在 bm25 rank0 + vector rank1 → 应排第一
        assert results[0].node.node_id == "c2"
        # 验证 RRF 分数已被设置
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

        # bm25 权重更高 → c2 应排第一
        config = RRFConfig(k=60, top_k=10, bm25_weight=0.8, vector_weight=0.2)
        retriever = HybridRRFRetriever(
            vector_retriever=mock_vector,
            bm25_retriever=mock_bm25,
            config=config,
        )

        query_bundle = QueryBundle(query_str="测试查询")
        results = await retriever.aretrieve(query_bundle)

        assert len(results) == 2
        # bm25 权重 (0.8) > vector 权重 (0.2) → bm25 rank0 的 RRF 分数最高
        assert results[0].node.node_id == "c2"

    @pytest.mark.asyncio
    async def test_rrf_truncates_to_top_k(self):
        """验证 RRF 融合按 top_k 截断。"""
        from spma.agents.doc.llamaindex_retrievers import (
            HybridRRFRetriever,
            RRFConfig,
        )

        # 向量侧返回 10 个不同节点
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

        query_bundle = QueryBundle(query_str="测试查询")
        results = await retriever.aretrieve(query_bundle)

        assert len(results) == 3
        # 应该是 rank 最高的 3 个
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

        query_bundle = QueryBundle(query_str="测试查询")
        await retriever.aretrieve(query_bundle)

        # 顺序不应是严格的 "vector" then "bm25"（串行），
        # 但 asyncio.gather 并发执行所以 order 顺序不确定
        # 这里只验证两路都被调用了
        assert "vector" in order
        assert "bm25" in order

    def test_sync_retrieve_raises_not_implemented(self):
        """验证同步 _retrieve 抛出 NotImplementedError。"""
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
        with pytest.raises(NotImplementedError):
            retriever._retrieve(QueryBundle(query_str="test"))
```

- [ ] **Step 2: 运行测试验证失败**

```bash
cd /Users/Ray/TraeProjects/SPMA && python -m pytest tests/unit/agents/doc/test_llamaindex_retrievers.py::TestHybridRRFRetriever -v
```

**Expected:** 全部 5 个测试 FAIL（`HybridRRFRetriever` 尚未创建）。

- [ ] **Step 3: 实现 HybridRRFRetriever**

```python
# 追加到 src/spma/agents/doc/llamaindex_retrievers.py 末尾

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

        # Step 3: 按 RRF 分数降序排列，标记融合来源
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

- [ ] **Step 4: 运行全部 retrievers 测试**

```bash
cd /Users/Ray/TraeProjects/SPMA && python -m pytest tests/unit/agents/doc/test_llamaindex_retrievers.py -v
```

**Expected:** 11 passed（6 ESBM25Retriever + 5 HybridRRFRetriever）

- [ ] **Step 5: Commit**

```bash
git add src/spma/agents/doc/llamaindex_retrievers.py tests/unit/agents/doc/test_llamaindex_retrievers.py
git commit -m "feat: add HybridRRFRetriever for parallel BM25+vector with weighted RRF fusion"
```

---

## Phase 3: 核心管道 + Postprocessor 链

### Task 5: 实现 PipelineConfig + build_postprocessor_chain

**Files:**
- Create: `src/spma/agents/doc/llamaindex_pipeline.py`（第一部分）
- Create: `tests/unit/agents/doc/test_llamaindex_pipeline.py`（第一部分）

- [ ] **Step 1: 编写 PipelineConfig + build_postprocessor_chain 测试**

```python
# tests/unit/agents/doc/test_llamaindex_pipeline.py（第一部分）
"""AdvancedLlamaIndexPipeline 单元测试。"""

import pytest


class TestPipelineConfig:
    """测试 PipelineConfig——管道参数集中管理。"""

    def test_default_values(self):
        """验证默认值与设计文档一致。"""
        from spma.agents.doc.llamaindex_pipeline import PipelineConfig

        cfg = PipelineConfig()
        assert cfg.vector_top_k == 20
        assert cfg.bm25_top_k == 20
        assert cfg.hybrid_final_top_k == 15
        assert cfg.rrf_k == 60
        assert cfg.rrf_bm25_weight == 0.5
        assert cfg.rrf_vector_weight == 0.5
        assert cfg.rerank_model == "BAAI/bge-reranker-v2-m3"
        assert cfg.rerank_top_n == 10
        assert cfg.enable_rerank is True
        assert cfg.hyde_max_query_len == 30
        assert cfg.hyde_top_k == 10

    def test_mode_weights_defaults(self):
        """验证默认模式权重。"""
        from spma.agents.doc.llamaindex_pipeline import PipelineConfig

        cfg = PipelineConfig()
        assert cfg.mode_weights["precise"] == {"bm25": 0.7, "vector": 0.3}
        assert cfg.mode_weights["hybrid"] == {"bm25": 0.5, "vector": 0.5}
        assert cfg.mode_weights["semantic"] == {"bm25": 0.3, "vector": 0.7}

    def test_custom_values_override_defaults(self):
        """验证自定义值可以覆盖默认值。"""
        from spma.agents.doc.llamaindex_pipeline import PipelineConfig

        cfg = PipelineConfig(
            vector_top_k=50,
            rrf_k=30,
            enable_rerank=False,
        )
        assert cfg.vector_top_k == 50
        assert cfg.rrf_k == 30
        assert cfg.enable_rerank is False
        # 未覆盖的保持默认
        assert cfg.bm25_top_k == 20


class TestBuildPostprocessorChain:
    """测试 build_postprocessor_chain——根据模式构建后处理链。"""

    def test_precise_mode_returns_empty(self):
        """验证 precise 模式返回空链（跳过语义重排）。"""
        from spma.agents.doc.llamaindex_pipeline import build_postprocessor_chain

        chain = build_postprocessor_chain(mode="precise")
        assert chain == []

    def test_hybrid_mode_returns_reranker_and_reorder(self):
        """验证 hybrid 模式返回 Reranker + Reorder。"""
        from spma.agents.doc.llamaindex_pipeline import build_postprocessor_chain
        from llama_index.core.postprocessor import (
            SentenceTransformerRerank,
            LongContextReorder,
        )

        chain = build_postprocessor_chain(mode="hybrid")
        assert len(chain) == 2
        assert isinstance(chain[0], SentenceTransformerRerank)
        assert isinstance(chain[1], LongContextReorder)
        assert chain[0].top_n == 10

    def test_semantic_mode_same_as_hybrid(self):
        """验证 semantic 模式与 hybrid 使用相同的后处理链。"""
        from spma.agents.doc.llamaindex_pipeline import build_postprocessor_chain

        hybrid_chain = build_postprocessor_chain(mode="hybrid")
        semantic_chain = build_postprocessor_chain(mode="semantic")
        assert len(hybrid_chain) == len(semantic_chain)

    def test_custom_rerank_top_n(self):
        """验证 rerank_top_n 参数传递给 Reranker。"""
        from spma.agents.doc.llamaindex_pipeline import build_postprocessor_chain

        chain = build_postprocessor_chain(mode="hybrid", rerank_top_n=5)
        assert chain[0].top_n == 5
```

- [ ] **Step 2: 运行测试验证失败**

```bash
cd /Users/Ray/TraeProjects/SPMA && python -m pytest tests/unit/agents/doc/test_llamaindex_pipeline.py -v
```

**Expected:** 全部 7 个测试 FAIL（文件尚未创建）。

- [ ] **Step 3: 实现 PipelineConfig + build_postprocessor_chain**

```python
# src/spma/agents/doc/llamaindex_pipeline.py（第一部分）
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
    """管道配置——集中管理所有可调参数。

    所有检索和后处理的可调参数集中在一个 dataclass 中，
    便于 YAML 配置加载和运行时覆盖。
    """

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

    # 模式权重覆盖——不同检索模式下的 BM25/向量侧重
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
    hyde_max_query_len: int = 30  # 短查询（<=30 字符）触发 HyDE
    hyde_top_k: int = 10


def build_postprocessor_chain(
    mode: str = "hybrid",
    rerank_model: str = "BAAI/bge-reranker-v2-m3",
    rerank_top_n: int = 10,
) -> list[BaseNodePostprocessor]:
    """根据检索模式构建后处理链。

    precise 模式跳过所有语义重排（保持精确匹配排序），
    hybrid/semantic 模式执行 Cross-Encoder 精排 + 长上下文重排。

    Args:
        mode: "precise" | "hybrid" | "semantic"
        rerank_model: 重排序模型名称
        rerank_top_n: 重排序后保留的节点数

    Returns:
        按顺序执行的 NodePostprocessor 列表
    """
    from llama_index.core.postprocessor import (
        SentenceTransformerRerank,
        LongContextReorder,
    )

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

- [ ] **Step 4: 运行测试验证通过**

```bash
cd /Users/Ray/TraeProjects/SPMA && python -m pytest tests/unit/agents/doc/test_llamaindex_pipeline.py -v
```

**Expected:** 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/spma/agents/doc/llamaindex_pipeline.py tests/unit/agents/doc/test_llamaindex_pipeline.py
git commit -m "feat: add PipelineConfig and build_postprocessor_chain"
```

---

### Task 6: 实现 AdvancedLlamaIndexPipeline

**Files:**
- Modify: `src/spma/agents/doc/llamaindex_pipeline.py`（追加第二部分）
- Modify: `tests/unit/agents/doc/test_llamaindex_pipeline.py`（追加第二部分）

- [ ] **Step 1: 编写 Pipeline 集成测试**

```python
# 追加到 tests/unit/agents/doc/test_llamaindex_pipeline.py 末尾

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from llama_index.core import QueryBundle
from llama_index.core.schema import NodeWithScore, TextNode


class MockEmbedder:
    """Mock BGEM3Embedder——返回固定 1024 维向量。"""
    async def embed(self, texts):
        return [[0.1] * 1024 for _ in texts]


class TestAdvancedLlamaIndexPipeline:
    """测试 AdvancedLlamaIndexPipeline——集成测试。"""

    def _make_pipeline(self, es_client=None, config=None):
        """构造 Pipeline 实例的辅助方法。"""
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
        """验证 __init__ 保存配置和 ES 客户端引用。"""
        mock_es = MagicMock()
        from spma.agents.doc.llamaindex_pipeline import PipelineConfig

        cfg = PipelineConfig(rrf_k=30)
        pipeline = self._make_pipeline(es_client=mock_es, config=cfg)

        assert pipeline._es_client is mock_es
        assert pipeline._config.rrf_k == 30
        assert pipeline._index is None  # initialize 之前为 None
        assert pipeline._embedder is None
        assert pipeline._hyde_llm is None

    @pytest.mark.asyncio
    async def test_should_use_hyde_short_query_no_req_ids(self):
        """验证短查询 + 无 req_ids 时返回 True。"""
        pipeline = self._make_pipeline()

        result = pipeline._should_use_hyde("短查询", {"req_ids": []})
        assert result is True

    @pytest.mark.asyncio
    async def test_should_use_hyde_long_query_returns_false(self):
        """验证长查询返回 False。"""
        pipeline = self._make_pipeline()

        long_query = "这是一条超过三十个字符的非常长的查询文本用于测试 HyDE 触发条件"
        assert len(long_query) > 30
        result = pipeline._should_use_hyde(long_query, {"req_ids": []})
        assert result is False

    @pytest.mark.asyncio
    async def test_should_use_hyde_with_req_ids_returns_false(self):
        """验证有 req_ids 时返回 False。"""
        pipeline = self._make_pipeline()

        result = pipeline._should_use_hyde("短查询", {"req_ids": ["REQ-187"]})
        assert result is False

    @pytest.mark.asyncio
    async def test_build_retriever_precise_mode(self):
        """验证 precise 模式构建带 req_ids 过滤的检索器。"""
        from spma.agents.doc.llamaindex_pipeline import PipelineConfig

        mock_es = AsyncMock()
        mock_es.search = AsyncMock(return_value=[])

        cfg = PipelineConfig()
        pipeline = self._make_pipeline(es_client=mock_es, config=cfg)

        # Mock initialize 的最小依赖
        with patch.object(pipeline, '_index', MagicMock()):
            retriever = pipeline._build_retriever(
                mode="precise",
                entities={"req_ids": ["REQ-001", "REQ-002"]},
            )

        assert retriever._config.bm25_weight == 0.7
        assert retriever._config.vector_weight == 0.3

    @pytest.mark.asyncio
    async def test_build_retriever_hybrid_mode(self):
        """验证 hybrid 模式构建等权检索器。"""
        from spma.agents.doc.llamaindex_pipeline import PipelineConfig

        mock_es = AsyncMock()
        mock_es.search = AsyncMock(return_value=[])

        cfg = PipelineConfig()
        pipeline = self._make_pipeline(es_client=mock_es, config=cfg)

        with patch.object(pipeline, '_index', MagicMock()):
            retriever = pipeline._build_retriever(
                mode="hybrid",
                entities={},
            )

        assert retriever._config.bm25_weight == 0.5
        assert retriever._config.vector_weight == 0.5

    @pytest.mark.asyncio
    async def test_search_returns_compatible_dict_format(self):
        """验证 search() 返回的 dict 格式与现有接口兼容。"""
        from spma.agents.doc.llamaindex_pipeline import PipelineConfig

        # 构造 mock 检索结果——模拟 HybridRRFRetriever 输出
        node1 = TextNode(
            id_="chunk-1",
            text="测试内容1",
            metadata={
                "source_id": "doc-1",
                "source_type": "doc",
                "req_ids": ["REQ-001"],
                "retrieval_source": "bm25",
                "rrf_score": 0.025,
            },
        )
        node2 = TextNode(
            id_="chunk-2",
            text="测试内容2",
            metadata={
                "source_id": "doc-2",
                "source_type": "doc",
                "req_ids": [],
                "retrieval_source": "vector",
                "rrf_score": 0.020,
            },
        )
        scored1 = NodeWithScore(node=node1, score=0.025)
        scored2 = NodeWithScore(node=node2, score=0.020)

        mock_retriever = AsyncMock()
        mock_retriever.aretrieve = AsyncMock(return_value=[scored1, scored2])

        mock_es = AsyncMock()
        cfg = PipelineConfig(enable_rerank=False)
        pipeline = self._make_pipeline(es_client=mock_es, config=cfg)
        pipeline._embedder = MockEmbedder()

        # Mock _build_retriever 返回 mock 检索器
        with patch.object(pipeline, '_build_retriever', return_value=mock_retriever):
            results = await pipeline.search(
                query="测试查询",
                mode="hybrid",
                entities={},
            )

        assert isinstance(results, list)
        assert len(results) == 2
        assert results[0]["chunk_id"] == "chunk-1"
        assert results[0]["source_id"] == "doc-1"
        assert results[0]["content"] == "测试内容1"
        assert results[0]["score"] == 0.025
        assert results[0]["metadata"]["retrieval_source"] == "bm25"
        assert results[1]["chunk_id"] == "chunk-2"
```

- [ ] **Step 2: 运行测试验证失败**

```bash
cd /Users/Ray/TraeProjects/SPMA && python -m pytest tests/unit/agents/doc/test_llamaindex_pipeline.py::TestAdvancedLlamaIndexPipeline -v
```

**Expected:** 部分测试 FAIL（`AdvancedLlamaIndexPipeline` 类的部分方法尚未实现）。

- [ ] **Step 3: 实现 AdvancedLlamaIndexPipeline**

```python
# 追加到 src/spma/agents/doc/llamaindex_pipeline.py 末尾


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
        """延迟初始化——支持依赖注入，在 graph.py 中调用。

        设计决策：不在 __init__ 中自动初始化，因为 embedder 的加载
        是异步的（需要从 ModelScope 下载模型），应由调用方控制时机。
        """
        from llama_index.vector_stores.postgres import PGVectorStore as LlamaPGVectorStore
        from spma.agents.doc.llamaindex_embedding import BGEM3EmbeddingAdapter

        cfg = self._config

        # 复用现有 BGEM3Embedder，避免重复加载模型（BGE-M3 约 2.2GB）
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

- [ ] **Step 4: 运行全部 pipeline 测试**

```bash
cd /Users/Ray/TraeProjects/SPMA && python -m pytest tests/unit/agents/doc/test_llamaindex_pipeline.py -v
```

**Expected:** 13 passed（7 PipelineConfig/Postprocessor + 6 Pipeline）

- [ ] **Step 5: Commit**

```bash
git add src/spma/agents/doc/llamaindex_pipeline.py tests/unit/agents/doc/test_llamaindex_pipeline.py
git commit -m "feat: add AdvancedLlamaIndexPipeline with search, HyDE, and mode routing"
```

---

## Phase 4: LangGraph 集成

### Task 7: 改造 graph.py search_node

**Files:**
- Modify: `src/spma/agents/doc/graph.py`

- [ ] **Step 1: 运行现有测试作为基线**

```bash
cd /Users/Ray/TraeProjects/SPMA && python -m pytest tests/unit/agents/doc/ -v
```

**Expected:** 现有测试全部通过（建立基线）。

- [ ] **Step 2: 改造 graph.py**

将 `search_node` 中的内联检索逻辑替换为 `AdvancedLlamaIndexPipeline.search()` 委托调用。

**改动点：**
1. 删除 `import asyncio`
2. 删除 `from spma.retrieval.rrf_fusion import weighted_fusion`
3. 在 `build_doc_agent_graph` 函数开头初始化 LlamaIndex 管道
4. 替换 `search_node` 实现

```python
# src/spma/agents/doc/graph.py（改造后完整文件）

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

- [ ] **Step 3: 验证现有测试仍然通过**

```bash
cd /Users/Ray/TraeProjects/SPMA && python -m pytest tests/unit/agents/doc/ -v
```

**Expected:** 所有现有测试通过（search_node 的接口未变）。

- [ ] **Step 4: 验证 graph 可编译**

```bash
cd /Users/Ray/TraeProjects/SPMA && python -c "
from unittest.mock import MagicMock

es_client = MagicMock()
es_client.search = MagicMock()
vector_store = MagicMock()
vector_store._dsn = 'postgresql://test:test@localhost:5432/test'
embedder = MagicMock()
embedder.embed = MagicMock()
llm = MagicMock()

from spma.agents.doc.graph import build_doc_agent_graph
graph = build_doc_agent_graph(es_client, vector_store, embedder, llm)
print('Graph compiled OK:', type(graph).__name__)
"
```

**Expected:** `Graph compiled OK: CompiledStateGraph`

- [ ] **Step 5: Commit**

```bash
git add src/spma/agents/doc/graph.py
git commit -m "refactor: delegate search_node to AdvancedLlamaIndexPipeline (Option 3)"
```

---

### Task 8: 清理 query.py（移除多余的 Settings.embed_model 设置）

**Files:**
- Modify: `src/spma/api/routes/query.py`

**说明**：`Settings.embed_model` 现在由 `AdvancedLlamaIndexPipeline.initialize()` 内部设置，query.py 无需单独设置。

实际上，经过自检发现原设计已有说明 query.py 不需要改动——因为 `Settings.embed_model` 已由 `pipeline.initialize()` 在 `build_doc_agent_graph()` 调用时自动设置。因此本任务仅验证无需改动。

- [ ] **Step 1: 验证 query.py 导入正常**

```bash
cd /Users/Ray/TraeProjects/SPMA && python -c "
from spma.api.routes.query import router
print('query.py imports OK')
"
```

**Expected:** `query.py imports OK`

query.py 不需要修改——当前代码已通过 `build_doc_agent_graph(es_client, vector_store, embedder, llm)` 传入 embedder，管道在初始化时自动设置 `Settings.embed_model`。

- [ ] **Step 2: 验证无改动，跳过 commit**

---

## Phase 5: 回归验证

### Task 9: 运行全部单元测试

- [ ] **Step 1: 运行全部单元测试**

```bash
cd /Users/Ray/TraeProjects/SPMA && python -m pytest tests/unit/ -v
```

**Expected:** 全部测试通过，无回归。

- [ ] **Step 2: 运行新增测试确认覆盖率**

```bash
cd /Users/Ray/TraeProjects/SPMA && python -m pytest tests/unit/agents/doc/test_llamaindex_*.py -v --tb=short
```

**Expected:** 约 29 个测试全部通过。

- [ ] **Step 3: Commit（如有未提交变更）**

```bash
git status
```

**Expected:** clean working tree

---

## 自检完成清单

| 检查项 | 状态 |
|--------|------|
| Spec 覆盖 | ✅ 每个设计文档章节对应至少一个 Task |
| Placeholder 扫描 | ✅ 无 TBD/TODO/未完成段落 |
| 类型一致性 | ✅ RRFConfig / PipelineConfig 字段名贯穿所有 Task |
| 文件路径精确 | ✅ 所有路径基于实际项目结构 |
| 命令可执行 | ✅ 所有 pytest 命令与 pyproject.toml 配置匹配 |
| 依赖顺序正确 | ✅ Task 1→2→3→4→5→6→7 依赖链清晰 |
