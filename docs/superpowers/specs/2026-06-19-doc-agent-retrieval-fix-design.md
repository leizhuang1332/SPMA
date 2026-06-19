# 修复 Doc Agent 检索链路 — 设计文档

**日期:** 2026-06-19  
**状态:** 设计已确认  
**背景:** 调试发现 `/api/v1/query` → `build_doc_agent_graph` → `g.ainvoke()` 始终返回空结果

---

## 根因摘要

通过逐节点追踪 LangGraph 图执行，定位到 4 个问题：

| 优先级 | 问题 | 影响 |
|--------|------|------|
| P0 | DSN 缺少 `+asyncpg` 驱动前缀，SQLAlchemy 报错 `psycopg2 is not async`，异常被静默吞掉 | 检索完全不可用 |
| P1 | `entities` 未传入 `g.ainvoke()` 初始状态 | 路由永远走 semantic 模式 |
| P2 | `rewritten_queries` 传入但无节点读取 | 改写查询浪费 |
| P3 | `search_node` 异常无日志 | 问题极难排查 |

---

## 修复方案

### P0: DSN 自动添加 asyncpg 前缀

**文件:** `src/spma/agents/doc/llamaindex_pipeline.py`

在 `AdvancedLlamaIndexPipeline.initialize()` 中创建 `LlamaPGVectorStore` 之前，检测并转换 DSN：

```python
dsn = self._config.dsn
if "+asyncpg" not in dsn:
    dsn = dsn.replace("postgresql://", "postgresql+asyncpg://")
    dsn = dsn.replace("postgres://", "postgresql+asyncpg://")
```

同时更新 `PipelineConfig` 默认 DSN 为 `postgresql+asyncpg://spma:spma123@localhost:5433/spma`。

### P1: 初始状态补充 entities

**文件:** `src/spma/api/routes/query.py`，第 150 行附近

在 `g.ainvoke()` 的初始状态 dict 中添加一行：

```python
"entities": entities,
```

entities 已在第 63 行通过 `extract_entities()` 提取，无需额外计算。

### P2: route_node 设置 current_query

**文件:** `src/spma/agents/doc/graph.py`，`route_node` 函数

读取 `rewritten_queries[0]` 并设为 `current_query`：

```python
if state.get("rewritten_queries"):
    state["current_query"] = state["rewritten_queries"][0]
```

放在 `route_node` 而非 `search_node`：`expand_node` 后续在 `current_query` 基础上扩展，保持查询链连续性。

### P3: search_node 异常日志

**文件:** `src/spma/agents/doc/graph.py`，`search_node` 函数，第 79 行

```python
except Exception:
    logger.exception("search_node 检索失败，返回空结果")
    fused = []
```

---

## 改动范围

| 文件 | 改动 | 风险 |
|------|------|------|
| `src/spma/agents/doc/llamaindex_pipeline.py` | PipelineConfig 默认 DSN + initialize() DSN 转换 | 低 |
| `src/spma/api/routes/query.py` | +1 行 entities 字段 | 低 |
| `src/spma/agents/doc/graph.py` | route_node +2 行，search_node +1 行日志 | 低 |
| **总计** | **~10 行，3 文件** | **无破坏性变更** |

---

## 验证方式

1. 用实际查询调用 `POST /api/v1/query`，确认返回 `result_count > 0`
2. 检查日志中不再出现 `psycopg2 is not async` 错误
3. 断点验证 `entities` 已传入 `route_node`，模式路由正常切换
4. 验证 `current_query` 使用的是改写查询而非原始查询
