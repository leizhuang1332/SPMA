# Reranker 模型从 ModelScope 加载 + Pipeline 初始化时创建一次

**日期**: 2026-06-19
**状态**: 设计中
**关联 Bug**: `search_node` 检索失败返回空结果（`RuntimeError: Cannot send a request, as the client has been closed.`）

## 背景

当前 `build_postprocessor_chain()` 在每次 `search()` 调用时创建新的 `SentenceTransformerRerank` 实例，进而创建 `CrossEncoder`。`CrossEncoder.__init__()` 通过 HuggingFace Hub 下载 `BAAI/bge-reranker-v2-m3` 模型。在特定网络环境下（HF CDN SSL 连接被干扰），下载失败触发 `huggingface_hub` 的重试 bug（`close_session()` 后旧 client 引用未更新），导致 `RuntimeError`，最终 `search_node` 返回空结果。

根因分析详见 `/review` 输出。

## 目标

1. Reranker 模型下载源从 HuggingFace Hub 切换为 ModelScope（与 `BGEM3Embedder` 一致）
2. 模型在 pipeline 初始化时创建一次，后续检索复用
3. 模型下载失败时降级运行（跳过 rerank），不阻塞检索
4. 所有降级路径输出告警日志

## 设计

### 1. 新增 `src/spma/retrieval/reranker.py`

镜像 `src/spma/retrieval/embedder.py` 的 `BGEM3Embedder` 模式，适配 Cross-Encoder 场景：

```
BGEReranker.__init__(device="cpu")
├─ ThreadPoolExecutor(max_workers=1)           ← 用于 rerank() 时 offload 推理
├─ SENTENCE_TRANSFORMERS_HOME → ~/.cache/modelscope/hub
├─ 检查本地缓存 → 命中直接 CrossEncoder(local_path)
├─ 缓存未命中 → snapshot_download(MODEL_ID) → CrossEncoder(local_path)
└─ 异常 → 向上传播，由调用方处理降级

BGEReranker.rerank(nodes, query_bundle, top_n) → list[NodeWithScore]
├─ 提取 query + doc 对
├─ run_in_executor → CrossEncoder.predict()    ← 异步，不阻塞事件循环
└─ 按 score 降序，返回 [:top_n]
```

**设计决策：`__init__` 同步 vs `create()` 异步工厂**

`BGEM3Embedder` 使用 `async create()` 工厂，因为其调用方（`query.py`/`app.py`）本身是 async 上下文，可以在图构建前 await。

`BGEReranker` 在 `AdvancedLlamaIndexPipeline.initialize()` 中被创建，而 `initialize()` 当前是**同步方法**（`graph.py:53`）。为了保持 `graph.py` 不变，采用同步 `__init__`：
- 模型下载/加载在 `__init__` 中同步完成（阻塞当前线程）
- 首次下载可能耗时 30-60s，但仅发生一次（之后命中缓存）
- 推理阶段 `rerank()` 通过 `run_in_executor` 异步执行，不阻塞事件循环

**依赖**：
- `sentence_transformers.CrossEncoder`
- `modelscope.snapshot_download`
- `llama_index.core.schema.NodeWithScore`, `QueryBundle`

**缓存路径**：`~/.cache/modelscope/hub/BAAI/bge-reranker-v2-m3`

**接口**：
```python
class BGEReranker:
    MODEL_ID = "BAAI/bge-reranker-v2-m3"

    def __init__(self, device: str = "cpu"):
        """同步初始化：下载（如需要）并加载 CrossEncoder 模型。
        
        Raises:
            Exception: 模型下载或加载失败时向上传播。
        """
        self._pool = ThreadPoolExecutor(max_workers=1)
        self._model = self._load_model(device)

    def _load_model(self, device: str) -> CrossEncoder:
        """同步加载：缓存检查 → ModelScope 下载 → CrossEncoder 初始化"""
        ...

    async def rerank(
        self,
        nodes: list[NodeWithScore],
        query_bundle: QueryBundle,
        top_n: int = 10,
    ) -> list[NodeWithScore]:
        """异步精排：offload CrossEncoder.predict 到线程池。"""
        ...
```

### 2. 改造 `src/spma/agents/doc/llamaindex_pipeline.py`

#### 2.1 `AdvancedLlamaIndexPipeline.initialize()` —— 新增 reranker 初始化

```python
def initialize(self, embedder, hyde_llm=None, reranker=None):
    # ... 现有 embedding/pgvector 初始化不变 ...

    # Reranker 初始化（降级安全）
    if self._config.enable_rerank:
        try:
            if reranker is not None:
                self._reranker = reranker
            else:
                from spma.retrieval.reranker import BGEReranker
                self._reranker = BGEReranker()  # 同步，阻塞直到下载/加载完成
            self._long_context_reorder = LongContextReorder()
        except Exception:
            logger.exception("Reranker 模型加载失败")
            logger.warning("Reranker 初始化失败，降级为无精排模式，检索结果将不做 Cross-Encoder 重排序")
            self._config.enable_rerank = False
            self._reranker = None
            self._long_context_reorder = None
```

#### 2.2 `search()` —— 复用实例，不再每次创建

```python
async def search(self, query, mode, entities, hyde_llm):
    # ... 检索逻辑 (retriever.aretrieve) 不变 ...

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

    # ... 后续 HyDE、去重、格式化不变 ...
```

#### 2.3 删除 `build_postprocessor_chain()`

该函数的唯一职责——根据 mode 决定是否使用 rerank——已被 `initialize()` + `search()` 中的条件判断替代。

### 3. `graph.py` —— 无需改动

`initialize()` 保持同步，签名不变。`build_doc_agent_graph` 无需修改。

如需测试 mock，可在构建 `PipelineConfig` 时设置 `enable_rerank=False`，或通过 `initialize(reranker=mock_reranker)` 注入。

### 4. 错误处理与降级策略

三层防护，所有降级点必须输出告警日志：

| 层级 | 场景 | 行为 | 告警日志 |
|------|------|------|----------|
| L1 | `BGEReranker()` 初始化失败 | `enable_rerank=False`，后续检索跳过 rerank | `logger.exception` + `logger.warning("Reranker 初始化失败，降级为无精排模式，检索结果将不做 Cross-Encoder 重排序")` |
| L2 | `reranker.rerank()` 单次调用失败 | 返回原始 nodes，不抛异常 | `logger.exception` + `logger.warning("Reranker 调用失败，本次检索跳过精排，返回原始排序结果")` |
| L3 | mode == "precise" | 直接跳过 rerank（主动选择，非降级） | 无需告警 |

## 改动文件汇总

| 文件 | 操作 | 影响 |
|------|------|------|
| `src/spma/retrieval/reranker.py` | **新建** | +~85 行 |
| `src/spma/agents/doc/llamaindex_pipeline.py` | `initialize()` 加 reranker + `search()` 改复用 + 删 `build_postprocessor_chain` | +25 / -20 行 |
| `src/spma/agents/doc/graph.py` | 无需改动 | 0 行 |

## 测试要点

1. **正常路径**：reranker 从 ModelScope 下载并缓存 → 后续检索使用缓存 → rerank 生效
2. **缓存命中**：模型已在 `~/.cache/modelscope/hub/BAAI/bge-reranker-v2-m3` → 跳过下载直接加载
3. **L1 降级**：ModelScope 不可达 → 输出告警 → `enable_rerank=False` → 检索正常返回（无 rerank）
4. **L2 降级**：`reranker.rerank()` 内部异常 → 输出告警 → 返回原始 nodes 顺序
5. **precise 模式**：mode="precise" → 不触发 rerank → 无告警
6. **实例复用**：连续两次 `search()` 调用 → 同一个 `self._reranker` 实例被复用
