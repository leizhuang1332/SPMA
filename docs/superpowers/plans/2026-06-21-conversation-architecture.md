# 对话架构重构实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将查询流程重构为 LangGraph StateGraph + AsyncPostgresSaver checkpoint 持久化 + SSE 流式交互，解决答案不显示、无短期记忆、SSE 端点缺失三个核心问题。

**Architecture:** 顶层 QueryOrchestrator StateGraph 编排 classify→rewrite→dispatch→workers→synthesis→quality，通过 AsyncPostgresSaver 自动持久化 messages 为对话历史唯一数据源。前端通过 SSE 消费 graph.astream() 输出，MessageList 双路径渲染历史+实时流。

**Tech Stack:** Python 3.13, LangGraph 1.2.4, LangChain 1.3.4, FastAPI, sse-starlette, asyncpg, Next.js 14, TypeScript

---

## File Map

| 文件 | 操作 | 职责 |
|------|------|------|
| `pyproject.toml` | 修改 | 添加 sse-starlette 依赖 |
| `src/spma/api/extract_turns.py` | **新建** | extract_turns + _merge_turns + format_history |
| `tests/test_extract_turns.py` | **新建** | extract_turns 单元测试 |
| `src/spma/api/query_graph.py` | **新建** | QueryOrchestrator StateGraph + 7 节点 |
| `src/spma/api/dependencies.py` | 修改 | checkpointer + retrieval infra 单例 |
| `src/spma/api/app.py` | 修改 | startup 初始化 checkpointer + graph |
| `src/spma/api/routes/query.py` | 修改 | 新增 query/stream SSE 端点 |
| `src/spma/api/routes/session.py` | 修改 | 新增 GET /{sid}/history 端点 |
| `src/spma/api/session_store.py` | 修改 | user_id 参数化 |
| `frontend/src/types/api.ts` | 修改 | 新增 QueryStreamRequest, SessionHistoryResponse |
| `frontend/src/lib/api.ts` | 修改 | 新增 getSessionHistory() |
| `frontend/src/context/app-context.tsx` | 修改 | pendingQuery, SSE_DONE 追加 turn, SET_SESSION_TURNS |
| `frontend/src/components/chat/message-list.tsx` | 修改 | 双路径渲染（历史 + 实时 streaming） |
| `frontend/src/hooks/useSSE.ts` | 修改 | QUERY_START 传 query, 对齐 request schema |
| `frontend/src/app/chat/[sessionId]/page.tsx` | 修改 | 加载分页历史 |

---

### Task 1: 添加 sse-starlette 依赖

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: 添加依赖**

在 `pyproject.toml` 的 `dependencies` 中追加 `"sse-starlette>=2.0.0"`：

```toml
    "sse-starlette>=2.0.0",
```

- [ ] **Step 2: 安装**

Run: `cd /Users/Ray/TraeProjects/SPMA && uv sync`
Expected: 成功安装

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: add sse-starlette dependency"
```

---

### Task 2: SessionStore user_id 参数化

**Files:**
- Modify: `src/spma/api/session_store.py:39-62`
- Modify: `src/spma/api/routes/session.py:26-45`

- [ ] **Step 1: 修改 create_session 签名**

`src/spma/api/session_store.py` 的 `create_session` 方法，将 `user_id` 从硬编码 `''` 改为参数：

```python
async def create_session(self, title: str | None = None, user_id: str = "") -> str:
    session_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    if self._use_db:
        async with self._db_pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO sessions (session_id, title, user_id, metadata, created_at, updated_at)
                   VALUES ($1, $2, $3, '{}', $4, $4)""",
                session_id, title, user_id, now,
            )
    else:
        self._memory_sessions[session_id] = {
            "session_id": session_id, "title": title, "user_id": user_id,
            "created_at": now.isoformat(), "updated_at": now.isoformat(),
        }
        self._memory_turns[session_id] = []
    return session_id
```

- [ ] **Step 2: 路由层传入 user_id**

`src/spma/api/routes/session.py` 的 `create_session` 中：

```python
user_id = _user.get("sub", "") if _user else ""
session_id = await store.create_session(title=title, user_id=user_id)
```

- [ ] **Step 3: Commit**

```bash
git add src/spma/api/session_store.py src/spma/api/routes/session.py
git commit -m "fix: parameterize user_id in session creation"
```

---

### Task 3: 创建 extract_turns 模块

**Files:**
- Create: `src/spma/api/extract_turns.py`

- [ ] **Step 1: 完整实现**

创建 `src/spma/api/extract_turns.py`：

```python
"""extract_turns — 从 LangGraph checkpoint 提取对话轮次。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage

if TYPE_CHECKING:
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

logger = logging.getLogger(__name__)
ALLOWED_TYPES = {"HumanMessage", "AIMessage", "AIMessageChunk", "ToolMessage"}


async def extract_turns(
    session_id: str, checkpointer: AsyncPostgresSaver,
    limit: int = 20, offset: int = 0,
) -> dict | None:
    """从 checkpoint 提取分页对话轮次。"""
    config = {"configurable": {"thread_id": session_id}}
    messages = await _get_messages_primary(checkpointer, config)
    if messages is None:
        messages = await _get_messages_fallback(checkpointer, config)
    if messages is None:
        return None
    turns = _merge_turns(messages)
    total = len(turns)
    return {"turns": turns[offset:offset + limit], "total": total, "offset": offset, "limit": limit}


async def _get_messages_primary(checkpointer, config) -> list | None:
    try:
        cp = await checkpointer.aget_tuple(config)
        if cp is None:
            return None
        return list(cp.checkpoint.get("channel_values", {}).get("messages", []))
    except Exception as e:
        logger.warning("aget_tuple failed: %s", e)
        return None


async def _get_messages_fallback(checkpointer, config) -> list | None:
    try:
        best, max_len = None, 0
        async for cp in checkpointer.alist(config, limit=100):
            msgs = cp.checkpoint.get("channel_values", {}).get("messages", [])
            if len(msgs) > max_len:
                max_len, best = len(msgs), msgs
        return list(best) if best else None
    except Exception as e:
        logger.error("checkpoint list failed: %s", e)
        return None


def _merge_turns(messages: list) -> list[dict]:
    """累积模式：HumanMessage flush, AIMessage 累积, ToolMessage 跳过。"""
    turns, cur = [], None
    for msg in messages:
        if type(msg).__name__ not in ALLOWED_TYPES:
            continue
        if isinstance(msg, HumanMessage):
            if cur:
                turns.append(cur)
            cur = {"query_text": _safe_content(msg), "answer": "", "tool_calls": []}
        elif isinstance(msg, AIMessage):
            if cur is None:
                cur = {"query_text": "", "answer": "", "tool_calls": []}
            content = _safe_content(msg)
            if content:
                cur["answer"] += content
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                for tc in msg.tool_calls:
                    cur["tool_calls"].append({
                        "id": getattr(tc, "id", ""),
                        "name": getattr(tc, "name", ""),
                        "args": getattr(tc, "args", {}),
                    })
    if cur and (cur["query_text"] or cur["answer"]):
        turns.append(cur)
    return turns


def _safe_content(msg) -> str:
    content = getattr(msg, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content)
    return str(content) if content else ""


def format_history(messages: list, max_turns: int = 10) -> str:
    """从 messages 构建分类器 Prompt 用的对话历史文本。"""
    lines = []
    for msg in messages[-max_turns * 2:]:
        if isinstance(msg, HumanMessage):
            lines.append(f"用户: {_safe_content(msg)}")
        elif isinstance(msg, AIMessage):
            lines.append(f"AI: {_safe_content(msg)[:200]}")
    return "\n".join(lines) if lines else "无"
```

- [ ] **Step 2: Commit**

```bash
git add src/spma/api/extract_turns.py
git commit -m "feat: add extract_turns for checkpoint-based conversation history"
```

---

### Task 4: extract_turns 单元测试

**Files:**
- Create: `tests/test_extract_turns.py`

- [ ] **Step 1: 编写测试**

```python
"""extract_turns 单元测试。"""
import pytest
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage


class TestMergeTurns:
    def test_single_turn(self):
        from spma.api.extract_turns import _merge_turns
        msgs = [HumanMessage(content="问"), AIMessage(content="答")]
        turns = _merge_turns(msgs)
        assert len(turns) == 1
        assert turns[0]["query_text"] == "问"
        assert turns[0]["answer"] == "答"

    def test_multi_turn(self):
        from spma.api.extract_turns import _merge_turns
        msgs = [HumanMessage(content="Q1"), AIMessage(content="A1"),
                HumanMessage(content="Q2"), AIMessage(content="A2")]
        turns = _merge_turns(msgs)
        assert len(turns) == 2
        assert turns[0]["query_text"] == "Q1"
        assert turns[1]["query_text"] == "Q2"

    def test_skips_tool_messages(self):
        from spma.api.extract_turns import _merge_turns
        msgs = [HumanMessage(content="Q"),
                AIMessage(content="", tool_calls=[{"name": "s", "args": {}, "id": "1"}]),
                ToolMessage(content="42", tool_call_id="1"),
                AIMessage(content="A")]
        turns = _merge_turns(msgs)
        assert len(turns) == 1
        assert "A" in turns[0]["answer"]
        assert len(turns[0]["tool_calls"]) == 1

    def test_merges_consecutive_ai(self):
        from spma.api.extract_turns import _merge_turns
        msgs = [HumanMessage(content="Q"), AIMessage(content="P1"), AIMessage(content="P2")]
        turns = _merge_turns(msgs)
        assert len(turns) == 1
        assert turns[0]["answer"] == "P1P2"

    def test_empty(self):
        from spma.api.extract_turns import _merge_turns
        assert _merge_turns([]) == []


class TestFormatHistory:
    def test_basic(self):
        from spma.api.extract_turns import format_history
        msgs = [HumanMessage(content="Q"), AIMessage(content="A")]
        result = format_history(msgs)
        assert "用户: Q" in result
        assert "AI: A" in result

    def test_empty(self):
        from spma.api.extract_turns import format_history
        assert format_history([]) == "无"


class TestSafeContent:
    def test_str(self):
        from spma.api.extract_turns import _safe_content
        assert _safe_content(HumanMessage(content="hi")) == "hi"

    def test_list(self):
        from spma.api.extract_turns import _safe_content
        assert _safe_content(AIMessage(content=[{"text": "a"}, {"text": "b"}])) == "ab"

    def test_none(self):
        from spma.api.extract_turns import _safe_content
        assert _safe_content(AIMessage(content=None)) == ""
```

- [ ] **Step 2: 运行测试**

Run: `cd /Users/Ray/TraeProjects/SPMA && python -m pytest tests/test_extract_turns.py -v`
Expected: 8 tests PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_extract_turns.py
git commit -m "test: add extract_turns unit tests"
```

---

### Task 5: 依赖注入扩展（checkpointer + retrieval infra）

**Files:**
- Modify: `src/spma/api/dependencies.py`

- [ ] **Step 1: 追加单例管理**

在文件末尾追加：

```python
# ---- Checkpointer ----
_checkpointer: "AsyncPostgresSaver | None" = None

def get_checkpointer() -> "AsyncPostgresSaver":
    global _checkpointer
    if _checkpointer is None:
        raise RuntimeError("AsyncPostgresSaver not initialized")
    return _checkpointer

def set_checkpointer(cp) -> None:
    global _checkpointer
    _checkpointer = cp

# ---- Query Graph ----
_query_graph = None

def get_query_graph():
    global _query_graph
    if _query_graph is None:
        raise RuntimeError("QueryOrchestrator graph not compiled")
    return _query_graph

def set_query_graph(g) -> None:
    global _query_graph
    _query_graph = g

# ---- Retrieval Infra (for worker nodes) ----
_es_client, _vector_store, _embedder = None, None, None

def get_es_client():
    global _es_client
    if _es_client is None: raise RuntimeError("ESClient not initialized")
    return _es_client

def set_es_client(c) -> None: global _es_client; _es_client = c

def get_vector_store():
    global _vector_store
    if _vector_store is None: raise RuntimeError("PGVectorStore not initialized")
    return _vector_store

def set_vector_store(s) -> None: global _vector_store; _vector_store = s

def get_embedder():
    global _embedder
    if _embedder is None: raise RuntimeError("BGEM3Embedder not initialized")
    return _embedder

def set_embedder(e) -> None: global _embedder; _embedder = e
```

- [ ] **Step 2: Commit**

```bash
git add src/spma/api/dependencies.py
git commit -m "feat: add checkpointer, query graph, and retrieval infra singletons"
```

---

### Task 6: 创建 QueryOrchestrator StateGraph

**Files:**
- Create: `src/spma/api/query_graph.py`

- [ ] **Step 1: 完整实现**

创建 `src/spma/api/query_graph.py`（约 200 行），包含：
- `QueryOrchestratorState` TypedDict（`messages: Annotated[list, add_messages]` + 编排字段）
- 7 个节点函数：`classify_node`, `rewrite_node`, `dispatch_node`, `doc_worker_node`, `code_worker_node`, `sql_worker_node`, `synthesis_node`, `quality_node`, `reschedule_node`
- `route_to_workers` 条件边（Send API fan-out）
- `should_reschedule` 条件边（质量门 < 0.6）
- `build_query_orchestrator_graph()` 构建函数

关键点：
- `classify_node` 调用 `format_history(state["messages"])` 构建对话历史，不再依赖前端
- `synthesis_node` yield `AIMessage(content=final_answer)` 到 messages（add_messages reducer 自动追加）
- 子 worker 节点复用现有 `build_doc_agent_graph()` 等，子 graph state 不进入 checkpoint

（完整代码见 spec §4，此处省略重复）

- [ ] **Step 2: 验证导入**

Run: `cd /Users/Ray/TraeProjects/SPMA && python -c "from spma.api.query_graph import build_query_orchestrator_graph; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add src/spma/api/query_graph.py
git commit -m "feat: add QueryOrchestrator StateGraph with checkpoint-ready state"
```

---

### Task 7: App startup 初始化

**Files:**
- Modify: `src/spma/api/app.py`

- [ ] **Step 1: 添加 startup 事件**

在 `create_app()` 中追加 startup 事件：

```python
@app.on_event("startup")
async def startup_checkpointer_and_graph():
    """初始化 AsyncPostgresSaver + 编译 QueryOrchestrator graph。"""
    try:
        yaml_path = _resolve_config_path()
        with open(yaml_path) as f:
            raw = yaml.safe_load(f) or {}
    except Exception as e:
        logger.warning("skip checkpointer init: %s", e)
        return

    dsn = raw.get("spma", {}).get("connections", {}).get("postgres", {}).get("readonly_replica", "")
    if not dsn:
        logger.warning("no postgres DSN, skip checkpointer")
        return

    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from spma.api.dependencies import set_checkpointer, set_query_graph
        from spma.api.query_graph import build_query_orchestrator_graph

        checkpointer = AsyncPostgresSaver.from_conn_string(dsn)
        await checkpointer.setup()
        set_checkpointer(checkpointer)

        graph = build_query_orchestrator_graph().compile(checkpointer=checkpointer)
        set_query_graph(graph)
        logger.info("Checkpointer + QueryOrchestrator graph initialized")
    except Exception as e:
        logger.warning("Checkpointer/Graph init failed: %s", e)
```

- [ ] **Step 2: Commit**

```bash
git add src/spma/api/app.py
git commit -m "feat: init AsyncPostgresSaver + QueryOrchestrator graph on startup"
```

---

### Task 8: SSE 流式查询端点

**Files:**
- Modify: `src/spma/api/routes/query.py`

- [ ] **Step 1: 添加 query_stream 端点**

在 `query.py` 中追加新的 SSE 端点（保留旧 `/api/v1/query` 标记 deprecated）：

```python
import asyncio
import json
import time as time_module
from typing import AsyncGenerator
from fastapi import Request
from sse_starlette.sse import EventSourceResponse
from langchain_core.messages import HumanMessage, AIMessageChunk


class QueryStreamRequest(BaseModel):
    query: str
    session_id: str
    sources_hint: list[str] | None = None


@router.post("/api/v1/query/stream")
async def query_stream(req: QueryStreamRequest, request: Request):
    """SSE 流式查询——graph.astream() → SSE events。"""
    from spma.api.dependencies import get_session_store, get_query_graph

    store = get_session_store()
    if not await store.session_exists(req.session_id):
        raise HTTPException(status_code=404, detail=f"Session {req.session_id} not found")

    query_id = str(uuid.uuid4())
    start_time = time_module.time()

    try:
        session = await store.get_session(req.session_id)
        if session and not session.get("title") and req.query:
            await store.update_session_title(req.session_id, req.query[:50])
    except Exception:
        pass

    graph = get_query_graph()
    config = {"configurable": {"thread_id": req.session_id}}

    async def event_gen() -> AsyncGenerator[dict, None]:
        try:
            async for mode, data in graph.astream(
                {
                    "messages": [HumanMessage(content=req.query)],
                    "original_query": req.query,
                    "session_id": req.session_id,
                    "sources_hint": req.sources_hint,
                },
                config,
                stream_mode=["messages", "updates"],
            ):
                if mode == "updates":
                    for node_name, payload in data.items():
                        ev = _map_node_to_event(node_name, payload, query_id)
                        if ev: yield ev
                elif mode == "messages":
                    msg, _ = data
                    if isinstance(msg, AIMessageChunk) and msg.content:
                        content = msg.content
                        if isinstance(content, str) and content:
                            yield {"event": "synthesis", "data": json.dumps({"chunk": content}, ensure_ascii=False)}

            latency = int((time_module.time() - start_time) * 1000)
            yield {"event": "done", "data": json.dumps({"query_id": query_id, "latency_ms": latency, "degradation": None, "suggested_followups": []}, ensure_ascii=False)}
        except asyncio.CancelledError:
            yield {"event": "error", "data": json.dumps({"code": "CANCELLED", "message": "客户端取消"}, ensure_ascii=False)}
        except Exception as e:
            logger.exception("Stream error")
            yield {"event": "error", "data": json.dumps({"code": "INTERNAL", "message": str(e)}, ensure_ascii=False)}

    return EventSourceResponse(event_gen())


def _map_node_to_event(node_name: str, payload: dict, query_id: str) -> dict | None:
    mapping = {"classify": "classification", "doc_worker": "worker_result",
               "code_worker": "worker_result", "sql_worker": "worker_result"}
    ev_type = mapping.get(node_name)
    if ev_type is None: return None
    data = {"node": node_name, "query_id": query_id}

    if node_name == "classify":
        cl = payload.get("classification", {})
        data.update({"sources": cl.get("sources", []), "is_cross_source": cl.get("is_cross_source", False),
                     "entities": cl.get("entities", {}), "elapsed_ms": 0})
    elif node_name.endswith("_worker"):
        wo = payload.get("worker_outputs", [])
        if wo:
            w = wo[0]
            data.update({"worker": w.get("worker_type", ""), "result_count": w.get("result_count", 0),
                         "retrieval_method": "hybrid", "elapsed_ms": 0})
    return {"event": ev_type, "data": json.dumps(data, ensure_ascii=False, default=str)}
```

- [ ] **Step 2: 验证导入**

Run: `cd /Users/Ray/TraeProjects/SPMA && python -c "from spma.api.routes.query import query_stream; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add src/spma/api/routes/query.py
git commit -m "feat: add SSE streaming endpoint /api/v1/query/stream"
```

---

### Task 9: 会话历史 API

**Files:**
- Modify: `src/spma/api/routes/session.py`

- [ ] **Step 1: 添加 GET /{sid}/history**

在 session.py 末尾追加：

```python
from fastapi import Query
from spma.api.extract_turns import extract_turns
from spma.api.dependencies import get_checkpointer

@router.get("/sessions/{session_id}/history")
async def get_session_history(
    session_id: str,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    try:
        checkpointer = get_checkpointer()
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Checkpointer not available")
    result = await extract_turns(session_id, checkpointer, limit, offset)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    return result
```

- [ ] **Step 2: Commit**

```bash
git add src/spma/api/routes/session.py
git commit -m "feat: add GET /sessions/{sid}/history with pagination"
```

---

### Task 10: 前端类型 + API + State + 渲染（批量前端修复）

**Files:**
- Modify: `frontend/src/types/api.ts`
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/context/app-context.tsx`
- Modify: `frontend/src/components/chat/message-list.tsx`
- Modify: `frontend/src/hooks/useSSE.ts`
- Modify: `frontend/src/app/chat/[sessionId]/page.tsx`

- [ ] **Step 1: types/api.ts — 新增类型**

```typescript
// 新增
export interface QueryStreamRequest {
  query: string;
  session_id: string;
  sources_hint?: SourceType[];
}

export interface SessionHistoryTurn {
  query_text: string;
  answer: string;
  tool_calls: Array<{ id: string; name: string; args: Record<string, unknown> }>;
}

export interface SessionHistoryResponse {
  turns: SessionHistoryTurn[];
  total: number;
  offset: number;
  limit: number;
}
```

- [ ] **Step 2: lib/api.ts — 新增 getSessionHistory**

```typescript
export function getSessionHistory(
  sessionId: string, params?: { limit?: number; offset?: number },
): Promise<SessionHistoryResponse> {
  const sp = new URLSearchParams();
  if (params?.limit) sp.set('limit', String(params.limit));
  if (params?.offset) sp.set('offset', String(params.offset));
  const qs = sp.toString();
  return fetchJSON<SessionHistoryResponse>(`/sessions/${sessionId}/history${qs ? `?${qs}` : ''}`);
}
```

- [ ] **Step 3: app-context.tsx — State + Action + Reducer 修复**

  1. `AppState` 新增 `pendingQuery: string`
  2. `initialState` 新增 `pendingQuery: ''`
  3. `QUERY_START` action 改为 `{ type: 'QUERY_START'; query: string }`
  4. `QUERY_START` reducer: 保存 `pendingQuery: action.query`
  5. `SSE_DONE` reducer: 构建 `completedTurn`，追加到 `sessions[].turns`，清空 `pendingQuery`
  6. 新增 `SET_SESSION_TURNS` action + reducer

- [ ] **Step 4: message-list.tsx — 双路径渲染**

  - `isStreaming = ['classifying','retrieving','synthesizing'].includes(currentPhase)`
  - 历史 turns 正常渲染
  - `isStreaming && pendingQuery` 时同时渲染 UserMessage(pendingQuery) + AIAnswer(streamingAnswer)
  - classifying/retrieving 显示 spinner；synthesizing 且无内容时也显示 spinner

- [ ] **Step 5: useSSE.ts — 对齐新端点**

  - `dispatch({ type: 'QUERY_START', query })` — 传入 query 文本
  - 请求体改为 `{ query, session_id, sources_hint }` — 移除 conversation_history

- [ ] **Step 6: page.tsx — 加载分页历史**

  - `useEffect` 中调用 `api.getSessionHistory(sessionId, {limit:20, offset:0})`
  - 成功 → dispatch `SET_SESSION_TURNS`
  - 失败 → 降级调用旧 `api.getSession`

- [ ] **Step 7: 验证编译**

Run: `cd /Users/Ray/TraeProjects/SPMA/frontend && npx tsc --noEmit 2>&1 | head -20`
Expected: 无新增类型错误

- [ ] **Step 8: Commit**

```bash
git add frontend/src/types/api.ts frontend/src/lib/api.ts frontend/src/context/app-context.tsx \
        frontend/src/components/chat/message-list.tsx frontend/src/hooks/useSSE.ts \
        frontend/src/app/chat/[sessionId]/page.tsx
git commit -m "fix: frontend — SSE_DONE append turn, dual-path MessageList, session history loading"
```

---

### Task 11: 端到端验证

- [ ] **Step 1: 启动后端** `cd /Users/Ray/TraeProjects/SPMA && uv run spma-api &`
- [ ] **Step 2: 测试 POST /api/v1/sessions** — 创建会话 → 201 + session_id
- [ ] **Step 3: 测试 POST /api/v1/query/stream** — 发送查询 → SSE event 流 → `event: done`
- [ ] **Step 4: 测试 GET /api/v1/sessions/{sid}/history** — 获取历史 → {turns, total}
- [ ] **Step 5: 启动前端** `cd frontend && npm run dev`
- [ ] **Step 6: 浏览器端到端** — 创建会话 → 发送问题 → 流式回答 → 答案保留 → 第二轮对话 → 刷新恢复
- [ ] **Step 7: Commit**

```bash
git add -A && git commit -m "verify: end-to-end conversation flow passing"
```
