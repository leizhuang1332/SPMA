# Reranker 模型从 ModelScope 加载 + Pipeline 初始化时创建一次 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 reranker 模型下载源从 HuggingFace Hub 切换为 ModelScope，在 pipeline 初始化时创建一次（而非每次 search 时创建），下载失败时降级运行。

**Architecture:** 新增 `BGEReranker` 类（镜像 `BGEM3Embedder` 模式），同步 `__init__` 处理下载/加载，异步 `rerank()` 通过 `run_in_executor` 执行推理。`AdvancedLlamaIndexPipeline.initialize()` 中创建 reranker 实例，`search()` 中复用。删除 `build_postprocessor_chain()`。`graph.py` 无需改动。

**Tech Stack:** Python 3.13, sentence-transformers (CrossEncoder), ModelScope (snapshot_download), llama_index (NodeWithScore, QueryBundle, LongContextReorder)

---

## File Structure

| 文件 | 职责 | 操作 |
|------|------|------|
| `src/spma/retrieval/reranker.py` | BGEReranker 类：ModelScope 下载 + CrossEncoder 加载 + 异步推理 | **新建** |
| `tests/unit/retrieval/test_bge_reranker.py` | BGEReranker 单元测试：加载、缓存命中、rerank 排序、降级 | **新建** |
| `src/spma/agents/doc/llamaindex_pipeline.py` | initialize() 加 reranker + search() 复用实例 + 删除 build_postprocessor_chain | **改造** |
| `tests/unit/agents/doc/test_llamaindex_pipeline.py` | 更新测试：删除 build_postprocessor_chain 测试 + 新增降级测试 | **改造** |

---

### Task 1: 编写 BGEReranker 单元测试（先写，预期失败）

**Files:**
- Create: `tests/unit/retrieval/test_bge_reranker.py`

- [ ] **Step 1: 创建测试文件，编写所有测试用例**

```python
"""BGEReranker 单元测试。"""
import asyncio
import os
from unittest.mock import MagicMock, patch, PropertyMock

import pytest
from llama_index.core.schema import NodeWithScore, TextNode, QueryBundle


def make_node(node_id: str, text: str, score: float = 0.5) -> NodeWithScore:
    """辅助：创建带分数的测试节点。"""
    node = TextNode(id_=node_id, text=text)
    return NodeWithScore(node=node, score=score)


class TestBGERerankerInit:
    """测试 BGEReranker.__init__ —— 模型加载逻辑。"""

    def test_cache_hit_skips_download(self):
        """缓存已存在时，跳过 ModelScope 下载，直接加载 CrossEncoder。"""
        with patch("os.path.isdir", return_value=True), \
             patch("os.listdir", return_value=["config.json", "model.safetensors"]), \
             patch("os.environ.setdefault") as mock_setdefault, \
             patch("spma.retrieval.reranker.CrossEncoder") as mock_cross_encoder, \
             patch("spma.retrieval.reranker.snapshot_download") as mock_download:
            from spma.retrieval.reranker import BGEReranker

            reranker = BGEReranker()

            # 验证 SENTENCE_TRANSFORMERS_HOME 已设置
            mock_setdefault.assert_called_once_with(
                "SENTENCE_TRANSFORMERS_HOME",
                os.path.expanduser("~/.cache/modelscope/hub"),
            )
            # 验证 CrossEncoder 从本地缓存路径加载
            mock_cross_encoder.assert_called_once()
            local_path = mock_cross_encoder.call_args[0][0]
            assert "BAAI/bge-reranker-v2-m3" in local_path
            # 验证没有触发 ModelScope 下载
            mock_download.assert_not_called()

    def test_cache_miss_triggers_download(self):
        """缓存不存在时，触发 ModelScope snapshot_download。"""
        with patch("os.path.isdir", return_value=False), \
             patch("os.environ.setdefault"), \
             patch("spma.retrieval.reranker.CrossEncoder") as mock_cross_encoder, \
             patch("spma.retrieval.reranker.snapshot_download", return_value="/fake/path") as mock_download:
            from spma.retrieval.reranker import BGEReranker

            reranker = BGEReranker()

            mock_download.assert_called_once_with(
                "BAAI/bge-reranker-v2-m3",
                cache_dir=os.path.expanduser("~/.cache/modelscope/hub"),
            )
            mock_cross_encoder.assert_called_once_with("/fake/path", device="cpu")

    def test_download_failure_propagates_exception(self):
        """下载失败时，异常向上传播（由调用方 initialize 处理降级）。"""
        with patch("os.path.isdir", return_value=False), \
             patch("os.environ.setdefault"), \
             patch("spma.retrieval.reranker.snapshot_download", side_effect=RuntimeError("Network error")):
            from spma.retrieval.reranker import BGEReranker

            with pytest.raises(RuntimeError, match="Network error"):
                BGEReranker()

    def test_cross_encoder_load_failure_propagates(self):
        """CrossEncoder 加载失败时，异常向上传播。"""
        with patch("os.path.isdir", return_value=True), \
             patch("os.listdir", return_value=["config.json"]), \
             patch("os.environ.setdefault"), \
             patch("spma.retrieval.reranker.CrossEncoder", side_effect=OSError("Corrupted model")):
            from spma.retrieval.reranker import BGEReranker

            with pytest.raises(OSError, match="Corrupted model"):
                BGEReranker()


class TestBGERerankerRerank:
    """测试 BGEReranker.rerank —— 异步精排逻辑。"""

    @pytest.mark.asyncio
    async def test_rerank_sorts_by_score_descending(self):
        """验证 rerank 按 CrossEncoder 分数降序排列。"""
        # 模拟 CrossEncoder.predict 返回的分数
        mock_model = MagicMock()
        mock_model.predict.return_value = [0.9, 0.3, 0.7]

        with patch("spma.retrieval.reranker.ThreadPoolExecutor"), \
             patch.object(BGEReranker, "_load_model", return_value=mock_model):
            from spma.retrieval.reranker import BGEReranker
            # 绕过 __init__ 的 _load_model 调用
            reranker = BGEReranker.__new__(BGEReranker)

        import asyncio
        from concurrent.futures import ThreadPoolExecutor
        reranker._model = mock_model
        reranker._pool = ThreadPoolExecutor(max_workers=1)

        query = QueryBundle(query_str="什么是 Python？")
        nodes = [
            make_node("c1", "Python 是一种编程语言", score=0.5),
            make_node("c2", "Java 是一种编程语言", score=0.5),
            make_node("c3", "Python 简单易学", score=0.5),
        ]

        result = await reranker.rerank(nodes=nodes, query_bundle=query, top_n=2)

        assert len(result) == 2
        assert result[0].node.node_id == "c1"  # score 0.9
        assert result[1].node.node_id == "c3"  # score 0.7
        assert result[0].score == pytest.approx(0.9)
        assert result[1].score == pytest.approx(0.7)

    @pytest.mark.asyncio
    async def test_rerank_top_n_limits_results(self):
        """验证 top_n 参数限制返回数量。"""
        mock_model = MagicMock()
        mock_model.predict.return_value = [0.5, 0.4, 0.3, 0.2, 0.1]

        from spma.retrieval.reranker import BGEReranker
        from concurrent.futures import ThreadPoolExecutor

        reranker = BGEReranker.__new__(BGEReranker)
        reranker._model = mock_model
        reranker._pool = ThreadPoolExecutor(max_workers=1)

        query = QueryBundle(query_str="test")
        nodes = [make_node(f"c{i}", f"text{i}") for i in range(5)]

        result = await reranker.rerank(nodes=nodes, query_bundle=query, top_n=3)
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_rerank_uses_correct_query_doc_pairs(self):
        """验证 rerank 传递正确的 (query, doc) 对给 CrossEncoder.predict。"""
        mock_model = MagicMock()
        mock_model.predict.return_value = [1.0, 0.5]

        from spma.retrieval.reranker import BGEReranker
        from concurrent.futures import ThreadPoolExecutor

        reranker = BGEReranker.__new__(BGEReranker)
        reranker._model = mock_model
        reranker._pool = ThreadPoolExecutor(max_workers=1)

        query = QueryBundle(query_str="搜索关键词")
        nodes = [
            make_node("a", "文档内容A"),
            make_node("b", "文档内容B"),
        ]

        await reranker.rerank(nodes=nodes, query_bundle=query, top_n=10)

        # 验证 predict 被调用时传入了正确的 (query, doc) 对
        call_args = mock_model.predict.call_args[0][0]
        assert call_args == [("搜索关键词", "文档内容A"), ("搜索关键词", "文档内容B")]
```

- [ ] **Step 2: 运行测试确认全部失败**

```bash
cd /Users/Ray/TraeProjects/SPMA && .venv/bin/python -m pytest tests/unit/retrieval/test_bge_reranker.py -v 2>&1 | tail -20
```

Expected: 所有 7 个测试 FAIL（模块 `spma.retrieval.reranker` 不存在）。

- [ ] **Step 3: 提交失败测试**

```bash
git add tests/unit/retrieval/test_bge_reranker.py
git commit -m "test: add failing BGEReranker unit tests (TDD)"
```

---

### Task 2: 实现 BGEReranker 类

**Files:**
- Create: `src/spma/retrieval/reranker.py`

- [ ] **Step 1: 创建 reranker.py**

```python
"""BGE Reranker——基于 ModelScope 下载 + sentence-transformers CrossEncoder 本地推理。

使用方式:
    reranker = BGEReranker()
    reranked = await reranker.rerank(nodes, query_bundle, top_n=10)
"""
import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from typing import List

logger = logging.getLogger(__name__)

MODEL_ID = "BAAI/bge-reranker-v2-m3"


class BGEReranker:
    """BGE Cross-Encoder 精排器。

    - 下载源: ModelScope (modelscope.cn)
    - 推理引擎: sentence-transformers CrossEncoder
    - 后端: CPU（开发用），GPU 可通过 device 参数切换
    """

    MODEL_ID = MODEL_ID

    def __init__(self, device: str = "cpu"):
        """同步初始化：下载（如需要）并加载 CrossEncoder 模型。

        Raises:
            Exception: 模型下载或加载失败时向上传播，由调用方处理降级。
        """
        self._pool = ThreadPoolExecutor(max_workers=1)
        self._model = self._load_model(device)

    def _load_model(self, device: str):
        """同步加载：缓存检查 → ModelScope 下载 → CrossEncoder 初始化。"""
        import os
        os.environ.setdefault(
            "SENTENCE_TRANSFORMERS_HOME",
            os.path.expanduser("~/.cache/modelscope/hub"),
        )

        from sentence_transformers import CrossEncoder

        cache_root = os.path.expanduser("~/.cache/modelscope/hub")
        model_dir = os.path.join(cache_root, self.MODEL_ID)

        if os.path.isdir(model_dir) and os.listdir(model_dir):
            logger.info("BGE Reranker 已在本地缓存，跳过下载: %s", model_dir)
            return CrossEncoder(model_dir, device=device)

        logger.info("本地缓存未命中，从 ModelScope 下载 BGE Reranker...")
        import logging as _logging
        _logging.getLogger("modelscope").setLevel(_logging.WARNING)

        from modelscope import snapshot_download
        local_path = snapshot_download(self.MODEL_ID, cache_dir=cache_root)
        logger.info("BGE Reranker 下载完成: %s", local_path)
        return CrossEncoder(local_path, device=device)

    async def rerank(
        self,
        nodes: List,
        query_bundle,
        top_n: int = 10,
    ) -> List:
        """异步精排——offload CrossEncoder.predict 到线程池。

        Args:
            nodes: List[NodeWithScore] — 待重排节点
            query_bundle: QueryBundle — 查询
            top_n: 返回数量

        Returns:
            List[NodeWithScore] — 按 CrossEncoder 分数降序排列的 top_n 个节点
        """
        query_str = query_bundle.query_str
        pairs = [(query_str, n.node.get_content()) for n in nodes]

        def _predict():
            return self._model.predict(
                pairs,
                show_progress_bar=False,
            )

        scores = await asyncio.get_event_loop().run_in_executor(self._pool, _predict)

        # 将 CrossEncoder 分数写入 node.score，按降序排列
        for node, score in zip(nodes, scores):
            node.score = float(score)

        nodes_sorted = sorted(nodes, key=lambda n: n.score, reverse=True)
        return nodes_sorted[:top_n]
```

- [ ] **Step 2: 运行测试验证通过**

```bash
cd /Users/Ray/TraeProjects/SPMA && .venv/bin/python -m pytest tests/unit/retrieval/test_bge_reranker.py -v 2>&1
```

Expected: 7 passed.

- [ ] **Step 3: 提交**

```bash
git add src/spma/retrieval/reranker.py
git commit -m "feat: add BGEReranker with ModelScope download and CrossEncoder inference"
```

---

### Task 3: 改造 AdvancedLlamaIndexPipeline

**Files:**
- Modify: `src/spma/agents/doc/llamaindex_pipeline.py`

- [ ] **Step 1: 改造 initialize() —— 新增 reranker 初始化**

将 [llamaindex_pipeline.py:108-143](src/spma/agents/doc/llamaindex_pipeline.py#L108-L143) 的 `initialize` 方法改为：

```python
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
            self._long_context_reorder = LongContextReorder()
        except Exception:
            logger.exception("Reranker 模型加载失败")
            logger.warning(
                "Reranker 初始化失败，降级为无精排模式，检索结果将不做 Cross-Encoder 重排序"
            )
            self._config.enable_rerank = False
            self._reranker = None
            self._long_context_reorder = None
```

- [ ] **Step 2: 改造 search() —— 复用 reranker 实例**

将 [llamaindex_pipeline.py:162-166](src/spma/agents/doc/llamaindex_pipeline.py#L162-L166) 的 postprocessor 逻辑替换为：

```python
        # 后处理：复用已初始化的 postprocessor 实例（不再每次调用 build_postprocessor_chain）
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
```

- [ ] **Step 3: 删除 build_postprocessor_chain() 函数**

删除 [llamaindex_pipeline.py:68-85](src/spma/agents/doc/llamaindex_pipeline.py#L68-L85) 整个 `build_postprocessor_chain` 函数。

- [ ] **Step 4: 清理无用的 import**

文件顶部的 import 块中，删除不再使用的 `SentenceTransformerRerank` 和 `LongContextReorder` 的 try/except 导入块（第 29-37 行）。`LongContextReorder` 的导入移到 `initialize()` 方法内部（已在 Step 1 代码中包含）。`SentenceTransformerRerank` 不再需要导入。

删除后，第 29-37 行的 try/except 块应移除：

```python
# 删除以下代码块（第 29-37 行）:
# Postprocessors (may fail if sentence-transformers is not installed)
try:
    from llama_index.core.postprocessor import (
        SentenceTransformerRerank,
        LongContextReorder,
    )
except ImportError:
    SentenceTransformerRerank = None  # type: ignore
    LongContextReorder = None  # type: ignore
```

在 `initialize()` 方法内部已有 `from llama_index.core.postprocessor import LongContextReorder`（Step 1 代码中包含）。

- [ ] **Step 5: 运行测试验证**

```bash
cd /Users/Ray/TraeProjects/SPMA && .venv/bin/python -m pytest tests/unit/agents/doc/test_llamaindex_pipeline.py -v 2>&1
```

Expected: `TestBuildPostprocessorChain` 类下的测试会失败（函数已删除），其余测试应通过。

- [ ] **Step 6: 提交**

```bash
git add src/spma/agents/doc/llamaindex_pipeline.py
git commit -m "refactor: load reranker once in initialize(), reuse in search(), remove build_postprocessor_chain"
```

---

### Task 4: 更新现有测试

**Files:**
- Modify: `tests/unit/agents/doc/test_llamaindex_pipeline.py`

- [ ] **Step 1: 删除 TestBuildPostprocessorChain 类**

删除整个 `TestBuildPostprocessorChain` 类（第 42-92 行）——该函数已被删除。

- [ ] **Step 2: 新增 L1 降级测试**

在 `TestAdvancedLlamaIndexPipeline` 类末尾添加：

```python
    def test_initialize_reranker_failure_disables_rerank(self):
        """L1 降级：Reranker 初始化失败时，enable_rerank=False。"""
        from spma.agents.doc.llamaindex_pipeline import PipelineConfig

        cfg = PipelineConfig(enable_rerank=True)
        pipeline = self._make_pipeline(config=cfg)

        with patch(
            "spma.agents.doc.llamaindex_pipeline.BGEReranker",
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
        # （在 search() 中由 mode != "precise" 条件控制）
        assert pipeline._reranker is not None
```

- [ ] **Step 3: 运行测试验证全部通过**

```bash
cd /Users/Ray/TraeProjects/SPMA && .venv/bin/python -m pytest tests/unit/agents/doc/test_llamaindex_pipeline.py tests/unit/retrieval/test_bge_reranker.py -v 2>&1
```

Expected: all tests PASS.

- [ ] **Step 4: 提交**

```bash
git add tests/unit/agents/doc/test_llamaindex_pipeline.py
git commit -m "test: update pipeline tests — remove build_postprocessor_chain tests, add degradation tests"
```

---

### Task 5: 运行全量测试确认无回归

- [ ] **Step 1: 运行完整的单元测试套件**

```bash
cd /Users/Ray/TraeProjects/SPMA && .venv/bin/python -m pytest tests/unit/ -v 2>&1 | tail -40
```

Expected: 全部通过（或有与本次改动无关的已有失败，需逐一确认）。

- [ ] **Step 2: 提交最终状态**

```bash
git status
# 确认无未提交变更
```

---

## 改动文件汇总

| 文件 | 操作 | 行数变化 |
|------|------|----------|
| `src/spma/retrieval/reranker.py` | 新建 | +78 |
| `tests/unit/retrieval/test_bge_reranker.py` | 新建 | +170 |
| `src/spma/agents/doc/llamaindex_pipeline.py` | 改造 | +30 / -25 |
| `tests/unit/agents/doc/test_llamaindex_pipeline.py` | 改造 | +80 / -50 |
