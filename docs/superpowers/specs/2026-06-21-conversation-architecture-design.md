# 对话架构重构设计

2026-06-21 · 方案 A：顶层 LangGraph StateGraph + AsyncPostgresSaver

## 概述

将当前手动编排的查询流程重构为标准 LangGraph StateGraph，利用 `AsyncPostgresSaver` 作为对话历史的唯一数据源，通过 SSE（Server-Sent Events）实现流式交互。解决三个核心问题：

1. 查询完成后答案不显示在消息列表中
2. conversation_history 未从前端传递（改为后端从 checkpoint 自动获取）
3. SSE 流式端点缺失

## §1 数据模型

### 1.1 表结构变更

| 表 | 操作 | 说明 |
|---|---|---|
| `sessions` | 保留 | 仅存元数据：`session_id`, `title`, `user_id`, `created_at`, `updated_at` |
| `agent_traces` | 降级 | 保留用于审计/分析/观测，不再作为对话历史查询的数据源 |
| `agent_rounds` | 保留 | agent 内部轮次记录，仅用于调试/观测 |
| LangGraph checkpoint 表 | 新建 | `AsyncPostgresSaver.setup()` 自动创建 `checkpoints`、`checkpoint_writes`、`checkpoint_blobs` |

### 1.2 关键映射

```
sessions.session_id  ←→  LangGraph config["configurable"]["thread_id"]
                              │
                              ▼
                     checkpoint["channel_values"]["messages"]
                              │
                              ▼
                     extract_turns() → API Response {turns, total}
```

### 1.3 对话内容唯一数据源

- LangGraph checkpoint 中的 `messages: list[HumanMessage | AIMessage | ToolMessage]`
- 写入：每个 super-step 完成后，`AsyncPostgresSaver` 自动持久化（原子事务）
- 读取：`extract_turns()` 从 checkpoint 提取并合并为前端可用的 turn 结构
- Session:Conversation = 1:N：一个 `thread_id`（等于 `session_id`）下有多个 checkpoint version，每个 version 包含累积的 messages

### 1.4 Session 创建流程

```
POST /api/v1/sessions
  → INSERT INTO sessions (session_id, title, user_id, ...)
  → 不创建 checkpoint（首次查询时由 graph.astream() 自动创建）
  → 返回 {session_id, created_at}
```

## §2 后端 API

### 2.1 端点总览

| 方法 | 路径 | 说明 | 变更 |
|------|------|------|------|
| `POST` | `/api/v1/sessions` | 创建会话 | 保留 |
| `GET` | `/api/v1/sessions/{sid}` | 获取会话元数据 | 保留 |
| `GET` | `/api/v1/sessions/{sid}/history` | 获取对话历史（分页） | **新增** |
| `DELETE` | `/api/v1/sessions/{sid}` | 删除会话 + 清理关联 checkpoint | 增强 |
| `POST` | `/api/v1/query/stream` | SSE 流式查询 | **新增**，替代旧 `/api/v1/query` |

### 2.2 POST /api/v1/query/stream

SSE 流式查询端点。请求体：

```json
{
  "query": "用户问题",
  "session_id": "uuid",
  "sources_hint": ["doc", "code"]
}
```

核心模式（对照 streaming-sse-patterns §2.1）：

- 路由层校验 session 存在性（generator 外部，快速 4xx）
- AsyncGenerator 内调用 `graph.astream()`，映射 `stream_mode=["messages", "updates"]`
- `updates` 模式 → node 完成事件（classification, worker_result, etc.）
- `messages` 模式 → token 级 synthesis chunk
- 异常在 generator 内部捕获，转为 `error` event，不抛给框架
- 使用 `sse-starlette` 的 `EventSourceResponse`

SSE 事件映射：

| stream_mode | LangGraph 输出 | SSE event | 前端 Reducer |
|---|---|---|---|
| `"updates"` | classify 节点完成 | `classification` | `SSE_CLASSIFICATION` |
| `"updates"` | worker 启动 | `worker_start` | `SSE_WORKER_START` |
| `"updates"` | worker 进度 | `worker_progress` | `SSE_WORKER_PROGRESS` |
| `"updates"` | worker 完成 | `worker_result` | `SSE_WORKER_RESULT` |
| `"messages"` | AIMessageChunk.content | `synthesis` | `SSE_SYNTHESIS_CHUNK` |
| `"messages"` | AIMessage.tool_calls | `tool_call_start` | `SSE_TOOL_CALL` |
| — | graph 执行完成 | `done` | `SSE_DONE` |
| — | 异常 | `error` | `SSE_ERROR` |

### 2.3 GET /api/v1/sessions/{sid}/history

返回分页对话历史。查询参数：`limit`（默认 20，最大 100）、`offset`（默认 0）。

响应格式：
```json
{
  "turns": [
    {
      "query_text": "用户问题",
      "answer": "AI 回答",
      "tool_calls": [],
      "created_at": "ISO8601"
    }
  ],
  "total": 42,
  "offset": 0,
  "limit": 20
}
```

内部调用 `extract_turns(session_id, checkpointer, limit, offset)`。

### 2.4 extract_turns() 设计

#### 数据获取

- 优先路径：`checkpointer.aget_tuple(config)` 直接查询最新 checkpoint → O(1)
- 降级路径：`checkpointer.alist(config, limit=N)` 遍历所有 checkpoint，取 messages 最长的
- SessionManager 缓存 `latest_checkpoint_id`，避免重复全量扫描

#### 轮次合并

累积模式：
- `HumanMessage` → flush 上一轮，新建 turn
- `AIMessage` → 累积 content + tool_calls 到当前 turn
- `ToolMessage` → 跳过（SSE 实时已展示）

多个 AIMessage 合并为一条 assistant 响应（LLM 可能在一次回复中先输出文字再调用工具）。

#### 分页

1. 提取完整 messages 列表
2. 构建 `human_indices`（HumanMessage 位置索引）
3. 按 offset/limit 截取对应轮次

#### 消息校验清洗

- 过滤不在白名单中的消息类型
- 标准化 tool_calls 参数（兼容不同 LangChain 版本）
- 空 content 且无 tool_calls 的 AIMessage 跳过
- 异常消息记录日志，不阻塞正常流程

#### 异步优化

- 复用 `asyncpg` 连接池
- 查询超时 5s，超时返回已获取的部分数据
- 单次最多处理 500 条消息

## §3 前端状态管理

### 3.1 AppContext Reducer 变更

**QUERY_START**：新增 `pendingQuery` 字段，保存用户原始输入：
```typescript
case 'QUERY_START':
  return {
    ...state,
    pendingQuery: action.query,  // 新增
    currentQuery: { ...initialState.currentQuery, phase: 'classifying' },
    detailPanelMode: 'progress',
  };
```

**SSE_DONE**：构建 completedTurn 并追加到 session.turns：
```typescript
case 'SSE_DONE': {
  const completedTurn = {
    query_id: action.data.query_id,
    session_id: state.currentSessionId!,
    query_text: state.pendingQuery,
    answer: state.currentQuery.synthesis.chunks.join(''),
    latency_ms: action.data.latency_ms,
    created_at: new Date().toISOString(),
  };
  const updatedSessions = state.sessions.map(s =>
    s.session_id === state.currentSessionId
      ? { ...s, turns: [...s.turns, completedTurn] }
      : s
  );
  return { ...state, sessions: updatedSessions, currentQuery: { ... }, detailPanelMode: 'sources' };
}
```

**新增 SET_SESSION_TURNS**：会话切换时加载分页历史
```typescript
case 'SET_SESSION_TURNS':
  return {
    ...state,
    sessions: state.sessions.map(s =>
      s.session_id === action.sessionId
        ? { ...s, turns: action.turns, totalTurns: action.total }
        : s
    ),
  };
```

### 3.2 MessageList 双路径渲染

- **历史 turns**：从 `session.turns` 渲染（含刚追加的本轮 turn）
- **实时 streaming**：`isStreaming` 时渲染 `streamingAnswer`（从 `synthesis.chunks` 拼接）
- **加载指示器**：classifying/retrieving 阶段显示

两者互不冲突：streaming 中历史和实时共存，done 后流式路径消失，本轮已在历史中。

### 3.3 移除前端 conversation_history

- `types/api.ts`：`QueryRequest` 不包含 `conversation_history` 字段
- `useSSE.ts`：请求体不发送 `conversation_history`
- 后端自动从 checkpoint 的 `messages` 中提取历史并格式化

### 3.4 会话切换加载

`chat/[sessionId]/page.tsx` 的 `useEffect` 中调用 `api.getSessionHistory(sessionId, {limit, offset})`，dispatch `SET_SESSION_TURNS`。

## §4 Agent 层改造

### 4.1 顶层 StateGraph：QueryOrchestrator

将所有手动编排步骤编译为一个 `StateGraph`：

```
classify → rewrite → dispatch ──Send API──┬─ doc_worker ─┐
                                           ├─ code_worker ─┼─→ synthesis → quality ─┬─→ END
                                           └─ sql_worker ─┘                         │
                                                    ↑                               │
                                                    └── reschedule ←── < 0.6 ──────┘
```

### 4.2 统一 State Schema

```python
class QueryOrchestratorState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]  # checkpoint 自动管理
    original_query: str
    session_id: str
    sources_hint: list[str] | None
    classification: dict | None
    entities: dict | None
    rewritten_queries: list[str]
    worker_outputs: Annotated[list[dict], operator.add]   # fan-in 累加
    final_answer: str
    quality_scores: dict
    reschedule_count: int
```

- `messages` 使用 LangGraph 内置的 `add_messages` reducer → 追加而非覆盖，checkpoint 恢复历史的关键
- `worker_outputs` 使用 `operator.add` → Send API fan-out 结果自动累加

### 4.3 conversation_history 后端自动构建

```python
def _format_history(messages: list[BaseMessage]) -> str:
    """从 messages 列表构建分类器 Prompt 用的文本格式"""
    lines = []
    for msg in messages[-10:]:
        if isinstance(msg, HumanMessage):
            lines.append(f"用户: {msg.content}")
        elif isinstance(msg, AIMessage):
            lines.append(f"AI: {msg.content[:200]}")
    return "\n".join(lines) if lines else "无"
```

在 `classify_node` 中调用，不再依赖前端传入。

### 4.4 子 graph 适配

现有 `build_doc_agent_graph()`、`build_code_agent_graph()` 等**不需要修改**。它们在顶层 graph 的节点中被调用，子 graph 的 state 不进入 checkpoint。

```python
async def doc_worker_node(state: QueryOrchestratorState) -> dict:
    doc_graph = _get_doc_agent_graph()
    result = await doc_graph.ainvoke({...})
    return {"worker_outputs": [{...}]}
```

### 4.5 Checkpoint 生命周期

```
每次 graph.astream() 调用:
  ├─ 进入前：从 AsyncPostgresSaver 恢复最新 checkpoint
  │   └─ state["messages"] 包含完整历史
  ├─ 每个 super-step 完成后：LangGraph 自动写入 checkpoint
  │   └─ checkpoint 写入是原子事务
  └─ graph 执行完毕：最终 state 已持久化
```

每个 super-step 产生一个 checkpoint version，属于同一 `thread_id`。

### 4.6 Checkpointer 单例

```python
_checkpointer: AsyncPostgresSaver | None = None

async def get_checkpointer() -> AsyncPostgresSaver:
    global _checkpointer
    if _checkpointer is None:
        DB_URI = _get_postgres_uri()
        _checkpointer = AsyncPostgresSaver.from_conn_string(DB_URI)
        await _checkpointer.setup()
    return _checkpointer
```

Graph 单例：
```python
async def get_query_graph() -> StateGraph:
    global _query_graph
    if _query_graph is None:
        _query_graph = _build_query_graph().compile(
            checkpointer=await get_checkpointer()
        )
    return _query_graph
```

## §5 实现路径

### 5.1 阶段划分

| 阶段 | 内容 | 依赖 |
|------|------|------|
| Phase 1 | 后端 checkpoint 基础设施：`AsyncPostgresSaver` 初始化 + `extract_turns()` | — |
| Phase 2 | 后端顶层 StateGraph：`QueryOrchestrator` graph 编译 + SSE 端点 | Phase 1 |
| Phase 3 | 前端状态修复：`SSE_DONE` 追加 turn + `MessageList` 双路径渲染 | Phase 2 |
| Phase 4 | 前端 SSE 对齐 + 会话历史加载 | Phase 3 |
| Phase 5 | 旧代码清理：废弃 `agent_traces` 的对话查询路径 | Phase 4 |

### 5.2 文件变更

**新增文件：**

| 文件 | 职责 |
|------|------|
| `src/spma/api/routes/query_graph.py` | 顶层 `QueryOrchestrator` StateGraph 构建 + 节点实现 |
| `src/spma/api/extract_turns.py` | `extract_turns()` + `_merge_turns()` + `_format_history()` |

**修改文件（后端）：**

| 文件 | 变更 |
|------|------|
| `src/spma/api/routes/query.py` | 旧 `general_query` 替换为 `query_stream`（SSE） |
| `src/spma/api/routes/session.py` | 新增 `GET /{sid}/history` 端点 |
| `src/spma/api/dependencies.py` | 新增 `get_checkpointer()`、`set_checkpointer()` 单例 |
| `src/spma/api/app.py` | `startup` 事件中初始化 `AsyncPostgresSaver` + 编译 graph 单例 |
| `src/spma/api/session_store.py` | `create_session` 接受 `user_id` 参数 |

**修改文件（前端）：**

| 文件 | 变更 |
|------|------|
| `src/context/app-context.tsx` | `QUERY_START` 保存 `pendingQuery`；`SSE_DONE` 追加 turn；新增 `SET_SESSION_TURNS` |
| `src/components/chat/message-list.tsx` | 新增 `streamingAnswer` 实时渲染路径 |
| `src/hooks/useSSE.ts` | 移除 `conversation_history`；对齐 `QueryStreamRequest` |
| `src/types/api.ts` | 新增 `QueryStreamRequest`、`SessionHistoryResponse`；移除 `conversation_history` |
| `src/lib/api.ts` | 新增 `getSessionHistory()` |
| `src/app/chat/[sessionId]/page.tsx` | 加载分页历史 |

**保留但降级的文件：**

| 文件 | 变更 |
|------|------|
| `src/spma/observability/trace_logger.py` | 保留用于审计/观测，不再用于对话查询 |

### 5.3 风险控制

- 所有新功能通过 `feature_flags.yaml` 加开关，可一键回退
- 旧 `/api/v1/query` 端点保留过渡版本，标记 `@deprecated`
- Phase 1-2 只追加代码，不改旧行为；Phase 5 再清理
- SSE 端点与旧端点并行运行，切换通过前端配置控制

## §6 错误处理

### 6.1 SSE 流式三层错误策略

| 层级 | 位置 | 策略 |
|---|---|---|
| 路由层 | generator 外部 | Pydantic 校验 + session 存在性 → 快速 4xx |
| Generator 层 | generator 内部 try/except | yield error event，连接正常关闭 |
| Agent 层 | graph.astream() try/except | yield error + 记录日志，不抛给框架 |

### 6.2 Extract Turns 降级

| 场景 | 行为 |
|---|---|
| thread_id 无 checkpoint | 返回 `{"turns": [], "total": 0}` |
| messages 序列化异常 | 跳过异常消息，记录日志，返回部分数据 |
| 查询超时 | 返回已获取数据 + `"partial": true` 标记 |

## §7 测试策略

### 7.1 后端

- `extract_turns()` 单元测试：正常消息序列、空 messages、混合类型、分页边界
- `query_stream` 集成测试：SSE 帧格式正确性、event 类型完整、cancel 断开
- Checkpoint 读写测试：多轮对话后 messages 完整性

### 7.2 前端

- `SSE_DONE` reducer 单元测试：turn 追加、session 更新
- `MessageList` 渲染测试：历史 turns、streaming 状态、done 后切换
- SSE 解析测试：分帧正确性、异常数据容错

---

## 附录 A：前端 SSE Frame 解析（参照 streaming-sse-patterns §3.1-3.2）

```
手动 ReadableStream + buf 累积
Frame 边界：/\r?\n\r?\n/
Content-Type: text/event-stream
不使用 EventSource API（不支持 POST + custom headers）
```

## 附录 B：与 streaming-sse-patterns 的对齐

| 模式 | 参考章节 |
|---|---|
| AsyncGenerator + EventSourceResponse | §2.1 |
| EventBus（post-agent 旁路通知） | §2.2（未来按需引入） |
| LangGraph astream 对接 | §2.3 |
| 错误传播三层策略 | §2.5 |
| 前端 ReadableStream 解析 | §3.1-3.2 |
| AbortController cancel | §3.5 |
