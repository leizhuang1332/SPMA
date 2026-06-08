# Phase 2 — Doc Agent + Synthesis Agent 实施计划

> **对于 agentic workers:** 必须子技能: 使用 superpowers:subagent-driven-development (推荐) 或 superpowers:executing-plans 逐步执行此计划。步骤使用 checkbox (`- [ ]`) 语法跟踪。

**目标:** 实现 Doc Agent（ES BM25 + PGVector 向量混合检索 + 3 级完备度判断 + 线索扩展循环）和 Synthesis Agent（加权 RRF 融合 + LLM 生成 + 分级自检），以及文档摄入管道、Redis 热状态存储、检索日志和完整测试体系。

**架构:** 5 独立 Agent 架构中的 Phase 2 增量——Doc Agent 和 Synthesis Agent 各自是独立的 LangGraph StateGraph，通过 Supervisor 的 Send API 接收任务派发。ES 为文本权威源（BM25），PGVector 仅存向量+ID，两者通过 chunk_id 关联。

**技术栈:** Python 3.13, LangGraph, Elasticsearch async, PGVector (BGE-M3), Redis, FastAPI, APScheduler

---

## 文件结构

| 文件 | 职责 | 状态 |
|------|------|------|
| `src/spma/retrieval/es_client.py` | ES 异步客户端封装 | 新建 |
| `src/spma/retrieval/rrf_fusion.py` | RRF 融合算法 | 新建 |
| `src/spma/retrieval/hybrid_search.py` | 混合检索编排 | 补全 |
| `src/spma/retrieval/search_logger.py` | 检索日志异步写入 | 补全 |
| `config/es_mapping.yaml` | ES 索引 mapping | 新建 |
| `config/doc_weights.yaml` | 分层权重 + 阈值配置 | 新建 |
| `src/spma/ingestion/parsers/docling_parser.py` | 文档解析器 | 补全 |
| `src/spma/ingestion/chunkers/semantic_chunker.py` | 语义分块器 | 补全 |
| `src/spma/ingestion/doc_pipeline.py` | 文档摄入主流程 | 补全 |
| `src/spma/agents/doc/state.py` | Doc Agent 状态模型 | 补全 |
| `src/spma/agents/doc/graph.py` | Doc Agent LangGraph | 补全 |
| `src/spma/agents/doc/retriever.py` | 检索模式选路+混合检索 | 补全 |
| `src/spma/agents/doc/completeness.py` | 3级完备度判断 | 补全 |
| `src/spma/agents/doc/clue_expander.py` | 线索扩展 | 补全 |
| `src/spma/agents/doc/prompts.py` | LLM Prompt 模板 | 补全 |
| `src/spma/agents/synthesis/state.py` | Synthesis 状态模型 | 补全 |
| `src/spma/agents/synthesis/graph.py` | Synthesis LangGraph | 补全 |
| `src/spma/agents/synthesis/fusion.py` | 加权 RRF 融合 | 补全 |
| `src/spma/agents/synthesis/generator.py` | LLM 生成初稿 | 补全 |
| `src/spma/agents/synthesis/auditor.py` | 自检逻辑 | 补全 |
| `src/spma/agents/synthesis/transparency.py` | 透明度标注 | 补全 |
| `src/spma/agents/synthesis/prompts.py` | Prompt 模板 | 补全 |
| `src/spma/infrastructure/state_store.py` | Redis 热状态+降级 | 补全 |
| `src/spma/models/search_log.py` | 检索日志数据模型 | 补全 |
| `tests/unit/retrieval/test_rrf_fusion.py` | RRF 融合测试 | 新建 |
| `tests/unit/agents/doc/test_router.py` | 模式选路测试 | 新建 |
| `tests/unit/agents/doc/test_assess.py` | 完备度判断测试 | 新建 |
| `tests/unit/agents/doc/test_clue_expander.py` | 线索扩展测试 | 新建 |
| `tests/unit/agents/synthesis/test_fusion.py` | 加权 RRF 测试 | 新建 |
| `tests/unit/agents/synthesis/test_audit.py` | 自检逻辑测试 | 新建 |
| `tests/unit/agents/synthesis/test_transparency.py` | 透明度标注测试 | 新建 |
| `tests/unit/ingestion/test_chunker.py` | 分块器测试 | 新建 |
| `tests/unit/infrastructure/test_state_store.py` | 状态存储测试 | 新建 |
| `tests/integration/test_doc_agent_loop.py` | Doc Agent 循环集成测试 | 新建 |
| `tests/integration/test_synthesis_loop.py` | Synthesis 循环集成测试 | 新建 |
| `tests/integration/test_ingestion_pipeline.py` | 双写一致性测试 | 新建 |
| `tests/e2e/test_doc_single_source.py` | Doc Agent E2E | 新建 |
| `tests/e2e/test_cross_source.py` | 跨源 E2E | 补全 |
| `tests/eval/test_doc_rag.py` | RAG 质量评估 | 新建 |

---

## Slice 1: 检索基础设施（ES 客户端 + RRF 融合 + 配置）

### Task 1.1: ES 索引 Mapping 配置

**Files:**
- Create: `config/es_mapping.yaml`

- [ ] **Step 1: 创建 ES mapping 配置文件**

```yaml
# config/es_mapping.yaml
index_name: spma_docs
settings:
  number_of_shards: 1
  number_of_replicas: 0
  refresh_interval: "1s"
  analysis:
    analyzer:
      ik_smart_analyzer:
        type: custom
        tokenizer: ik_smart
mappings:
  properties:
    chunk_id:
      type: keyword
    source_id:
      type: keyword
    source_type:
      type: keyword
    req_ids:
      type: keyword
    content:
      type: text
      analyzer: ik_smart_analyzer
      search_analyzer: ik_smart_analyzer
    doc_type:
      type: keyword
    version:
      type: keyword
    updated_at:
      type: date
    chunk_index:
      type: integer
    page_title:
      type: text
      analyzer: ik_smart_analyzer
      fields:
        keyword:
          type: keyword
```

- [ ] **Step 2: 提交**

```bash
git add config/es_mapping.yaml
git commit -m "feat(config): add ES index mapping for doc search"
```

---

### Task 1.2: 分层权重配置文件

**Files:**
- Create: `config/doc_weights.yaml`

- [ ] **Step 1: 创建权重配置文件**

```yaml
# config/doc_weights.yaml
weights:
  precise:
    bm25: 0.8
    vector: 0.2
  semantic:
    bm25: 0.2
    vector: 0.8
  hybrid:
    bm25: 0.5
    vector: 0.5

rrf:
  k: 60

hyde:
  max_query_chars: 30
  min_entity_completeness: partial
  parallel: true
  target: vector_only

thresholds:
  vector_similarity_converge: 0.85
  min_results_converge: 5
  max_rounds: 3
  timeout_ms: 2000
```

- [ ] **Step 2: 提交**

```bash
git add config/doc_weights.yaml
git commit -m "feat(config): add doc weights configuration (precise/semantic/hybrid)"
```

---

### Task 1.3: RRF 融合算法

**Files:**
- Create: `src/spma/retrieval/rrf_fusion.py`
- Create: `tests/unit/retrieval/test_rrf_fusion.py`

- [ ] **Step 1: 编写失败测试**

```python
# tests/unit/retrieval/test_rrf_fusion.py
import pytest
from spma.retrieval.rrf_fusion import equal_weight_fusion, weighted_fusion


class TestEqualWeightFusion:
    def test_basic_fusion_sorts_by_rrf_score(self):
        """两个来源各 3 条结果，验证 RRF 融合排序正确。"""
        bm25_results = [
            {"chunk_id": "a", "score": 0.9},
            {"chunk_id": "b", "score": 0.7},
            {"chunk_id": "c", "score": 0.5},
        ]
        vector_results = [
            {"chunk_id": "b", "score": 0.8},
            {"chunk_id": "c", "score": 0.6},
            {"chunk_id": "d", "score": 0.4},
        ]

        fused = equal_weight_fusion(bm25_results, vector_results, top_k=4, k=60)

        # chunk "b" 在两个来源都排高位 → 应该第一
        assert fused[0]["chunk_id"] == "b"
        # 所有 chunk 排名不超出输入范围
        chunk_ids = [r["chunk_id"] for r in fused]
        assert set(chunk_ids) <= {"a", "b", "c", "d"}
        # RRF 分数降序
        scores = [r["rrf_score"] for r in fused]
        assert scores == sorted(scores, reverse=True)

    def test_fusion_respects_top_k(self):
        """验证 top_k 截断。"""
        bm25_results = [{"chunk_id": f"bm{i}", "score": 1.0 - i * 0.1} for i in range(10)]
        vector_results = [{"chunk_id": f"vec{i}", "score": 1.0 - i * 0.1} for i in range(10)]

        fused = equal_weight_fusion(bm25_results, vector_results, top_k=5, k=60)

        assert len(fused) == 5

    def test_fusion_deduplicates_by_chunk_id(self):
        """同一 chunk 出现在两边 → 合并为一条，取最佳排名。"""
        bm25_results = [
            {"chunk_id": "shared", "score": 0.9, "snippet": "bm25"},
        ]
        vector_results = [
            {"chunk_id": "shared", "score": 0.8, "snippet": "vector"},
        ]

        fused = equal_weight_fusion(bm25_results, vector_results, top_k=10, k=60)

        assert len(fused) == 1
        assert fused[0]["chunk_id"] == "shared"

    def test_empty_inputs(self):
        """空输入返回空列表。"""
        assert equal_weight_fusion([], [], top_k=10, k=60) == []
        assert equal_weight_fusion([{"chunk_id": "a", "score": 0.9}], [], top_k=10, k=60) == [
            {"chunk_id": "a", "score": 0.9, "rrf_score": pytest.approx(1 / 61, rel=1e-5)}
        ]


class TestWeightedFusion:
    def test_weighted_fusion_respects_weights(self):
        """SQL 权重 1.2 > Doc 权重 1.0 — SQL 结果排序提升。"""
        doc_results = [
            {"chunk_id": "x", "score": 0.9, "source_type": "doc", "worker_rank": 0},
            {"chunk_id": "y", "score": 0.7, "source_type": "doc", "worker_rank": 1},
        ]
        sql_results = [
            {"chunk_id": "z", "score": 0.6, "source_type": "sql", "worker_rank": 0},
        ]
        weights = {"doc": 1.0, "sql": 1.2}

        fused = weighted_fusion([doc_results, sql_results], weights=weights, top_k=10, k=60)

        # SQL 的 "z" 虽然分数低，但权重 1.2 拉高了 RRF 分
        assert len(fused) == 3
```

- [ ] **Step 2: 运行测试确认失败**

```bash
pytest tests/unit/retrieval/test_rrf_fusion.py -v
# Expected: FAIL — 模块未实现
```

- [ ] **Step 3: 实现 RRF 融合算法**

```python
# src/spma/retrieval/rrf_fusion.py
"""RRF (Reciprocal Rank Fusion) 融合算法——等权 + 加权。

等权 RRF: score(chunk) = sum(1 / (k + rank_i))  for each source i
加权 RRF: score(chunk) = sum(w_i / (k + rank_i))  for each source i

k=60 为标准选择（学界和工业界验证的最稳健常数）。
"""


def equal_weight_fusion(
    source_a: list[dict],
    source_b: list[dict],
    top_k: int = 10,
    k: int = 60,
) -> list[dict]:
    """等权 RRF 融合两个来源的检索结果。

    Args:
        source_a: 第一个来源的结果列表，每项含 chunk_id 和 score
        source_b: 第二个来源的结果列表
        top_k: 返回数量
        k: RRF 常数

    Returns:
        按 rrf_score 降序的融合结果列表
    """
    rrf_scores: dict[str, float] = {}
    best_meta: dict[str, dict] = {}

    for rank, item in enumerate(source_a):
        cid = item["chunk_id"]
        rrf_scores[cid] = rrf_scores.get(cid, 0) + 1 / (k + rank)
        if cid not in best_meta:
            best_meta[cid] = dict(item)

    for rank, item in enumerate(source_b):
        cid = item["chunk_id"]
        rrf_scores[cid] = rrf_scores.get(cid, 0) + 1 / (k + rank)
        if cid not in best_meta:
            best_meta[cid] = dict(item)

    sorted_chunks = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
    results = []
    for cid, rrf_score in sorted_chunks[:top_k]:
        entry = {"chunk_id": cid, "rrf_score": rrf_score, **best_meta[cid]}
        results.append(entry)

    return results


def weighted_fusion(
    source_groups: list[list[dict]],
    weights: dict[str, float],
    top_k: int = 10,
    k: int = 60,
) -> list[dict]:
    """加权 RRF 融合多个 Worker 来源的结果。

    Args:
        source_groups: 每个 Worker 的结果列表，每项含 source_type 和 worker_rank
        weights: {source_type: weight} 映射
        top_k: 返回数量
        k: RRF 常数

    Returns:
        按加权 rrf_score 降序的融合结果列表
    """
    rrf_scores: dict[str, float] = {}
    best_meta: dict[str, dict] = {}

    for group in source_groups:
        if not group:
            continue
        source_type = group[0].get("source_type", "unknown")
        w = weights.get(source_type, 1.0)

        for item in group:
            rank = item.get("worker_rank", 0)
            cid = item["chunk_id"]
            rrf_scores[cid] = rrf_scores.get(cid, 0) + w / (k + rank)
            if cid not in best_meta:
                best_meta[cid] = dict(item)

    sorted_chunks = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
    results = []
    for cid, rrf_score in sorted_chunks[:top_k]:
        entry = {"chunk_id": cid, "rrf_score": rrf_score, **best_meta[cid]}
        results.append(entry)

    return results
```

- [ ] **Step 4: 运行测试确认通过**

```bash
pytest tests/unit/retrieval/test_rrf_fusion.py -v
# Expected: 5 passed
```

- [ ] **Step 5: 提交**

```bash
git add src/spma/retrieval/rrf_fusion.py tests/unit/retrieval/test_rrf_fusion.py
git commit -m "feat(retrieval): implement RRF fusion (equal + weighted)"
```

---

### Task 1.4: ES 异步客户端封装

**Files:**
- Create: `src/spma/retrieval/es_client.py`

- [ ] **Step 1: 实现 ES 客户端**

```python
# src/spma/retrieval/es_client.py
"""Elasticsearch 异步客户端——BM25 文本检索。

封装索引 CRUD + 健康检查，通过 BM25Interface Protocol 适配。
设计决策: ES 为文本权威源，存储完整 chunk 文本 + 元数据。
"""

from typing import Any

from elasticsearch import AsyncElasticsearch, NotFoundError


class ESClient:
    """Elasticsearch 异步客户端，实现 BM25Interface Protocol。"""

    def __init__(
        self,
        hosts: list[str] | None = None,
        index_name: str = "spma_docs",
    ):
        hosts = hosts or ["http://localhost:9200"]
        self._client = AsyncElasticsearch(hosts)
        self.index_name = index_name

    async def search(
        self,
        query: str,
        top_k: int = 20,
        filters: dict | None = None,
    ) -> list[dict]:
        """BM25 关键词搜索。

        Args:
            query: 搜索文本
            top_k: 返回数量
            filters: 可选的 term 过滤条件，如 {"req_ids": ["REQ-187"]}

        Returns:
            [{chunk_id, source_id, source_type, req_ids, content, score, ...}, ...]
        """
        must_clauses: list[dict] = [
            {"match": {"content": query}},
        ]

        filter_clauses: list[dict] = []
        if filters:
            for field, value in filters.items():
                if isinstance(value, list):
                    filter_clauses.append({"terms": {field: value}})
                else:
                    filter_clauses.append({"term": {field: value}})

        body: dict[str, Any] = {
            "size": top_k,
            "query": {
                "bool": {
                    "must": must_clauses,
                }
            },
        }
        if filter_clauses:
            body["query"]["bool"]["filter"] = filter_clauses

        resp = await self._client.search(index=self.index_name, body=body)

        results = []
        for hit in resp["hits"]["hits"]:
            source = hit["_source"]
            source["score"] = hit["_score"]
            source["chunk_id"] = source.get("chunk_id", hit["_id"])
            results.append(source)

        return results

    async def index_chunks(self, chunks: list[dict]) -> int:
        """批量索引文档 chunk。

        Returns:
            成功索引的 chunk 数量
        """
        if not chunks:
            return 0

        operations = []
        for chunk in chunks:
            operations.append({"index": {"_index": self.index_name, "_id": chunk["chunk_id"]}})
            operations.append(chunk)

        resp = await self._client.bulk(operations=operations, refresh=True)
        return len(chunks) - len(resp.get("errors", []))

    async def delete_by_source(self, source_id: str) -> int:
        """按 source_id 删除所有关联 chunk。

        Returns:
            删除的 chunk 数量
        """
        resp = await self._client.delete_by_query(
            index=self.index_name,
            body={"query": {"term": {"source_id": source_id}}},
            refresh=True,
        )
        return resp.get("deleted", 0)

    async def get_chunks(self, chunk_ids: list[str]) -> list[dict]:
        """批量获取 chunk 完整内容（mget）。"""
        if not chunk_ids:
            return []

        docs = [{"_index": self.index_name, "_id": cid} for cid in chunk_ids]
        resp = await self._client.mget(body={"docs": docs})
        results = []
        for doc in resp["docs"]:
            if doc.get("found"):
                source = doc["_source"]
                source["chunk_id"] = source.get("chunk_id", doc["_id"])
                results.append(source)
        return results

    async def create_index(self, mapping: dict | None = None) -> None:
        """创建索引（如不存在）。"""
        exists = await self._client.indices.exists(index=self.index_name)
        if not exists:
            await self._client.indices.create(index=self.index_name, body=mapping)

    async def delete_index(self) -> None:
        """删除索引。"""
        await self._client.indices.delete(index=self.index_name, ignore=[404])

    async def health_check(self) -> bool:
        """检查 ES 集群是否可用。"""
        try:
            health = await self._client.cluster.health()
            return health.get("status") in ("green", "yellow")
        except Exception:
            return False

    async def close(self) -> None:
        """关闭连接。"""
        await self._client.close()
```

- [ ] **Step 2: 提交**

```bash
git add src/spma/retrieval/es_client.py
git commit -m "feat(retrieval): add Elasticsearch async client (BM25 search + CRUD)"
```

---

## Slice 2: 文档摄入管道

### Task 2.1: 语义分块器

**Files:**
- Create: `tests/unit/ingestion/test_chunker.py`
- Modify: `src/spma/ingestion/chunkers/semantic_chunker.py`

- [ ] **Step 1: 编写失败测试**

```python
# tests/unit/ingestion/test_chunker.py
import pytest
from spma.ingestion.chunkers.semantic_chunker import SemanticChunker, estimate_tokens


class TestSemanticChunker:
    def test_chunk_by_headers(self):
        """按 ## 标题切分文档。"""
        chunker = SemanticChunker(chunk_size_tokens=500, overlap_tokens=50)
        text = """## 第一节
这是第一节的内容，描述了用户登录模块的基本流程。

## 第二节
这是第二节的内容，描述了支付模块的设计方案。包括订单状态流转和退款机制。"""

        chunks = chunker.split(text)
        assert len(chunks) >= 2
        assert any("第一节" in c.content for c in chunks)
        assert any("第二节" in c.content for c in chunks)

    def test_chunk_preserves_metadata(self):
        """chunk 保留 source_id 和 req_ids。"""
        chunker = SemanticChunker(chunk_size_tokens=500, overlap_tokens=50)
        text = "## 登录模块\n用户输入用户名和密码后，系统验证身份。"

        chunks = chunker.split(
            text,
            source_id="confluence:123",
            source_type="confluence",
            req_ids=["REQ-001", "REQ-002"],
        )

        for chunk in chunks:
            assert chunk.source_id == "confluence:123"
            assert chunk.source_type == "confluence"
            assert set(chunk.req_ids) == {"REQ-001", "REQ-002"}

    def test_chunk_within_token_limit(self):
        """每个 chunk 不超过 chunk_size_tokens。"""
        chunker = SemanticChunker(chunk_size_tokens=500, overlap_tokens=50)
        long_text = "用户登录流程描述。\n" * 200

        chunks = chunker.split(long_text)
        for chunk in chunks:
            assert estimate_tokens(chunk.content) <= 550  # 500 + 一些余量

    def test_overlap_between_chunks(self):
        """相邻 chunk 之间有重叠。"""
        chunker = SemanticChunker(chunk_size_tokens=200, overlap_tokens=50)
        text = "。\n".join([f"这是第{i}段内容，包含一些具体的需求描述和实现细节。" for i in range(20)])

        chunks = chunker.split(text)
        if len(chunks) >= 2:
            last_sentence_of_first = chunks[0].content[-50:]
            second_chunk = chunks[1].content
            overlap_found = last_sentence_of_first[:20] in second_chunk
            assert overlap_found

    def test_short_text_returns_single_chunk(self):
        """短文本不切片，返回单 chunk。"""
        chunker = SemanticChunker(chunk_size_tokens=500, overlap_tokens=50)
        text = "这是一个简短的 PRD 描述。"

        chunks = chunker.split(text)
        assert len(chunks) == 1
        assert chunks[0].content == text

    def test_min_chunk_size_enforced(self):
        """过短的 chunk 不会单独生成。"""
        chunker = SemanticChunker(chunk_size_tokens=500, min_chunk_size_tokens=100)
        text = """## 大节
""" + "大节内容。" * 50 + """
## 小节
短内容。"""

        chunks = chunker.split(text)
        assert all(estimate_tokens(c.content) >= 100 or c.content == "短内容。" or len(chunks) == 1 for c in chunks)
```

- [ ] **Step 2: 运行测试确认失败**

```bash
pytest tests/unit/ingestion/test_chunker.py -v
# Expected: FAIL — SemanticChunker 不可用或没有 split 方法
```

- [ ] **Step 3: 实现语义分块器**

```python
# src/spma/ingestion/chunkers/semantic_chunker.py
"""递归语义分块器——按自然边界切割文档。

策略: 先按一级标题切 → 二级标题切 → 段落切 → 句子切
参数: ~500 tokens/块, 50-token overlap
分隔符优先级: \n## > \n### > \n\n > \n > 。
"""

import re
import uuid
from dataclasses import dataclass, field

import tiktoken


def estimate_tokens(text: str, model: str = "cl100k_base") -> int:
    """估算文本的 token 数量。"""
    enc = tiktoken.get_encoding(model)
    return len(enc.encode(text))


@dataclass
class DocChunk:
    chunk_id: str
    content: str
    source_id: str = ""
    source_type: str = ""
    req_ids: list[str] = field(default_factory=list)
    doc_type: str = ""
    version: str = ""
    updated_at: str = ""
    chunk_index: int = 0
    page_title: str = ""


class SemanticChunker:
    """递归语义分块器。

    按分隔符优先级递归切分: ## → ### → \n\n → \n → 。
    每个 chunk 控制在 ~500 tokens，相邻 chunk 之间 50-token overlap。
    """

    def __init__(
        self,
        chunk_size_tokens: int = 500,
        overlap_tokens: int = 50,
        min_chunk_size_tokens: int = 100,
    ):
        self.chunk_size_tokens = chunk_size_tokens
        self.overlap_tokens = overlap_tokens
        self.min_chunk_size_tokens = min_chunk_size_tokens
        self._separators = ["\n## ", "\n### ", "\n\n", "\n", "。"]

    def split(
        self,
        text: str,
        source_id: str = "",
        source_type: str = "",
        req_ids: list[str] | None = None,
        doc_type: str = "",
        version: str = "",
        updated_at: str = "",
        page_title: str = "",
    ) -> list[DocChunk]:
        """将文本切分为 chunk 列表。"""
        req_ids = req_ids or []
        if estimate_tokens(text) <= self.chunk_size_tokens:
            return [self._make_chunk(
                text, 0, source_id, source_type, req_ids,
                doc_type, version, updated_at, page_title,
            )]

        sections = self._recursive_split(text, 0)
        chunks = []
        for i, content in enumerate(sections):
            if estimate_tokens(content) < self.min_chunk_size_tokens and len(sections) > 1:
                continue
            chunks.append(self._make_chunk(
                content, i, source_id, source_type, req_ids,
                doc_type, version, updated_at, page_title,
            ))

        # 添加 overlap: 每个 chunk 末尾追加前一个 chunk 的最后 overlap_tokens
        if self.overlap_tokens > 0 and len(chunks) > 1:
            for i in range(1, len(chunks)):
                prev_end = chunks[i - 1].content[-200:]
                prefix = self._last_n_tokens(prev_end, self.overlap_tokens)
                chunks[i].content = prefix + chunks[i].content

        return chunks

    def _recursive_split(self, text: str, depth: int) -> list[str]:
        """递归按分隔符切分。"""
        if depth >= len(self._separators):
            return [text]

        sep = self._separators[depth]
        parts = text.split(sep)

        if len(parts) == 1:
            return self._recursive_split(text, depth + 1)

        result = []
        for part in parts:
            stripped = part.strip()
            if not stripped:
                continue
            if estimate_tokens(stripped) <= self.chunk_size_tokens:
                result.append(stripped)
            else:
                result.extend(self._recursive_split(stripped, depth + 1))

        return result

    def _make_chunk(self, content, index, source_id, source_type, req_ids,
                    doc_type, version, updated_at, page_title) -> DocChunk:
        return DocChunk(
            chunk_id=str(uuid.uuid4()),
            content=content,
            source_id=source_id,
            source_type=source_type,
            req_ids=list(req_ids),
            doc_type=doc_type,
            version=version,
            updated_at=updated_at,
            chunk_index=index,
            page_title=page_title,
        )

    def _last_n_tokens(self, text: str, n: int) -> str:
        enc = tiktoken.get_encoding("cl100k_base")
        tokens = enc.encode(text)
        if len(tokens) <= n:
            return text
        return enc.decode(tokens[-n:])
```

- [ ] **Step 4: 运行测试确认通过**

```bash
pytest tests/unit/ingestion/test_chunker.py -v
# Expected: 5 passed
```

- [ ] **Step 5: 提交**

```bash
git add src/spma/ingestion/chunkers/semantic_chunker.py tests/unit/ingestion/test_chunker.py
git commit -m "feat(ingestion): implement semantic chunker with recursive header/sentence splitting"
```

---

### Task 2.2: 文档摄入主流程

**Files:**
- Modify: `src/spma/ingestion/doc_pipeline.py`

- [ ] **Step 1: 实现文档摄入管道**

```python
# src/spma/ingestion/doc_pipeline.py
"""PRD 文档摄入主流程。

Parser → SemanticChunker → BGE-M3 embedding → ES + PGVector 双写
"""

import logging
from datetime import datetime, timezone

from spma.ingestion.chunkers.semantic_chunker import SemanticChunker, DocChunk
from spma.retrieval.es_client import ESClient

logger = logging.getLogger(__name__)


class DocIngestionPipeline:
    """PRD 文档摄入管道——解析→分块→嵌入→双写。"""

    def __init__(
        self,
        es_client: ESClient,
        vector_store,  # PGVector client (Phase 1 提供的接口)
        embedder,       # BGE-M3 embedding 客户端
        chunker: SemanticChunker | None = None,
    ):
        self.es = es_client
        self.vector_store = vector_store
        self.embedder = embedder
        self.chunker = chunker or SemanticChunker()

    async def ingest_document(
        self,
        text: str,
        source_id: str,
        source_type: str = "confluence",
        req_ids: list[str] | None = None,
        doc_type: str = "prd",
        version: str = "",
        page_title: str = "",
    ) -> int:
        """摄入单个文档——全流程。

        Returns:
            成功写入的 chunk 数量
        """
        chunks = self.chunker.split(
            text=text,
            source_id=source_id,
            source_type=source_type,
            req_ids=req_ids,
            doc_type=doc_type,
            version=version,
            updated_at=datetime.now(timezone.utc).isoformat(),
            page_title=page_title,
        )

        if not chunks:
            logger.warning(f"文档 {source_id} 分块后无内容")
            return 0

        chunk_dicts = [self._chunk_to_dict(c) for c in chunks]

        # 并行写入 ES + PGVector
        es_count = await self.es.index_chunks(chunk_dicts)

        try:
            embeddings = await self.embedder.embed([c.content for c in chunks])
            pg_count = await self.vector_store.upsert(
                [(c.chunk_id, emb, c.source_id) for c, emb in zip(chunks, embeddings)],
                table="chunk_embeddings",
            )
        except Exception as e:
            logger.error(f"PGVector 写入失败 (source={source_id}): {e}")
            pg_count = 0

        logger.info(
            f"摄入完成: source={source_id}, chunks={len(chunks)}, "
            f"es={es_count}, pgvector={pg_count}"
        )
        return len(chunks)

    async def update_document(
        self,
        text: str,
        source_id: str,
        source_type: str = "confluence",
        req_ids: list[str] | None = None,
        doc_type: str = "prd",
        version: str = "",
        page_title: str = "",
    ) -> int:
        """更新文档——删旧写新。"""
        deleted_es = await self.es.delete_by_source(source_id)
        deleted_pg = await self.vector_store.delete_by_source(source_id)
        logger.info(f"删除旧 chunks: es={deleted_es}, pgvector={deleted_pg}")

        return await self.ingest_document(
            text=text,
            source_id=source_id,
            source_type=source_type,
            req_ids=req_ids,
            doc_type=doc_type,
            version=version,
            page_title=page_title,
        )

    async def delete_document(self, source_id: str) -> tuple[int, int]:
        """删除文档——ES + PGVector 并行删除。"""
        deleted_es = await self.es.delete_by_source(source_id)
        deleted_pg = await self.vector_store.delete_by_source(source_id)
        return deleted_es, deleted_pg

    @staticmethod
    def _chunk_to_dict(chunk: DocChunk) -> dict:
        return {
            "chunk_id": chunk.chunk_id,
            "source_id": chunk.source_id,
            "source_type": chunk.source_type,
            "req_ids": chunk.req_ids,
            "content": chunk.content,
            "doc_type": chunk.doc_type,
            "version": chunk.version,
            "updated_at": chunk.updated_at,
            "chunk_index": chunk.chunk_index,
            "page_title": chunk.page_title,
        }
```

- [ ] **Step 2: 提交**

```bash
git add src/spma/ingestion/doc_pipeline.py
git commit -m "feat(ingestion): implement doc pipeline (parse→chunk→embed→ES+PGVector dual-write)"
```

---

## Slice 3: Doc Agent 核心实现

### Task 3.1: Doc Agent 检索模式选路

**Files:**
- Create: `tests/unit/agents/doc/test_router.py`
- Modify: `src/spma/agents/doc/retriever.py`

- [ ] **Step 1: 编写失败测试**

```python
# tests/unit/agents/doc/test_router.py
import pytest
from spma.agents.doc.retriever import route_retrieval_mode


class TestRetrievalRouter:
    def test_precise_when_req_ids_present(self):
        """req_ids 非空 → precise 模式。"""
        entities = {"req_ids": ["REQ-187"], "module": None}
        assert route_retrieval_mode(entities) == "precise"

    def test_hybrid_when_module_present_no_req_ids(self):
        """module 命中但无 req_ids → hybrid 模式。"""
        entities = {"req_ids": [], "module": "用户登录"}
        assert route_retrieval_mode(entities) == "hybrid"

    def test_semantic_when_no_entities(self):
        """无有效实体 → semantic 模式。"""
        entities = {"req_ids": [], "module": None}
        assert route_retrieval_mode(entities) == "semantic"

    def test_precise_takes_priority_over_hybrid(self):
        """req_ids 和 module 同时存在 → precise 优先。"""
        entities = {"req_ids": ["REQ-187"], "module": "支付模块"}
        assert route_retrieval_mode(entities) == "precise"
```

- [ ] **Step 2: 运行测试确认失败**

```bash
pytest tests/unit/agents/doc/test_router.py -v
# Expected: FAIL
```

- [ ] **Step 3: 实现检索模式选路**

```python
# src/spma/agents/doc/retriever.py
"""Doc Agent 混合检索——BM25 + BGE-M3 向量检索 + RRF 融合。

分层权重: precise(BM25主导) / semantic(向量主导) / hybrid(等权)
"""

from spma.models.entities import WorkerEntities


def route_retrieval_mode(entities: WorkerEntities) -> str:
    """实体驱动的检索模式选择。

    precise: req_ids 非空 → BM25 主导 + ES term query 精确过滤
    hybrid:  module 命中 → 等权混合
    semantic: 无有效实体 → 向量主导
    """
    req_ids = entities.get("req_ids", [])
    module = entities.get("module")

    if req_ids:
        return "precise"

    if module:
        return "hybrid"

    return "semantic"
```

- [ ] **Step 4: 运行测试确认通过**

```bash
pytest tests/unit/agents/doc/test_router.py -v
# Expected: 4 passed
```

- [ ] **Step 5: 提交**

```bash
git add src/spma/agents/doc/retriever.py tests/unit/agents/doc/test_router.py
git commit -m "feat(doc-agent): implement retrieval mode router (precise/hybrid/semantic)"
```

---

### Task 3.2: 3 级完备度判断

**Files:**
- Create: `tests/unit/agents/doc/test_assess.py`
- Modify: `src/spma/agents/doc/completeness.py`

- [ ] **Step 1: 编写失败测试**

```python
# tests/unit/agents/doc/test_assess.py
import pytest
from spma.agents.doc.completeness import assess_completeness, CompletenessResult


class MockLLM:
    """Mock LLM——预设响应映射。"""

    def __init__(self, responses: dict[str, str] | None = None):
        self.responses = responses or {}
        self.call_count = 0

    async def generate(self, prompt: str) -> str:
        self.call_count += 1
        for key, resp in self.responses.items():
            if key in prompt:
                return resp
        return '{"assessment": "sufficient", "reason": "信息充足"}'

    async def is_available(self) -> bool:
        return True


class TestCompletenessAssessment:
    def test_l1_deterministic_convergence(self):
        """结果 ≥ 5 AND req_ids命中 → 直接收敛，不调 LLM。"""
        results = [{"chunk_id": f"c{i}", "req_ids": ["REQ-001"]} for i in range(5)]
        llm = MockLLM()

        outcome = assess_completeness(results=results, entities={"req_ids": ["REQ-001"]}, llm=llm)

        assert outcome.verdict == "converge"
        assert outcome.level == "L1"
        assert outcome.reason == "deterministic_req_ids"
        assert llm.call_count == 0

    def test_l2_vector_threshold_convergence(self):
        """结果 ≥ 5 AND Top-3 相似度 > 0.85 → 直接收敛。"""
        results = [
            {"chunk_id": f"c{i}", "score": 0.95 - i * 0.02}
            for i in range(5)
        ]
        llm = MockLLM()

        outcome = assess_completeness(results=results, entities={"req_ids": []}, llm=llm)

        assert outcome.verdict == "converge"
        assert outcome.level == "L2"
        assert outcome.reason == "vector_threshold"
        assert llm.call_count == 0

    def test_l2_does_not_trigger_when_top3_below_threshold(self):
        """Top-3 相似度 ≤ 0.85 → 不满足 L2。"""
        results = [
            {"chunk_id": f"c{i}", "score": 0.70 - i * 0.05}
            for i in range(5)
        ]
        llm = MockLLM()

        outcome = assess_completeness(results=results, entities={"req_ids": []}, llm=llm)

        assert outcome.level != "L2" or outcome.verdict != "converge"

    def test_l3_llm_fallback_when_insufficient(self):
        """L1/L2 不满足 → Haiku 判断充足 → 收敛。"""
        results = [{"chunk_id": f"c{i}", "score": 0.60 - i * 0.1} for i in range(3)]
        llm = MockLLM(responses={
            "信息是否充足": '{"assessment": "sufficient", "reason": "虽然结果少，但覆盖了核心内容"}',
        })

        outcome = assess_completeness(results=results, entities={"req_ids": []}, llm=llm)

        assert outcome.verdict == "converge"
        assert outcome.level == "L3"
        assert llm.call_count == 1

    def test_l3_llm_judges_insufficient(self):
        """L1/L2 不满足 → Haiku 判断不足 → 需要扩展。"""
        results = [{"chunk_id": "c1", "score": 0.50}]
        llm = MockLLM(responses={
            "信息是否充足": '{"assessment": "insufficient", "reason": "只找到一条弱相关结果，缺少具体实现细节"}',
        })

        outcome = assess_completeness(results=results, entities={"req_ids": []}, llm=llm)

        assert outcome.verdict == "expand"
        assert outcome.reason == "llm_judged_insufficient"

    def test_below_min_results_triggers_expand(self):
        """结果 < 5 → 直接进入 L3，通常判定为不足。"""
        results = [{"chunk_id": "c1", "score": 0.95}]
        llm = MockLLM(responses={
            "信息是否充足": '{"assessment": "insufficient", "reason": "仅 1 条结果"}',
        })

        outcome = assess_completeness(
            results=results,
            entities={"req_ids": []},
            llm=llm,
            min_results=5,
        )

        # L1 检查: len(4) < 5 → 不触发; L2: len < 5 → 不触发; L3: LLM 判断
        assert outcome.verdict == "expand"
```

- [ ] **Step 2: 运行测试确认失败**

```bash
pytest tests/unit/agents/doc/test_assess.py -v
# Expected: FAIL
```

- [ ] **Step 3: 实现 3 级完备度判断**

```python
# src/spma/agents/doc/completeness.py
"""Doc Agent 完备度判断——3 级递进。

L1: 确定性收敛——结果≥5条 AND req_ids命中 → 自动收敛（不调LLM）
L2: 向量阈值——结果≥5条 AND Top-3相似度>0.85 → 自动收敛
L3: LLM兜底——Haiku判断是否充足

设计依据: SPMA-design-02 收敛契约
"""

import json
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class CompletenessResult:
    verdict: str          # "converge" | "expand"
    level: str            # "L1" | "L2" | "L3"
    reason: str


def assess_completeness(
    results: list[dict],
    entities: dict[str, Any],
    llm,  # LLM client (Haiku)
    min_results: int = 5,
    vector_threshold: float = 0.85,
) -> CompletenessResult:
    """3 级完备度判断。

    Args:
        results: 累计去重后的检索结果
        entities: WorkerEntities
        llm: LLM 客户端（Haiku 模型）
        min_results: L1/L2 触发所需的最小结果数
        vector_threshold: L2 向量相似度阈值

    Returns:
        CompletenessResult(verdict="converge"|"expand", level="L1"|"L2"|"L3", reason=str)
    """
    req_ids = entities.get("req_ids", [])

    # L1: 确定性收敛
    if len(results) >= min_results and req_ids:
        return CompletenessResult(
            verdict="converge",
            level="L1",
            reason="deterministic_req_ids",
        )

    # L2: 向量阈值
    if len(results) >= min_results:
        top3_scores = [r.get("score", 0) for r in results[:3]]
        avg_top3 = sum(top3_scores) / len(top3_scores) if top3_scores else 0
        if avg_top3 > vector_threshold:
            return CompletenessResult(
                verdict="converge",
                level="L2",
                reason="vector_threshold",
            )

    # L3: LLM 兜底
    verdict, reason = _llm_completeness_check(results, entities, llm)
    return CompletenessResult(
        verdict=verdict,
        level="L3",
        reason=reason,
    )


async def _llm_completeness_check(
    results: list[dict],
    entities: dict[str, Any],
    llm,
) -> tuple[str, str]:
    """调用 Haiku 判断检索结果是否充足。"""
    snippets = "\n".join(
        f"- [{r.get('chunk_id', '?')}]: {r.get('content', r.get('snippet', ''))[:200]}"
        for r in results[:10]
    )
    prompt = f"""根据以下检索结果，判断信息是否足以回答用户问题。

检索结果摘要:
{snippets}

用户可能关注的实体: {json.dumps(entities, ensure_ascii=False)}

只输出 JSON: {{"assessment": "sufficient" 或 "insufficient", "reason": "判断理由"}}"""

    try:
        resp = await llm.generate(prompt)
        data = json.loads(resp)
        assessment = data.get("assessment", "insufficient")
        if assessment == "sufficient":
            return "converge", "llm_judged_sufficient"
        else:
            return "expand", "llm_judged_insufficient"
    except Exception as e:
        logger.warning(f"LLM 完备度判断失败: {e}，默认进入扩展")
        return "expand", "llm_error_default_expand"
```

- [ ] **Step 4: 运行测试确认通过**

```bash
pytest tests/unit/agents/doc/test_assess.py -v
# Expected: 6 passed
```

- [ ] **Step 5: 提交**

```bash
git add src/spma/agents/doc/completeness.py tests/unit/agents/doc/test_assess.py
git commit -m "feat(doc-agent): implement 3-level completeness assessment (L1/L2/L3)"
```

---

### Task 3.3: 线索扩展

**Files:**
- Create: `tests/unit/agents/doc/test_clue_expander.py`
- Modify: `src/spma/agents/doc/clue_expander.py`

- [ ] **Step 1: 编写失败测试**

```python
# tests/unit/agents/doc/test_clue_expander.py
import pytest
from spma.agents.doc.clue_expander import rule_based_expand, llm_based_expand


class TestRuleBasedExpand:
    def test_extracts_new_req_ids(self):
        """从结果中提取新的 req_ids。"""
        results = [
            {"req_ids": ["REQ-001", "REQ-003"]},
            {"req_ids": ["REQ-002", "REQ-003"]},
        ]
        known_req_ids = {"REQ-001"}

        new_query = rule_based_expand(
            original_query="支付流程",
            results=results,
            known_req_ids=known_req_ids,
        )

        assert "REQ-002" in new_query or "REQ-003" in new_query

    def test_extracts_frequent_terms(self):
        """提取出现 ≥2 次的专有名词。"""
        results = [
            {"content": "支付回调接口 需要处理超时"},
            {"content": "支付回调接口 需要实现幂等"},
            {"content": "订单状态流转"},
        ]

        new_query = rule_based_expand(
            original_query="支付流程",
            results=results,
            known_req_ids=set(),
        )

        # "支付回调接口" 出现 2 次 → 应被提取
        assert "支付" in new_query

    def test_no_new_info_returns_original(self):
        """无新线索时返回原始 query。"""
        results = [{"content": "简短的描述"}]

        new_query = rule_based_expand(
            original_query="支付流程",
            results=results,
            known_req_ids=set(),
        )

        assert new_query == "支付流程"


class TestLLMBasedExpand:
    async def test_generates_expansion_queries(self):
        """LLM 生成扩展查询方向。"""
        results = [
            {"content": "支付流程包括下单和回调两个阶段"},
        ]

        class MockLLM:
            async def generate(self, prompt: str) -> str:
                return "订单状态管理\n支付异常处理\n退款流程设计"

        new_query = await llm_based_expand(
            original_query="支付流程",
            results=results,
            llm=MockLLM(),
        )

        assert "订单" in new_query or "退款" in new_query
```

- [ ] **Step 2: 运行测试确认失败**

```bash
pytest tests/unit/agents/doc/test_clue_expander.py -v
# Expected: FAIL
```

- [ ] **Step 3: 实现线索扩展**

```python
# src/spma/agents/doc/clue_expander.py
"""线索扩展——R2 规则扩展 + R3 LLM 扩展。

规则扩展(R2): ~0ms 延迟，从 Top-5 提取高频词/新req_ids/标题词
LLM扩展(R3): ~200ms 延迟，Haiku 生成 2-3 个搜索方向
"""

import re
import json
import logging
from collections import Counter

logger = logging.getLogger(__name__)


def rule_based_expand(
    original_query: str,
    results: list[dict],
    known_req_ids: set[str],
) -> str:
    """规则驱动的线索扩展（R2）。

    从 Top-5 结果中提取:
    1. 新发现的 req_ids
    2. 出现 ≥2 次的专有名词
    3. 最高层级标题词

    Returns:
        扩展后的查询字符串
    """
    expansion_terms: list[str] = []

    # 1. 提取新的 req_ids
    for r in results[:5]:
        for rid in r.get("req_ids", []):
            if rid not in known_req_ids:
                expansion_terms.append(rid)

    # 2. 高频专有名词（出现 ≥2 次）
    all_terms: list[str] = []
    for r in results[:5]:
        content = r.get("content", r.get("snippet", ""))
        words = re.findall(r'[一-鿿\w]{2,}', content)
        all_terms.extend(words)

    term_counts = Counter(all_terms)
    frequent_terms = [term for term, count in term_counts.items()
                      if count >= 2 and len(term) >= 2]
    expansion_terms.extend(frequent_terms[:5])

    # 3. 去重 + 限制
    seen = set()
    unique_terms = []
    for t in expansion_terms:
        if t.lower() not in seen and t not in original_query:
            seen.add(t.lower())
            unique_terms.append(t)

    if not unique_terms:
        return original_query

    return original_query + " " + " ".join(unique_terms[:8])


async def llm_based_expand(
    original_query: str,
    results: list[dict],
    llm,
) -> str:
    """LLM 驱动的线索扩展（R3）。

    基于累计结果 + 原始 query 生成 2-3 个搜索方向。
    """
    snippets = "\n".join(
        f"- {r.get('content', r.get('snippet', ''))[:200]}"
        for r in results[:5]
    )
    prompt = f"""根据以下检索结果和用户问题，生成 2-3 个扩展搜索方向（用换行分隔）。

用户问题: {original_query}

已有检索结果:
{snippets}

扩展搜索方向（每个方向一行，直接写关键词/短语，不用编号）:"""

    try:
        resp = await llm.generate(prompt)
        directions = [line.strip() for line in resp.strip().split("\n")
                      if line.strip() and len(line.strip()) > 2]
        expanded = original_query + " " + " ".join(directions[:3])
        return expanded
    except Exception as e:
        logger.warning(f"LLM 线索扩展失败: {e}，使用原始 query")
        return original_query
```

- [ ] **Step 4: 运行测试确认通过**

```bash
pytest tests/unit/agents/doc/test_clue_expander.py -v
# Expected: 3 passed
```

- [ ] **Step 5: 提交**

```bash
git add src/spma/agents/doc/clue_expander.py tests/unit/agents/doc/test_clue_expander.py
git commit -m "feat(doc-agent): implement clue expansion (R2 rule-based + R3 LLM-based)"
```

---

### Task 3.4: Doc Agent LangGraph 编排

**Files:**
- Modify: `src/spma/agents/doc/state.py`
- Modify: `src/spma/agents/doc/prompts.py`
- Modify: `src/spma/agents/doc/graph.py`

- [ ] **Step 1: 补全状态模型**

```python
# src/spma/agents/doc/state.py
"""Doc Agent 专属状态定义。"""

from typing import TypedDict, NotRequired


class BM25Hit(TypedDict, total=False):
    chunk_id: str
    score: float
    content: str
    source_id: str
    source_type: str
    req_ids: list[str]


class VectorHit(TypedDict, total=False):
    chunk_id: str
    score: float
    source_id: str


class FusedChunk(TypedDict, total=False):
    chunk_id: str
    rrf_score: float
    content: str
    source_id: str
    source_type: str
    req_ids: list[str]
    score: float


class DocAgentState(TypedDict, total=False):
    # 输入
    original_query: str
    entities: dict        # WorkerEntities
    max_rounds: int
    timeout_ms: int

    # 逐轮
    round: int
    current_query: str
    weight_mode: str      # "precise" | "hybrid" | "semantic"
    bm25_candidates: list[dict]
    vector_candidates: list[dict]
    fused_results: list[dict]
    accumulated_results: list[dict]

    # 完备度
    assessment: str       # "sufficient" | "insufficient"
    convergence_reason: str
    has_exact_match: bool

    # HyDE
    hyde_enabled: bool

    # 输出
    final_results: list[dict]
    rounds_used: int
    total_latency_ms: int
```

- [ ] **Step 2: 补全 Prompt 模板**

```python
# src/spma/agents/doc/prompts.py
"""Doc Agent LLM Prompt 模板。"""

COMPLETENESS_CHECK_PROMPT = """根据以下检索结果，判断信息是否足以回答用户问题。

检索结果摘要:
{snippets}

用户可能关注的实体: {entities_json}

只输出 JSON: {{"assessment": "sufficient" 或 "insufficient", "reason": "判断理由"}}"""


HYDE_PROMPT = """根据用户的问题，写一段假设性的文档内容（200-300字），模拟文档中可能如何描述相关信息。
只输出文档内容，不要标注或解释。

用户问题: {query}

假设的文档内容:"""


EXPANSION_PROMPT = """根据以下检索结果和用户问题，生成 2-3 个扩展搜索方向（用换行分隔）。

用户问题: {query}

已有检索结果:
{snippets}

扩展搜索方向（每个方向一行，直接写关键词/短语，不用编号）:"""
```

- [ ] **Step 3: 实现 Doc Agent LangGraph**

```python
# src/spma/agents/doc/graph.py
"""Doc Agent 的 LangGraph StateGraph 定义。

节点: route(检索模式选择) → search(混合检索) → aggregate(累计去重) → assess(完备度判断)
条件边: 不够 → expand(线索扩展) → 回到search / 够了 → END

设计依据: SPMA-design-02 Agent循环图
"""

import time
import asyncio
from typing import Literal

from langgraph.graph import StateGraph, END

from spma.agents.doc.state import DocAgentState
from spma.agents.doc.retriever import route_retrieval_mode
from spma.agents.doc.completeness import assess_completeness
from spma.agents.doc.clue_expander import rule_based_expand, llm_based_expand
from spma.retrieval.rrf_fusion import equal_weight_fusion


def build_doc_agent_graph(
    es_client,
    vector_store,
    embedder,
    llm,               # Haiku for completeness check
    hyde_llm=None,     # Haiku for HyDE
    weights_config: dict | None = None,
) -> StateGraph:
    """构建 Doc Agent 的 LangGraph StateGraph。

    Args:
        es_client: ES 客户端
        vector_store: PGVector 客户端
        embedder: BGE-M3 embedding 客户端
        llm: LLM 客户端（Haiku——完备度判断 + 线索扩展）
        hyde_llm: HyDE LLM 客户端（可选）
        weights_config: 分层权重配置

    Returns:
        可编译的 StateGraph
    """
    wc = weights_config or {}

    async def route_node(state: DocAgentState) -> dict:
        """检索模式选择节点。"""
        entities = state.get("entities", {})
        mode = route_retrieval_mode(entities)
        state["weight_mode"] = mode

        # HyDE 触发条件检查
        query = state.get("original_query", "")
        hyde_enabled = (
            len(query) <= 30
            and entities.get("req_ids", []) == []
            and hyde_llm is not None
        )
        state["hyde_enabled"] = hyde_enabled

        return state

    async def search_node(state: DocAgentState) -> dict:
        """混合检索节点——ES BM25 + PGVector 向量 + 可选 HyDE。"""
        query = state.get("current_query", state.get("original_query", ""))
        mode = state.get("weight_mode", "semantic")
        entities = state.get("entities", {})
        weights = wc.get("weights", {}).get(mode, {"bm25": 0.5, "vector": 0.5})

        # 构建 ES 过滤器
        es_filters = None
        if mode == "precise":
            req_ids = entities.get("req_ids", [])
            if req_ids:
                es_filters = {"req_ids": req_ids}

        # 并行: ES BM25 + PGVector 向量
        es_future = es_client.search(query, top_k=20, filters=es_filters)

        query_embedding = await embedder.embed([query])
        vector_future = vector_store.search(
            embedding=query_embedding[0],
            top_k=20,
            table="chunk_embeddings",
        )

        bm25_results, vector_results = await asyncio.gather(es_future, vector_future)

        # HyDE 向量补充检索
        hyde_results = []
        if state.get("hyde_enabled") and hyde_llm:
            try:
                hyde_text = await hyde_llm.generate(query)
                hyde_emb = await embedder.embed([hyde_text])
                hyde_results = await vector_store.search(
                    embedding=hyde_emb[0],
                    top_k=10,
                    table="chunk_embeddings",
                )
            except Exception:
                pass

        # RRF 等权融合（三路）
        all_vector = vector_results + hyde_results
        fused = equal_weight_fusion(
            source_a=bm25_results,
            source_b=all_vector,
            top_k=10,
            k=wc.get("rrf", {}).get("k", 60),
        )

        state["bm25_candidates"] = bm25_results[:20]
        state["vector_candidates"] = all_vector[:20]
        state["fused_results"] = fused

        return state

    async def aggregate_node(state: DocAgentState) -> dict:
        """累计去重——本轮 + 前轮合并，按 chunk_id 去重。"""
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
        """完备度判断节点——3 级递进。"""
        results = state.get("accumulated_results", [])
        entities = state.get("entities", {})
        thresholds = wc.get("thresholds", {})

        outcome = await assess_completeness(
            results=results,
            entities=entities,
            llm=llm,
            min_results=thresholds.get("min_results_converge", 5),
            vector_threshold=thresholds.get("vector_similarity_converge", 0.85),
        )

        state["assessment"] = outcome.verdict
        state["convergence_reason"] = f"{outcome.level}:{outcome.reason}"
        return state

    async def expand_node(state: DocAgentState) -> dict:
        """线索扩展节点——R2 规则扩展 / R3 LLM 扩展。"""
        round_num = state.get("round", 1)
        original_query = state.get("original_query", "")
        results = state.get("accumulated_results", [])
        known_req_ids = set()
        for r in results:
            for rid in r.get("req_ids", []):
                known_req_ids.add(rid)

        if round_num == 2:
            new_query = rule_based_expand(original_query, results, known_req_ids)
        else:  # round 3
            new_query = await llm_based_expand(original_query, results, llm)

        state["current_query"] = new_query
        state["round"] = round_num + 1
        return state

    def should_continue(state: DocAgentState) -> Literal["expand", "END"]:
        """条件边: 决定是否继续扩展。"""
        assessment = state.get("assessment", "sufficient")
        round_num = state.get("round", 1)
        max_rounds = state.get("max_rounds", 3)

        if assessment == "converge" or round_num >= max_rounds:
            state["rounds_used"] = round_num
            state["final_results"] = state.get("accumulated_results", [])
            return "END"

        return "expand"

    # 构建 StateGraph
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
        "assess",
        should_continue,
        {"expand": "expand", "END": END},
    )

    graph.add_edge("expand", "search")

    return graph
```

- [ ] **Step 4: 提交**

```bash
git add src/spma/agents/doc/state.py src/spma/agents/doc/prompts.py src/spma/agents/doc/graph.py
git commit -m "feat(doc-agent): implement LangGraph StateGraph (route→search→aggregate→assess→expand)"
```

---

## Slice 4: Synthesis Agent

### Task 4.1: Synthesis 加权 RRF 融合

**Files:**
- Create: `tests/unit/agents/synthesis/test_fusion.py`
- Modify: `src/spma/agents/synthesis/fusion.py`

- [ ] **Step 1: 编写失败测试**

```python
# tests/unit/agents/synthesis/test_fusion.py
import pytest
from spma.agents.synthesis.fusion import synthesize_fusion, DEFAULT_WORKER_WEIGHTS


class TestSynthesizeFusion:
    def test_sql_weighted_higher_than_doc(self):
        """SQL (1.2) > Doc (1.0) — SQL 排名提升。"""
        doc_output = {
            "citations": [
                {"chunk_id": "d1", "snippet": "doc chunk 1", "source_type": "prd"},
                {"chunk_id": "d2", "snippet": "doc chunk 2", "source_type": "prd"},
            ]
        }
        sql_output = {
            "citations": [
                {"chunk_id": "s1", "snippet": "sql result", "source_type": "sql"},
            ]
        }
        worker_outputs = [doc_output, sql_output]

        result = synthesize_fusion(worker_outputs)

        assert len(result) == 3
        assert result[0]["rrf_score"] >= result[1]["rrf_score"]

    def test_fusion_deduplicates_by_chunk_id(self):
        """同一 chunk 出现在两个 Worker → 合并。"""
        shared = {"chunk_id": "shared", "snippet": "shared doc", "source_type": "prd"}
        doc_output = {"citations": [shared]}
        sql_output = {"citations": [shared]}
        worker_outputs = [doc_output, sql_output]

        result = synthesize_fusion(worker_outputs)

        assert len(result) == 1
        assert result[0]["chunk_id"] == "shared"

    def test_empty_worker_outputs(self):
        """空 Worker 输出 → 空列表。"""
        result = synthesize_fusion([])
        assert result == []

    def test_single_worker_fallback(self):
        """只有 1 个 Worker 返回结果 → 直接返回其 citations。"""
        doc_output = {
            "citations": [
                {"chunk_id": "d1", "snippet": "only source", "source_type": "prd"},
            ]
        }
        result = synthesize_fusion([doc_output])

        assert len(result) == 1
        assert result[0]["chunk_id"] == "d1"
```

- [ ] **Step 2: 实现加权 RRF 融合**

```python
# src/spma/agents/synthesis/fusion.py
"""Synthesis Agent 加权 RRF 融合——多 Worker citations 合并排序。

SQL 权重 1.2 > Doc 1.0 = Code 1.0
"""

from spma.retrieval.rrf_fusion import weighted_fusion

DEFAULT_WORKER_WEIGHTS = {
    "doc": 1.0,
    "sql": 1.2,
    "code": 1.0,
}


def synthesize_fusion(
    worker_outputs: list[dict],
    weights: dict[str, float] | None = None,
    top_k: int = 20,
) -> list[dict]:
    """融合多个 Worker 的 citations 为统一排序列表。

    Args:
        worker_outputs: 每个 Worker 的 WorkerOutput
        weights: {source_type: weight} 映射
        top_k: 返回数量

    Returns:
        加权 RRF 融合后的 citation 列表
    """
    if weights is None:
        weights = DEFAULT_WORKER_WEIGHTS

    source_groups: list[list[dict]] = []
    for output in worker_outputs:
        citations = output.get("citations", [])
        if not citations:
            continue
        # 为每条 citation 标记 source_type 和 worker_rank
        for rank, citation in enumerate(citations):
            citation["worker_rank"] = rank
        source_groups.append(citations)

    if not source_groups:
        return []

    if len(source_groups) == 1:
        result = []
        for c in source_groups[0]:
            c["rrf_score"] = 1.0 / (1 + c.get("worker_rank", 0))
            result.append(c)
        return result[:top_k]

    return weighted_fusion(source_groups, weights=weights, top_k=top_k, k=60)
```

- [ ] **Step 3: 运行测试确认通过**

```bash
pytest tests/unit/agents/synthesis/test_fusion.py -v
# Expected: 4 passed
```

- [ ] **Step 4: 提交**

```bash
git add src/spma/agents/synthesis/fusion.py tests/unit/agents/synthesis/test_fusion.py
git commit -m "feat(synthesis): implement weighted RRF fusion for multi-worker citations"
```

---

### Task 4.2: Synthesis 自检逻辑

**Files:**
- Create: `tests/unit/agents/synthesis/test_audit.py`
- Modify: `src/spma/agents/synthesis/auditor.py`

- [ ] **Step 1: 编写失败测试**

```python
# tests/unit/agents/synthesis/test_audit.py
import pytest
from spma.agents.synthesis.auditor import audit_answer, AuditResult


class MockLLM:
    def __init__(self, json_response: str):
        self.json_response = json_response

    async def generate(self, prompt: str) -> str:
        return self.json_response


class TestAuditAnswer:
    async def test_pass_when_all_checks_ok(self):
        """引用覆盖率 ≥ 80% AND 无矛盾 → PASS。"""
        llm = MockLLM(json_response='''{
            "citation_coverage": 0.90,
            "unverified_claims": [],
            "contradictions": [],
            "coverage_gaps": [],
            "verdict": "pass"
        }''')

        result = await audit_answer(
            draft_answer="用户登录需要用户名密码。[PRD §2.1]",
            original_query="登录流程",
            fused_citations=[{"chunk_id": "c1", "snippet": "登录流程描述"}],
            llm=llm,
        )

        assert result.verdict == "pass"

    async def test_fix_when_low_coverage_no_contradiction(self):
        """引用覆盖率 < 80% → fix。"""
        llm = MockLLM(json_response='''{
            "citation_coverage": 0.55,
            "unverified_claims": ["支付流程的具体步骤缺少引用"],
            "contradictions": [],
            "coverage_gaps": [],
            "verdict": "fix"
        }''')

        result = await audit_answer(
            draft_answer="支付流程包含多个步骤。[PRD §3.1]",
            original_query="支付流程",
            fused_citations=[{"chunk_id": "c1", "snippet": "支付流程概述"}],
            llm=llm,
        )

        assert result.verdict == "fix"

    async def test_contradiction_detected(self):
        """跨源矛盾 → contradiction。"""
        llm = MockLLM(json_response='''{
            "citation_coverage": 0.85,
            "unverified_claims": [],
            "contradictions": [{
                "claim_a": "Doc 说支付需要 3 步",
                "claim_b": "SQL 显示只有 2 步",
                "source_a": "doc",
                "source_b": "sql"
            }],
            "coverage_gaps": [],
            "verdict": "contradiction"
        }''')

        result = await audit_answer(
            draft_answer="支付流程有 3 步。[PRD §2.1] 数据库显示只有 2 步。[SQL:orders]",
            original_query="支付流程",
            fused_citations=[
                {"chunk_id": "c1", "snippet": "3 步支付流程", "source_type": "prd"},
                {"chunk_id": "c2", "snippet": "orders 表 2 步", "source_type": "sql"},
            ],
            llm=llm,
        )

        assert result.verdict == "contradiction"
        assert len(result.contradictions) == 1

    async def test_gap_detected(self):
        """覆盖缺口 → gap。"""
        llm = MockLLM(json_response='''{
            "citation_coverage": 0.80,
            "unverified_claims": [],
            "contradictions": [],
            "coverage_gaps": ["退款流程未被回答"],
            "verdict": "gap"
        }''')

        result = await audit_answer(
            draft_answer="支付流程包括下单和回调。[PRD §2.1]",
            original_query="支付流程包括下单、回调和退款",
            fused_citations=[{"chunk_id": "c1", "snippet": "下单和回调"}],
            llm=llm,
        )

        assert result.verdict == "gap"
        assert "退款" in str(result.coverage_gaps)
```

- [ ] **Step 2: 实现自检逻辑**

```python
# src/spma/agents/synthesis/auditor.py
"""Synthesis Auditor——引用完整性 + 跨源一致性 + 问题覆盖度检查。

分级处理:
- PASS: 引用覆盖率 ≥ 80% AND 无矛盾 AND 无覆盖缺口
- fix: 引用覆盖率 < 80% → 修正一次
- contradiction: 跨源矛盾 → 标注通过
- gap: 覆盖缺口 → 标注通过

设计依据: API-04 §7.2 自检Prompt
"""

import json
import logging
from dataclasses import dataclass, field

from spma.agents.synthesis.prompts import AUDIT_PROMPT

logger = logging.getLogger(__name__)


@dataclass
class AuditResult:
    verdict: str                      # "pass" | "fix" | "contradiction" | "gap"
    citation_coverage: float = 0.0    # 0-1
    unverified_claims: list[str] = field(default_factory=list)
    contradictions: list[dict] = field(default_factory=list)
    coverage_gaps: list[str] = field(default_factory=list)


async def audit_answer(
    draft_answer: str,
    original_query: str,
    fused_citations: list[dict],
    llm,
) -> AuditResult:
    """对 Synthesis 初稿进行自检。

    Args:
        draft_answer: LLM 生成的 Markdown 初稿
        original_query: 用户原始问题
        fused_citations: RRF 融合后的引用列表
        llm: LLM 客户端（Haiku/Sonnet）

    Returns:
        AuditResult(verdict, citation_coverage, contradictions, coverage_gaps)
    """
    if not fused_citations:
        return AuditResult(
            verdict="fix",
            citation_coverage=0.0,
            unverified_claims=["无检索结果支撑，全部陈述缺乏引用"],
        )

    prompt = AUDIT_PROMPT.format(
        audit_target=draft_answer,
        original_query=original_query,
    )

    try:
        resp = await llm.generate(prompt)
        data = json.loads(resp)

        result = AuditResult(
            verdict=data.get("verdict", "fix"),
            citation_coverage=data.get("citation_coverage", 0.0),
            unverified_claims=data.get("unverified_claims", []),
            contradictions=data.get("contradictions", []),
            coverage_gaps=data.get("coverage_gaps", []),
        )

        # 规则兜底: 如果 citation_coverage ≥ 0.8 但 LLM 判 fix，可能来自 gap/contradiction
        if result.citation_coverage >= 0.8 and result.verdict == "fix":
            if result.coverage_gaps:
                result.verdict = "gap"
            elif result.contradictions:
                result.verdict = "contradiction"

        return result

    except Exception as e:
        logger.warning(f"自检 LLM 调用失败: {e}，默认 pass（降级）")
        return AuditResult(
            verdict="pass",
            citation_coverage=0.5,
            unverified_claims=["自检 LLM 调用失败，引用覆盖度未完整验证"],
        )
```

- [ ] **Step 3: 运行测试确认通过**

```bash
pytest tests/unit/agents/synthesis/test_audit.py -v
# Expected: 4 passed
```

- [ ] **Step 4: 补全 Synthesis Prompt 模板**

```python
# src/spma/agents/synthesis/prompts.py
"""Synthesis Agent LLM Prompt 模板。"""

GENERATION_PROMPT = """你是一个企业知识助手。根据以下检索结果，回答用户问题。

用户问题: {original_query}

检索结果:
{doc_results}
{sql_results}

要求:
1. 用 Markdown 格式组织回答
2. 每条陈述必须标注引用来源 [源类型: 标识符]
3. 区分"确定的事实"和"推测的结论"
4. 如果跨源信息存在矛盾，显式标注
5. 如果有未能回答的部分，在末尾列出"""


AUDIT_PROMPT = """你是一个严谨的审计员。检查刚才生成的回答:
{audit_target}

检查项目:
1. 引用完整性: 每条陈述都有引用支撑吗？
2. 跨源一致性: Doc/SQL 的信息有矛盾吗？
3. 覆盖度: 用户原始问题 "{original_query}" 的每个方面都被回答了吗？

输出 JSON:
{{
  "citation_coverage": 0.xx,
  "unverified_claims": ["陈述1缺少引用", ...],
  "contradictions": [{{"claim_a": "...", "claim_b": "...", "source_a": "...", "source_b": "..."}}],
  "coverage_gaps": ["未回答的方面", ...],
  "verdict": "pass" | "fix" | "contradiction" | "gap"
}}"""
```

- [ ] **Step 5: 提交**

```bash
git add src/spma/agents/synthesis/auditor.py src/spma/agents/synthesis/prompts.py tests/unit/agents/synthesis/test_audit.py
git commit -m "feat(synthesis): implement audit logic with tiered handling (pass/fix/contradiction/gap)"
```

---

### Task 4.3: 透明度标注 + Synthesis LangGraph

**Files:**
- Modify: `src/spma/agents/synthesis/transparency.py`
- Modify: `src/spma/agents/synthesis/generator.py`
- Modify: `src/spma/agents/synthesis/state.py`
- Modify: `src/spma/agents/synthesis/graph.py`

- [ ] **Step 1: 实现透明度标注**

```python
# src/spma/agents/synthesis/transparency.py
"""透明度标注生成。

标注类型:
- worker_timeout: ⏱️ 部分Worker超时
- worker_failure: ⚠️ 仅基于[源类型]结果
- unverified: ❌ 引用未验证
- contradiction: ⚡ 跨源矛盾
- coverage_gap: ❓ 方面未回答
- token_exhausted: 📊 Token预算耗尽
"""

from typing import TypedDict


class TransparencyAnnotation(TypedDict):
    level: str        # "warning" | "error" | "info"
    icon: str         # emoji
    message: str
    details: str | None


def generate_transparency_annotations(
    audit_result,               # AuditResult
    worker_failures: list[str],  # 失败的 Worker 类型
    token_exhausted: bool = False,
) -> list[TransparencyAnnotation]:
    """生成透明度标注列表。"""
    annotations: list[TransparencyAnnotation] = []

    for worker_type in worker_failures:
        annotations.append({
            "level": "warning",
            "icon": "⚠️",
            "message": f"仅基于{worker_type}结果",
            "details": f"{worker_type} Agent 未能返回结果，回答仅基于部分来源",
        })

    if audit_result.unverified_claims:
        annotations.append({
            "level": "warning",
            "icon": "❌",
            "message": "引用未验证",
            "details": f"{len(audit_result.unverified_claims)} 条陈述缺少引用支撑",
        })

    for contradiction in audit_result.contradictions:
        annotations.append({
            "level": "error",
            "icon": "⚡",
            "message": "跨源矛盾",
            "details": f"{contradiction.get('claim_a', '?')} vs {contradiction.get('claim_b', '?')}",
        })

    if audit_result.coverage_gaps:
        annotations.append({
            "level": "info",
            "icon": "❓",
            "message": "方面未回答",
            "details": "; ".join(audit_result.coverage_gaps),
        })

    if token_exhausted:
        annotations.append({
            "level": "warning",
            "icon": "📊",
            "message": "Token预算耗尽",
            "details": "生成因 Token 预算限制而截断",
        })

    return annotations
```

- [ ] **Step 2: 实现 LLM 生成初稿**

```python
# src/spma/agents/synthesis/generator.py
"""LLM 生成初稿——Sonnet 根据融合结果 + 用户问题生成 Markdown 回答。"""

from spma.agents.synthesis.prompts import GENERATION_PROMPT


async def generate_draft_answer(
    original_query: str,
    fused_citations: list[dict],
    worker_outputs: list[dict],
    llm,  # Sonnet
) -> str:
    """生成 Markdown 初稿（含引用标注）。

    Args:
        original_query: 用户原始问题
        fused_citations: RRF 融合后的引用列表
        worker_outputs: 原始 Worker 输出列表
        llm: LLM 客户端（Sonnet）

    Returns:
        Markdown 格式的回答
    """
    doc_results = _format_results(
        [c for c in fused_citations if c.get("source_type") == "prd"],
        "文档"
    )
    sql_results = _format_results(
        [c for c in fused_citations if c.get("source_type") == "sql"],
        "数据库"
    )

    prompt = GENERATION_PROMPT.format(
        original_query=original_query,
        doc_results=doc_results,
        sql_results=sql_results,
    )

    return await llm.generate(prompt)


def _format_results(citations: list[dict], label: str) -> str:
    if not citations:
        return f"[来自{label}] 无结果"

    lines = [f"[来自{label}]"]
    for i, c in enumerate(citations):
        snippet = c.get("snippet", c.get("content", ""))[:300]
        source_id = c.get("source_id", c.get("chunk_id", "?"))
        lines.append(f"{i + 1}. [{source_id}] {snippet}")
    return "\n".join(lines)
```

- [ ] **Step 3: 补全 Synthesis 状态模型 + LangGraph**

```python
# src/spma/agents/synthesis/state.py
"""Synthesis Agent 专属状态定义。"""

from typing import TypedDict, NotRequired


class SynthesisAgentState(TypedDict, total=False):
    # 输入
    original_query: str
    worker_outputs: list           # List[WorkerOutput]
    max_rounds: int                # 默认 2
    timeout_ms: int                # 默认 2000

    # Round 1: 生成
    fused_citations: list[dict]
    draft_answer: str

    # Round 2: 审计
    audit_result: dict             # AuditResult as dict
    citation_coverage: float
    contradictions: list[dict]
    coverage_gaps: list[str]

    # 透明度
    annotations: list[dict]

    # 输出
    final_answer: str
    convergence_reason: str
    total_latency_ms: int
```

```python
# src/spma/agents/synthesis/graph.py
"""Synthesis Agent 的 LangGraph StateGraph 定义。

节点: fuse(加权RRF融合) → generate(LLM生成) → audit(自检)
条件边: fix→generate / pass→END / contradiction→END / gap→END

设计依据: API-04 Synthesis Agent
"""

from typing import Literal

from langgraph.graph import StateGraph, END

from spma.agents.synthesis.state import SynthesisAgentState
from spma.agents.synthesis.fusion import synthesize_fusion
from spma.agents.synthesis.generator import generate_draft_answer
from spma.agents.synthesis.auditor import audit_answer
from spma.agents.synthesis.transparency import generate_transparency_annotations


def build_synthesis_agent_graph(
    llm,       # Sonnet for generation
    audit_llm,  # Haiku for audit
) -> StateGraph:
    """构建 Synthesis Agent 的 LangGraph StateGraph。"""

    async def fuse_node(state: SynthesisAgentState) -> dict:
        """加权 RRF 融合——合并多 Worker citations。"""
        worker_outputs = state.get("worker_outputs", [])
        fused = synthesize_fusion(worker_outputs)
        state["fused_citations"] = fused
        return state

    async def generate_node(state: SynthesisAgentState) -> dict:
        """LLM 生成初稿。"""
        draft = await generate_draft_answer(
            original_query=state.get("original_query", ""),
            fused_citations=state.get("fused_citations", []),
            worker_outputs=state.get("worker_outputs", []),
            llm=llm,
        )
        state["draft_answer"] = draft
        return state

    async def audit_node(state: SynthesisAgentState) -> dict:
        """自检——引用完整性 + 跨源一致性 + 覆盖度。"""
        result = await audit_answer(
            draft_answer=state.get("draft_answer", ""),
            original_query=state.get("original_query", ""),
            fused_citations=state.get("fused_citations", []),
            llm=audit_llm,
        )

        state["audit_result"] = {
            "verdict": result.verdict,
            "citation_coverage": result.citation_coverage,
            "contradictions": result.contradictions,
            "coverage_gaps": result.coverage_gaps,
            "unverified_claims": result.unverified_claims,
        }
        state["citation_coverage"] = result.citation_coverage
        state["contradictions"] = result.contradictions
        state["coverage_gaps"] = result.coverage_gaps

        # 生成透明度标注
        worker_failures = [
            str(w.get("worker_type", "unknown"))
            for w in state.get("worker_outputs", [])
            if not w.get("citations")
        ]
        annotations = generate_transparency_annotations(
            audit_result=result,
            worker_failures=worker_failures,
        )
        state["annotations"] = annotations

        return state

    def should_continue(state: SynthesisAgentState) -> Literal["generate", "END"]:
        """条件边: fix → 修正一次 / 其他 → END。"""
        round_num = state.get("round", 1)
        max_rounds = state.get("max_rounds", 2)
        verdict = state.get("audit_result", {}).get("verdict", "pass")

        if verdict == "fix" and round_num < max_rounds:
            state["round"] = round_num + 1
            # 在修正轮次中，带上审计反馈
            state["convergence_reason"] = "retry_fix"
            return "generate"

        # 构建终稿（含标注）
        draft = state.get("draft_answer", "")
        annotations = state.get("annotations", [])
        annotation_text = "\n\n---\n" + "\n".join(
            f"{a['icon']} **{a['message']}**: {a.get('details', '')}"
            for a in annotations
        ) if annotations else ""
        state["final_answer"] = draft + annotation_text
        state["convergence_reason"] = verdict
        return "END"

    graph = StateGraph(SynthesisAgentState)

    graph.add_node("fuse", fuse_node)
    graph.add_node("generate", generate_node)
    graph.add_node("audit", audit_node)

    graph.set_entry_point("fuse")
    graph.add_edge("fuse", "generate")
    graph.add_edge("generate", "audit")

    graph.add_conditional_edges(
        "audit",
        should_continue,
        {"generate": "generate", "END": END},
    )

    return graph
```

- [ ] **Step 4: 提交**

```bash
git add src/spma/agents/synthesis/state.py src/spma/agents/synthesis/graph.py src/spma/agents/synthesis/generator.py src/spma/agents/synthesis/transparency.py
git commit -m "feat(synthesis): implement LangGraph StateGraph (fuse→generate→audit) + transparency annotations"
```

---

## Slice 5: Redis 状态存储与降级

### Task 5.1: Redis 状态存储

**Files:**
- Create: `tests/unit/infrastructure/test_state_store.py`
- Modify: `src/spma/infrastructure/state_store.py`

- [ ] **Step 1: 编写失败测试**

```python
# tests/unit/infrastructure/test_state_store.py
import pytest
import time
from spma.infrastructure.state_store import RedisStateStore, InMemoryStateStore


class FakeRedis:
    """Fake Redis——内存 dict 模拟读写。"""
    def __init__(self):
        self._store: dict[str, bytes] = {}
        self._ttl: dict[str, float] = {}
        self.healthy = True

    async def get(self, key: str) -> bytes | None:
        exp = self._ttl.get(key, float("inf"))
        if time.time() > exp:
            self._store.pop(key, None)
            return None
        return self._store.get(key)

    async def setex(self, key: str, ttl: int, value: bytes) -> None:
        self._store[key] = value
        self._ttl[key] = time.time() + ttl

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)
        self._ttl.pop(key, None)

    async def ping(self) -> bool:
        return self.healthy

    async def close(self) -> None:
        pass


class TestRedisStateStore:
    async def test_save_and_load_round_trip(self):
        """保存状态后能正确读取。"""
        redis = FakeRedis()
        store = RedisStateStore(redis_client=redis)

        state = {"round": 2, "accumulated_chunk_ids": ["uuid-abc", "uuid-def"]}
        key = "agent:user-001:sess-abc:qry-xyz:doc:state"

        await store.save(key, state, ttl=300)
        loaded = await store.load(key)

        assert loaded == state

    async def test_load_expired_returns_none(self):
        """TTL 过期后返回 None。"""
        redis = FakeRedis()
        store = RedisStateStore(redis_client=redis)

        key = "agent:user-001:sess-abc:qry-xyz:doc:state"
        await store.save(key, {"round": 1}, ttl=0)  # 立即过期

        time.sleep(0.1)
        loaded = await store.load(key)

        assert loaded is None

    async def test_delete_removes_state(self):
        """删除后加载返回 None。"""
        redis = FakeRedis()
        store = RedisStateStore(redis_client=redis)

        key = "agent:user-001:sess-abc:qry-xyz:doc:state"
        await store.save(key, {"round": 1}, ttl=300)
        await store.delete(key)
        loaded = await store.load(key)

        assert loaded is None


class TestInMemoryDegradation:
    async def test_fallback_when_redis_unavailable(self):
        """Redis 不可用 → 降级到 InMemory。"""
        redis = FakeRedis()
        redis.healthy = False
        store = RedisStateStore(redis_client=redis)

        health = await store.health_check()
        assert health is False

        # 降级到内存存储
        mem_store = InMemoryStateStore()
        await mem_store.save("test_key", {"data": "fallback"})
        loaded = await mem_store.load("test_key")

        assert loaded == {"data": "fallback"}

    async def test_in_memory_no_persistence(self):
        """进程内存不跨查询共享。"""
        store_a = InMemoryStateStore()
        store_b = InMemoryStateStore()

        await store_a.save("key", {"from": "store_a"})
        loaded = await store_b.load("key")

        assert loaded is None
```

- [ ] **Step 2: 实现 Redis 状态存储 + 降级**

```python
# src/spma/infrastructure/state_store.py
"""三层状态存储——进程内存 → Redis热状态 → PostgreSQL冷trace。

Layer 1: ProcessMemoryStore (Phase 1, Python dict, 无外部依赖)
Layer 2: RedisHotStore (Phase 2+, Write-through, TTL=5min)
Layer 3: PostgresColdStore (Phase 3+, Write-back, 异步写入)

降级: Redis 不可用 → 自动降级到进程内存，标注 degradation level

设计依据: SPMA-design-06 §2 Checkpointer隔离 + SPMA-design-07 §5 状态管理
"""

import json
import time
import logging
import uuid
from typing import Protocol

logger = logging.getLogger(__name__)


class StateStorageProtocol(Protocol):
    """状态存储的抽象接口——三层实现共用。"""
    async def save(self, key: str, state: dict, ttl: int | None = None) -> None: ...
    async def load(self, key: str) -> dict | None: ...
    async def delete(self, key: str) -> None: ...
    async def health_check(self) -> bool: ...


class InMemoryStateStore:
    """进程内存状态存储——降级时使用。"""

    def __init__(self):
        self._store: dict[str, tuple[dict, float]] = {}

    async def save(self, key: str, state: dict, ttl: int = 300) -> None:
        expires_at = time.time() + ttl
        self._store[key] = (state, expires_at)

    async def load(self, key: str) -> dict | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        state, expires_at = entry
        if time.time() > expires_at:
            del self._store[key]
            return None
        return state

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)

    async def health_check(self) -> bool:
        return True


class RedisStateStore:
    """Redis 热状态存储——Write-through, TTL=5min。"""

    def __init__(self, redis_client, default_ttl: int = 300):
        self._redis = redis_client
        self.default_ttl = default_ttl
        self._fallback = InMemoryStateStore()
        self._degraded = False

    async def save(self, key: str, state: dict, ttl: int | None = None) -> None:
        ttl = ttl or self.default_ttl
        value = json.dumps(state, ensure_ascii=False)
        try:
            await self._redis.setex(key, ttl, value.encode("utf-8"))
        except Exception as e:
            logger.warning(f"Redis 保存失败 ({key}): {e}，降级到内存")
            self._degraded = True
            await self._fallback.save(key, state, ttl)

    async def load(self, key: str) -> dict | None:
        try:
            raw = await self._redis.get(key)
            if raw is None:
                return None
            return json.loads(raw)
        except Exception as e:
            logger.warning(f"Redis 加载失败 ({key}): {e}，尝试内存降级")
            self._degraded = True
            return await self._fallback.load(key)

    async def delete(self, key: str) -> None:
        try:
            await self._redis.delete(key)
        except Exception as e:
            logger.warning(f"Redis 删除失败 ({key}): {e}")
        await self._fallback.delete(key)

    async def health_check(self) -> bool:
        try:
            if await self._redis.ping():
                if self._degraded:
                    logger.info("Redis 已恢复，切回 Redis 主存储")
                    self._degraded = False
                return True
        except Exception:
            pass
        self._degraded = True
        return False

    @property
    def is_degraded(self) -> bool:
        return self._degraded


# ============================================================
# 确认闸门 Token 存储（从 Phase 1 迁移到 Phase 2 Redis）
# ============================================================

class ConfirmationTokenStore:
    """确认闸门的 token → state 映射存储。

    Phase 2: 迁移到 RedisStateStore。
    """

    def __init__(self, state_store: RedisStateStore | None = None):
        self._store = state_store or InMemoryStateStore()

    async def save(self, state: dict, ttl_seconds: int = 180) -> str:
        token = f"tok_{uuid.uuid4().hex[:12]}"
        await self._store.save(
            f"confirmation:{token}",
            {"state": state, "expires_at": time.time() + ttl_seconds},
            ttl=ttl_seconds,
        )
        return token

    async def load(self, token: str) -> dict | None:
        return await self._store.load(f"confirmation:{token}")

    async def delete(self, token: str) -> None:
        await self._store.delete(f"confirmation:{token}")
```

- [ ] **Step 3: 运行测试确认通过**

```bash
pytest tests/unit/infrastructure/test_state_store.py -v
# Expected: 4 passed
```

- [ ] **Step 4: 提交**

```bash
git add src/spma/infrastructure/state_store.py tests/unit/infrastructure/test_state_store.py
git commit -m "feat(infra): implement Redis state store with InMemory fallback degradation"
```

---

## Slice 6: 检索日志

### Task 6.1: 检索日志实现

**Files:**
- Modify: `src/spma/models/search_log.py`
- Modify: `src/spma/retrieval/search_logger.py`

- [ ] **Step 1: 补全检索日志数据模型**

```python
# src/spma/models/search_log.py
"""检索日志数据结构。

设计依据: SPMA-design-02 §1.5.3 埋点日志结构
"""

from typing import TypedDict, NotRequired


class SearchLogEntry(TypedDict, total=False):
    log_id: str
    timestamp: str
    worker_type: str              # "doc" | "sql" | "code"
    worker_version: str
    query_id: str
    query_text: str
    query_type: str               # "precise" | "hybrid" | "semantic"
    trigger: str                  # "user" | "webhook" | "scheduler"
    entities: dict                # ExtractedEntities
    agent_rounds: int
    convergence_reason: str
    bm25_candidates: list[dict]   # ES Top-20, [{chunk_id, score, snippet}]
    vector_candidates: list[dict] # PGVector Top-20
    rrf_fused: list[dict]         # RRF Top-10
    latency_ms: int
    feedback: dict | None         # 异步填充的用户反馈
```

- [ ] **Step 2: 实现检索日志异步写入**

```python
# src/spma/retrieval/search_logger.py
"""检索日志——异步写入 PostgreSQL search_logs 表。

设计原则: 异步写入不阻塞检索主链路; 只记录 Top-20 + Top-10 摘要。
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class SearchLogger:
    """异步检索日志记录器。"""

    def __init__(self, db_pool=None):
        self._db_pool = db_pool  # asyncpg pool or similar
        self._queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=1000)
        self._worker_task: asyncio.Task | None = None

    async def start(self) -> None:
        """启动后台写入 worker。"""
        self._worker_task = asyncio.create_task(self._log_worker())

    async def stop(self) -> None:
        """停止后台 worker。"""
        if self._worker_task:
            self._worker_task.cancel()
        # 清空剩余队列
        while not self._queue.empty():
            entry = self._queue.get_nowait()
            await self._write_to_db(entry)

    async def log(self, entry_data: dict[str, Any]) -> None:
        """记录一条检索日志（异步入队）。"""
        entry: dict[str, Any] = {
            "log_id": str(uuid.uuid4()),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "worker_type": entry_data.get("worker_type", "doc"),
            "worker_version": entry_data.get("worker_version", "0.1"),
            "query_id": entry_data.get("query_id", ""),
            "query_text": entry_data.get("query_text", ""),
            "query_type": entry_data.get("query_type", "hybrid"),
            "trigger": entry_data.get("trigger", "user"),
            "entities": entry_data.get("entities", {}),
            "agent_rounds": entry_data.get("agent_rounds", 1),
            "convergence_reason": entry_data.get("convergence_reason", ""),
            "bm25_candidates": _extract_summary(entry_data.get("bm25_candidates", []), 20),
            "vector_candidates": _extract_summary(entry_data.get("vector_candidates", []), 20),
            "rrf_fused": _extract_summary(entry_data.get("rrf_fused", []), 10),
            "latency_ms": entry_data.get("latency_ms", 0),
            "feedback": entry_data.get("feedback", None),
        }

        try:
            self._queue.put_nowait(entry)
        except asyncio.QueueFull:
            logger.warning("检索日志队列已满 (1000)，丢弃一条日志")

    async def _log_worker(self) -> None:
        """后台 worker——从队列取日志写入 DB。"""
        while True:
            try:
                entry = await asyncio.wait_for(self._queue.get(), timeout=5.0)
                await self._write_to_db(entry)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"日志写入失败: {e}")

    async def _write_to_db(self, entry: dict) -> None:
        """写入 PostgreSQL（如果 db_pool 可用）。"""
        if self._db_pool is None:
            # 无 DB 连接时至少打印到 stdout
            logger.info(f"SEARCH_LOG: {json.dumps(entry, ensure_ascii=False)[:500]}")
            return

        try:
            async with self._db_pool.acquire() as conn:
                await conn.execute(
                    """INSERT INTO search_logs (log_id, timestamp, worker_type, query_text,
                       query_type, agent_rounds, convergence_reason, bm25_candidates,
                       vector_candidates, rrf_fused, latency_ms, entities)
                       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)""",
                    entry["log_id"],
                    entry["timestamp"],
                    entry["worker_type"],
                    entry["query_text"],
                    entry["query_type"],
                    entry["agent_rounds"],
                    entry["convergence_reason"],
                    json.dumps(entry["bm25_candidates"], ensure_ascii=False),
                    json.dumps(entry["vector_candidates"], ensure_ascii=False),
                    json.dumps(entry["rrf_fused"], ensure_ascii=False),
                    entry["latency_ms"],
                    json.dumps(entry["entities"], ensure_ascii=False),
                )
        except Exception as e:
            logger.error(f"DB 日志写入失败: {e}")


def _extract_summary(candidates: list[dict], max_count: int) -> list[dict]:
    """提取候选人摘要——只保留 chunk_id, score, snippet 的前 200 字符。"""
    return [
        {
            "chunk_id": c.get("chunk_id", ""),
            "score": c.get("score", 0),
            "snippet": str(c.get("snippet", c.get("content", "")))[:200],
        }
        for c in candidates[:max_count]
    ]
```

- [ ] **Step 3: 提交**

```bash
git add src/spma/models/search_log.py src/spma/retrieval/search_logger.py
git commit -m "feat(observability): implement search logger with async DB write queue"
```

---

## Slice 7: E2E 测试 + RAG 评估

### Task 7.1: Doc Agent E2E + 跨源 E2E

**Files:**
- Create: `tests/e2e/test_doc_single_source.py`
- Modify: `tests/e2e/test_cross_source.py`

- [ ] **Step 1: 编写 Doc Agent 单源 E2E 测试**

```python
# tests/e2e/test_doc_single_source.py
"""Doc Agent 单源 E2E 测试——需要真实 ES + PGVector 服务。"""

import pytest


@pytest.mark.e2e
class TestDocAgentE2E:
    async def test_precise_search_by_req_id(self, doc_agent, test_es_client):
        """通过 req_id 精确检索——100% 命中。"""
        # 前置: 索引已知 req_id 的测试文档
        await test_es_client.index_chunks([
            {
                "chunk_id": "test-req-001",
                "source_id": "confluence:test-001",
                "source_type": "confluence",
                "req_ids": ["REQ-TEST-001"],
                "content": "## 用户登录模块\n用户登录需要用户名和密码。[REQ-TEST-001]",
                "doc_type": "prd",
                "version": "v1.0",
                "updated_at": "2026-06-01T00:00:00Z",
                "chunk_index": 0,
                "page_title": "登录模块 PRD",
            }
        ])

        result = await doc_agent.search(
            query="REQ-TEST-001",
            entities={"req_ids": ["REQ-TEST-001"]},
        )

        assert result["has_exact_match"] is True
        assert len(result["final_results"]) >= 1
        found = False
        for r in result["final_results"]:
            if "REQ-TEST-001" in str(r.get("req_ids", [])):
                found = True
        assert found

    async def test_semantic_search_short_query(self, doc_agent, test_es_client):
        """短语义查询——应返回相关结果。"""
        await test_es_client.index_chunks([
            {
                "chunk_id": "test-sem-001",
                "source_id": "confluence:test-001",
                "source_type": "confluence",
                "req_ids": [],
                "content": "## 支付流程\n支付流程包括用户下单、第三方支付回调、订单状态更新三个步骤。",
                "doc_type": "prd",
                "version": "v1.0",
                "updated_at": "2026-06-01T00:00:00Z",
                "chunk_index": 0,
                "page_title": "支付流程 PRD",
            }
        ])

        result = await doc_agent.search(
            query="支付流程",
            entities={"req_ids": [], "module": None},
        )

        assert len(result["final_results"]) >= 1
```

- [ ] **Step 2: 编写跨源 E2E 测试**

```python
# tests/e2e/test_cross_source.py
"""Doc + SQL → Synthesis 跨源 E2E 测试。"""

import pytest


@pytest.mark.e2e
class TestCrossSourceE2E:
    async def test_doc_sql_synthesis_basic(self, synthesis_agent, test_es_client, test_vector_store):
        """Doc + SQL 结果 → Synthesis 生成融合回答。"""
        # 前置: Doc 结果
        await test_es_client.index_chunks([
            {
                "chunk_id": "cross-doc-001",
                "source_id": "confluence:test-001",
                "source_type": "confluence",
                "req_ids": ["REQ-CROSS-001"],
                "content": "用户登录模块支持用户名密码和手机验证码两种方式。",
                "doc_type": "prd",
                "version": "v1.0",
                "updated_at": "2026-06-01T00:00:00Z",
                "chunk_index": 0,
                "page_title": "登录模块",
            }
        ])

        doc_output = {
            "worker_type": "doc",
            "citations": [{
                "chunk_id": "cross-doc-001",
                "snippet": "用户登录模块支持用户名密码和手机验证码两种方式。",
                "source_type": "prd",
                "source_id": "confluence:test-001",
            }]
        }
        sql_output = {
            "worker_type": "sql",
            "citations": [{
                "chunk_id": "cross-sql-001",
                "snippet": "users 表包含 username, password_hash, phone 字段",
                "source_type": "sql",
                "source_id": "public.users",
            }]
        }

        result = await synthesis_agent.synthesize(
            original_query="用户登录有哪些方式？",
            worker_outputs=[doc_output, sql_output],
        )

        assert result["final_answer"] is not None
        assert len(result["final_answer"]) > 50
        assert result["convergence_reason"] in ("pass", "fix", "contradiction", "gap")

    async def test_cross_source_contradiction_detection(self, synthesis_agent):
        """Doc 说 3 步，SQL 显示 2 步 → 应检测到矛盾。"""
        doc_output = {
            "worker_type": "doc",
            "citations": [{
                "chunk_id": "contra-doc-001",
                "snippet": "支付流程包含三步：下单、支付确认、发货。",
                "source_type": "prd",
                "source_id": "confluence:pay",
            }]
        }
        sql_output = {
            "worker_type": "sql",
            "citations": [{
                "chunk_id": "contra-sql-001",
                "snippet": "orders 表的状态字段只有两种：pending、completed。",
                "source_type": "sql",
                "source_id": "public.orders",
            }]
        }

        result = await synthesis_agent.synthesize(
            original_query="支付流程有几步骤？",
            worker_outputs=[doc_output, sql_output],
        )

        annotations = result.get("annotations", [])
        contradiction_found = any("矛盾" in a.get("message", "") or a.get("icon") == "⚡"
                                  for a in annotations)
        assert contradiction_found or result["convergence_reason"] == "contradiction"
```

- [ ] **Step 3: 编写 RAG 质量评估**

```python
# tests/eval/test_doc_rag.py
"""Doc Agent RAG 质量评估——Recall@10, MRR。

需要: 50 条标注测试集 + ES + PGVector 已索引测试语料
"""

import pytest


@pytest.mark.eval
class TestDocRAGQuality:
    ANNOTATED_QUERIES = [
        # (query, entities, expected_chunk_ids)
        ("REQ-001", {"req_ids": ["REQ-001"]}, ["doc-req001-chunk1", "doc-req001-chunk2"]),
        ("支付流程", {"req_ids": [], "module": "支付"}, ["doc-pay-chunk1", "doc-pay-chunk3"]),
        # ... 50 条标注
    ]

    async def test_recall_at_10(self, doc_agent):
        """Recall@10 ≥ 0.88。"""
        total_hits = 0
        total_expected = 0

        for query, entities, expected_ids in self.ANNOTATED_QUERIES:
            result = await doc_agent.search(query=query, entities=entities)
            returned_ids = {r["chunk_id"] for r in result["final_results"][:10]}
            hits = len(returned_ids & set(expected_ids))
            total_hits += hits
            total_expected += len(expected_ids)

        recall = total_hits / total_expected if total_expected > 0 else 0
        assert recall >= 0.88, f"Recall@10 = {recall:.3f} < 0.88"

    async def test_mrr(self, doc_agent):
        """MRR ≥ 0.80。"""
        reciprocal_ranks = []

        for query, entities, expected_ids in self.ANNOTATED_QUERIES:
            result = await doc_agent.search(query=query, entities=entities)
            ranks = []
            for i, r in enumerate(result["final_results"][:10]):
                if r["chunk_id"] in expected_ids:
                    ranks.append(i + 1)
            rr = 1 / min(ranks) if ranks else 0
            reciprocal_ranks.append(rr)

        mrr = sum(reciprocal_ranks) / len(reciprocal_ranks)
        assert mrr >= 0.80, f"MRR = {mrr:.3f} < 0.80"
```

- [ ] **Step 4: 更新 E2E conftest**

```python
# tests/e2e/conftest.py (补全)
import pytest
import asyncio
from spma.retrieval.es_client import ESClient
from spma.agents.doc.graph import build_doc_agent_graph
from spma.agents.synthesis.graph import build_synthesis_agent_graph


@pytest.fixture
async def test_es_client():
    client = ESClient(hosts=["http://localhost:9200"], index_name="spma_docs_test")
    await client.create_index()
    yield client
    await client.delete_index()
    await client.close()


@pytest.fixture
async def test_vector_store():
    # 使用 Testcontainers 或已部署的 PGVector
    from spma.retrieval.vector_store import PgVectorStore
    store = PgVectorStore(connection_string="postgresql://localhost:5432/spma_test")
    yield store


@pytest.fixture
async def doc_agent(test_es_client, test_vector_store):
    graph = build_doc_agent_graph(
        es_client=test_es_client,
        vector_store=test_vector_store,
        embedder=None,  # 使用真实 BGE-M3 服务或 Mock
        llm=None,       # 使用 MockLLM
    )
    return graph.compile()


@pytest.fixture
async def synthesis_agent():
    graph = build_synthesis_agent_graph(
        llm=None,      # MockLLM
        audit_llm=None,  # MockLLM
    )
    return graph.compile()
```

- [ ] **Step 5: 提交**

```bash
git add tests/e2e/test_doc_single_source.py tests/e2e/test_cross_source.py tests/e2e/conftest.py tests/eval/test_doc_rag.py
git commit -m "test(e2e): add Doc Agent single-source + cross-source E2E tests and RAG eval"
```

---

## 执行顺序

```
Slice 1 (检索基础设施):  Task 1.1 → 1.2 → 1.3 → 1.4
                          │
Slice 2 (摄入管道):       Task 2.1 → 2.2
                          │
Slice 3 (Doc Agent 核心):  Task 3.1 → 3.2 → 3.3 → 3.4
                          │
Slice 4 (Synthesis Agent): Task 4.1 → 4.2 → 4.3
                          │
Slice 5 (Redis 状态):      Task 5.1
                          │
Slice 6 (检索日志):        Task 6.1
                          │
Slice 7 (E2E + RAG):      Task 7.1
```

Slice 1 是所有后续工作的前提。Slice 2 依赖 Slice 1 的 ES 客户端。Slice 3 依赖 Slice 1 的 RRF 融合 + Slice 2 的 chunker。Slice 4 依赖 Slice 1 的 RRF 融合。Slice 5-7 依赖前 4 个 Slice 完成。

---

## 性能目标

| 指标 | 目标 |
|------|------|
| Doc Agent 单源 P50 | < 3s |
| Doc Agent P95 | < 6s |
| Doc Agent ≤ 3 轮 | ≤ 2s 超时 |
| Synthesis ≤ 2 轮 | ≤ 2s 超时 |
| ES BM25 P99 | < 50ms |
| PGVector P99 | < 100ms |
| Recall@10 | ≥ 0.88 |
| 引用覆盖率 | ≥ 80% |
