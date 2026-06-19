# Doc Agent 检索链路修复 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 4 个导致 `/api/v1/query` → `build_doc_agent_graph` → `g.ainvoke()` 返回空结果的问题

**Architecture:** 3 文件、~10 行改动。涉及 DSN 异步驱动修复（llamaindex_pipeline.py）、初始状态补充 entities（query.py）、route_node 设置 current_query（graph.py）、search_node 异常日志（graph.py）

**Tech Stack:** Python, SQLAlchemy async, LangGraph

---

### Task 1: P0 — DSN 添加 asyncpg 异步驱动前缀

**Files:**
- Modify: `src/spma/agents/doc/llamaindex_pipeline.py:41`
- Modify: `src/spma/agents/doc/llamaindex_pipeline.py:105-126`

- [ ] **Step 1: 修改 PipelineConfig 默认 DSN**

将第 41 行：
```python
dsn: str = "postgresql://spma:spma123@localhost:5433/spma"
```
改为：
```python
dsn: str = "postgresql+asyncpg://spma:spma123@localhost:5433/spma"
```

- [ ] **Step 2: 在 initialize() 中添加 DSN 自动转换逻辑**

在 `initialize()` 方法中，第 113 行 `vector_store = LlamaPGVectorStore(` 之前插入 DSN 兼容转换：

```python
def initialize(self, embedder, hyde_llm=None) -> None:
    """延迟初始化——在 graph.py 中调用。"""
    from llama_index.vector_stores.postgres import PGVectorStore as LlamaPGVectorStore
    from spma.agents.doc.llamaindex_embedding import BGEM3EmbeddingAdapter

    self._embedder = embedder
    Settings.embed_model = BGEM3EmbeddingAdapter(embedder)

    # 确保 DSN 使用 asyncpg 驱动（SQLAlchemy async engine 需要）
    dsn = self._config.dsn
    if "+asyncpg" not in dsn:
        dsn = dsn.replace("postgresql://", "postgresql+asyncpg://")
        dsn = dsn.replace("postgres://", "postgresql+asyncpg://")

    vector_store = LlamaPGVectorStore(
        connection_string=dsn,
        async_connection_string=dsn,
        table_name="chunk_embeddings",
        ...
    )
```

- [ ] **Step 3: 验证 DSN 转换逻辑**

运行命令验证 DSN 转换正确：

```bash
python -c "
dsn = 'postgresql://spma:spma123@localhost:5433/spma'
if '+asyncpg' not in dsn:
    dsn = dsn.replace('postgresql://', 'postgresql+asyncpg://')
    dsn = dsn.replace('postgres://', 'postgresql+asyncpg://')
print(dsn)
# 预期输出: postgresql+asyncpg://spma:spma123@localhost:5433/spma
"
```

- [ ] **Step 4: 提交**

```bash
git add src/spma/agents/doc/llamaindex_pipeline.py
git commit -m "fix: add asyncpg driver prefix to PGVector DSN in LlamaIndex pipeline"
```

---

### Task 2: P1 — 初始状态补充 entities 字段

**Files:**
- Modify: `src/spma/api/routes/query.py:151-156`

- [ ] **Step 1: 在 g.ainvoke() 初始状态中添加 entities**

`entities` 已在第 63 行通过 `extract_entities(req.query, classification)` 提取，只需补充到初始状态 dict 中。

将第 151-156 行：
```python
result = await g.ainvoke({
    "original_query": req.query,
    "rewritten_queries": [rewritten_query],
    "retriever": None,
    "query_id": query_id,
})
```
改为：
```python
result = await g.ainvoke({
    "original_query": req.query,
    "rewritten_queries": [rewritten_query],
    "retriever": None,
    "query_id": query_id,
    "entities": entities,
})
```

- [ ] **Step 2: 提交**

```bash
git add src/spma/api/routes/query.py
git commit -m "fix: pass entities to doc agent initial state in query endpoint"
```

---

### Task 3: P2 — route_node 从 rewritten_queries 设置 current_query

**Files:**
- Modify: `src/spma/agents/doc/graph.py:53-64`

- [ ] **Step 1: 在 route_node 末尾添加 current_query 设置**

在 `route_node` 函数中，`state["hyde_enabled"] = hyde_enabled` 之前插入：

```python
async def route_node(state: DocAgentState) -> dict:
    entities = state.get("entities", {})
    mode = route_retrieval_mode(entities)
    state["weight_mode"] = mode
    query = state.get("original_query", "")

    # 优先使用改写查询
    rewritten = state.get("rewritten_queries", [])
    if rewritten:
        state["current_query"] = rewritten[0]

    hyde_enabled = (
        len(query) <= 30
        and not entities.get("req_ids")
        and hyde_llm is not None
    )
    state["hyde_enabled"] = hyde_enabled
    return state
```

> **为什么在 route_node 而非 search_node 设置：** `expand_node` 后续会在 `current_query` 基础上追加扩展词，放在 route_node 能让扩展链从改写查询开始，延续查询优化的收益。

- [ ] **Step 2: 提交**

```bash
git add src/spma/agents/doc/graph.py
git commit -m "fix: set current_query from rewritten_queries in route_node"
```

---

### Task 4: P3 — search_node 异常添加日志

**Files:**
- Modify: `src/spma/agents/doc/graph.py:80-81`

- [ ] **Step 1: 在 except 块中添加 logger.exception**

第 67 行附近，search_node 函数已经 import 了所需模块。在 except 块中添加日志。

将：
```python
        print(f"search_node: {fused}")
    except Exception:
        fused = []
```
改为：
```python
        print(f"search_node: {fused}")
    except Exception:
        logger.exception("search_node 检索失败，返回空结果")
        fused = []
```

> graph.py 当前**没有** logger。必须添加。

- [ ] **Step 2: 在 graph.py 顶部添加 logger**

在 `from typing import Literal` 之后插入：

```python
import logging

logger = logging.getLogger(__name__)
```

改后文件顶部变为：
```python
from typing import Literal
import logging

from langgraph.graph import StateGraph, END
...

logger = logging.getLogger(__name__)
```

- [ ] **Step 3: 提交**

```bash
git add src/spma/agents/doc/graph.py
git commit -m "fix: add exception logging to search_node for silent retrieval failures"
```

---

### Task 5: 端到端验证

- [ ] **Step 1: 启动应用**

```bash
cd /Users/Ray/TraeProjects/SPMA && python -m spma.main &
```

- [ ] **Step 2: 发送测试请求**

```bash
curl -s -X POST http://localhost:8000/api/v1/query \
  -H "Content-Type: application/json" \
  -d '{"query": "运力预估账单", "session_id": "test-fix"}' | python -m json.tool
```

- [ ] **Step 3: 验证 result_count > 0**

检查返回 JSON 中 `worker_results[0].result_count > 0` 且 `worker_results[0].error` 为空。

- [ ] **Step 4: 验证日志无 psycopg2 错误**

```bash
# 检查应用日志中不再出现
grep "psycopg2 is not async" <log_file>  # 预期：无匹配
```

- [ ] **Step 5: 提交（如有验证脚本）**

```bash
git add <any verification artifacts>
git commit -m "test: add E2E verification for doc agent retrieval fix"
```
