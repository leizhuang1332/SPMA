# 流式进度与模型思考透传设计

**日期：** 2026-06-21
**状态：** 待评审
**方案：** C — Redis Pub/Sub 双通道解耦架构

---

## 1. 问题陈述

当前 SPMA 的 SSE 流式存在以下用户体验问题：

1. **等待焦虑重**：从用户提问到最终回答出现，只有三个旋转图标依次切换（"正在理解…"→"正在检索…"→"正在生成…"），用户完全不知道系统在做什么
2. **LLM 调用非流式**：所有 Provider 的 `chat()` 方法等待完整响应后返回，synthesis 的 token 是事后分块而非实时流出
3. **Worker 内部黑盒**：Doc/Code/SQL worker 的检索、评估、扩展循环等子步骤完全不可见
4. **模型思考不可见**：DeepSeek 的 thinking tokens 被丢弃，用户看不到模型的推理过程
5. **后端 SSE 事件缺口**：前端定义了 8 种 SSE 事件类型（含 `worker_start`、`worker_progress`、`confirmation_required`），后端只发射 4 种（`classification`、`worker_result`、`synthesis`、`done`）

### 目标

- 所有节点的子步骤处理过程流式返回给前端
- 模型思考（thinking/CoT tokens）流式返回并在前端可折叠展示
- LLM 输出 token 级实时流式渲染
- Worker 启动/进度事件实时推送
- 改动不破坏现有 Graph 架构，进度故障不影响核心问答

---

## 2. 方案选择

### 方案对比

| 维度 | A. 渐进增强 | B. 带内流式重构 | C. 带外通道（选中） |
|---|---|---|---|
| 核心思路 | 现有 astream() 框架内补全，回调收集事件，节点完成时批量发送 | 节点改为 async generator，通过 astream_events() 实时推送 | Redis Pub/Sub 作为独立进度通道，与 graph.astream() 并行 |
| 子步骤实时性 | 延迟（等节点完成） | 实时 | 实时 |
| 架构耦合度 | 高 | 中 | 低 |
| 改动范围 | 3-5 天 | 5-8 天 | 8-12 天 |
| 扩展性 | 低 | 中 | 高（天然支持多订阅者） |

### 选择理由

方案 C 的核心优势：

- **职责分离**：数据流（Graph 结果）和进度流（思考/子步骤）完全解耦，互不干扰
- **故障隔离**：Redis 故障不影响 Graph 执行和核心 SSE 输出（自动降级为无进度模式）
- **零签名变更**：Agent 节点不改函数签名（通过闭包捕获 ProgressPublisher），Graph 返回类型不变
- **扩展友好**：未来 WebSocket、监控、日志只需订阅 Redis channel，不改任何 agent 代码
- **复用现有基础设施**：项目已使用 Redis（缓存/状态存储），零新增依赖

---

## 3. 架构设计

### 3.1 整体架构

```
POST /api/v1/query/stream → 创建 query_id → 启动双通道

通道 1 (数据):  graph.astream() ─→ StreamMerger ─→ SSE (classification/worker_result/synthesis/done)
通道 2 (进度):  Redis Pub/Sub   ─→ StreamMerger ─→ SSE (worker_start/worker_progress/thinking)
                                        │
                                   asyncio.Queue
                                        │
                                   unified SSE events
```

### 3.2 组件清单

| 组件 | 文件 | 类型 | 职责 |
|---|---|---|---|
| ProgressPublisher | `src/spma/api/progress.py` | 新增 | 封装 `redis.publish()`，提供类型安全的 `publish_start/publish_step/publish_thinking` |
| StreamMerger | `src/spma/api/stream_merger.py` | 新增 | 合并 graph stream + Redis stream → 统一 SSE yield |
| LLM astream() | `src/spma/llm/providers/{base,anthropic,openai_compat}.py` | 修改 | 所有 Provider 新增 `astream()` 异步生成器，yield `StreamChunk(type, content)` |
| LLMRouter.astream() | `src/spma/llm/router.py` | 修改 | 新增 `astream()` 路由方法，保留同款路由/降级逻辑 |
| Agent 节点 | `src/spma/agents/{doc,code,sql,synthesis}/graph.py` | 修改 | 通过闭包接受 ProgressPublisher，在子步骤入口调用 publish |
| SSE 端点 | `src/spma/api/routes/query.py` | 修改 | StreamMerger 替代原 event_gen() |
| ThinkingPanel | `frontend/src/components/detail/thinking-panel.tsx` | 新增 | 可折叠思考面板，流式渲染 CoT tokens |
| ProgressTracker | `frontend/src/components/detail/progress-tracker.tsx` | 修改 | 展开子步骤时间线 |
| API 类型 | `frontend/src/types/api.ts` | 修改 | 新增 SSEThinkingEvent、keepalive 类型，扩展 SSEWorkerProgressEvent |
| AppContext | `frontend/src/context/app-context.tsx` | 修改 | 新增 SubStepState、thinking state、SSE_THINKING action 等 |
| useSSE | `frontend/src/hooks/useSSE.ts` | 修改 | 新增 thinking/keepalive 事件分发 |

### 3.3 事件时序

```
0.0s  SSE: classification        ← Graph
0.2s  SSE: worker_start (doc)     ← Redis
0.3s  SSE: worker_progress (doc, searching)  ← Redis
0.4s  SSE: worker_start (code)    ← Redis
1.2s  SSE: worker_progress (doc, aggregating, found=12)  ← Redis
2.1s  SSE: worker_progress (doc, assessing)  ← Redis
2.8s  SSE: worker_result (doc)    ← Graph
3.0s  SSE: worker_start (synthesis)  ← Redis
3.1s  SSE: thinking (chunk)       ← Redis
4.5s  SSE: synthesis (chunk)      ← Graph
4.6s  SSE: synthesis (chunk)      ← Graph
8.0s  SSE: done                   ← Graph
```

---

## 4. 后端详细设计

### 4.1 ProgressPublisher（`src/spma/api/progress.py`）

类型安全的 Redis Pub/Sub 进度发布器。

核心设计要点：
- **静默降级**：Redis 异常被 catch 不抛出，进度故障不影响回答质量
- **无 Redis 模式**：接受 `redis_client=None`，此时 `_publish()` 直接 return，前端降级为无进度 UI
- **Channel 命名**：`spma:progress:{query_id}`，天然隔离不同查询
- **复用连接池**：使用 bootstrap 中已创建的 redis_client

```python
@dataclass
class ProgressEvent:
    query_id: str
    event_type: str    # "worker_start" | "worker_progress" | "thinking"
    node: str          # "doc_worker" | "code_worker" | "sql_worker" | "synthesis"
    timestamp: str = ""
    step: str | None = None        # 子步骤名
    message: str | None = None     # 人类可读描述
    stats: dict | None = None      # {"found": 12, "round": 2}
    thinking_chunk: str | None = None

class ProgressPublisher:
    async def publish_start(self, node: str): ...
    async def publish_step(self, node: str, step: str, message: str = "", stats: dict = None): ...
    async def publish_thinking(self, node: str, chunk: str): ...
```

### 4.2 StreamMerger（`src/spma/api/stream_merger.py`）

双通道合并器，用 asyncio.Queue 统一两条异步流。

```python
class StreamMerger:
    async def run(self) -> AsyncGenerator[dict, None]:
        """启动双通道 asyncio.Tasks，从队列中逐个 yield 统一 SSE event"""
        graph_task = asyncio.create_task(self._consume_graph_stream())
        progress_task = asyncio.create_task(self._consume_progress_stream())

        tasks_done = 0
        while tasks_done < 2:
            try:
                event = await asyncio.wait_for(self.queue.get(), timeout=30.0)
                if event is SENTINEL:
                    tasks_done += 1
                else:
                    yield event
            except asyncio.TimeoutError:
                yield {"event": "keepalive", ...}  # 30s 心跳

        yield self._build_done_event()
```

- `_consume_graph_stream()`：同现有 `event_gen()` 逻辑，classify/worker_result/synthesis → queue
- `_consume_progress_stream()`：订阅 Redis channel，解析 ProgressEvent → queue
- 30s 心跳防止代理/负载均衡器断开连接
- SENTINEL 标记通道结束

### 4.3 LLM Provider astream()

**基类新增抽象方法（`base.py`）：**

```python
@dataclass
class StreamChunk:
    type: Literal["thinking", "output"]
    content: str
    model: str | None = None
    finish_reason: str | None = None

class LLMProvider(ABC):
    @abstractmethod
    async def astream(self, messages, model, **kwargs) -> AsyncGenerator[StreamChunk, None]: ...
```

**AnthropicProvider（`anthropic.py`）：**
- 使用 `AsyncAnthropic.messages.stream()` SDK
- `content_block_delta` 事件 → `thinking_delta` → `StreamChunk(type="thinking")`
- `content_block_delta` 事件 → `text_delta` → `StreamChunk(type="output")`

**OpenAICompatProvider（`openai_compat.py`）：**
- 使用 `stream=True` + `stream_options={"include_usage": True}`
- DeepSeek 的 `delta.reasoning_content` → `StreamChunk(type="thinking")`
- `delta.content` → `StreamChunk(type="output")`

### 4.4 LLMRouter.astream()

与现有 `chat()` 方法保持相同的路由/降级逻辑，但返回 AsyncGenerator：

```python
async def astream(self, messages, *, role=None, model=None, **kwargs) -> AsyncGenerator[StreamChunk]:
    role_name = role or "default"
    # ... 同 chat() 的 provider 选择 + 参数解析 ...

    try:
        async for chunk in provider.astream(messages, resolved_model, **resolved_kwargs):
            yield chunk
    except Exception:
        # 降级到 fallback provider 的 astream
        ...
```

**注意**：仅 synthesis generate 节点使用 `astream()`，classify/rewrite/assess 等快速调用仍用同步 `chat()`。

### 4.5 Agent 节点改造

每个 agent 子图的构建函数接受可选的 `ProgressPublisher`，节点通过闭包捕获：

```python
# Before
def build_doc_agent_graph(es_client, vector_store, embedder, llm, ...):
# After
def build_doc_agent_graph(es_client, vector_store, embedder, llm, ...,
                          progress: ProgressPublisher | None = None):
```

各 worker 节点发布事件（code_worker 和 sql_worker 同理，此处仅列举 doc 和 synthesis 为代表）：

| Worker | 节点 | 事件 |
|---|---|---|
| doc_worker | route | `publish_step("doc_worker", "routing", "正在分析查询策略…")` |
| doc_worker | search | `publish_step("doc_worker", "searching", "正在检索 ES + PGVector…")` |
| doc_worker | aggregate | `publish_step("doc_worker", "aggregating", stats={"found": 12, "round": 1})` |
| doc_worker | assess | `publish_step("doc_worker", "assessing", "正在评估检索完整性…")` |
| doc_worker | expand | `publish_step("doc_worker", "expanding", "正在扩展查询…")` |
| code_worker | route | `publish_step("code_worker", "routing", "正在分析代码仓库…")` |
| code_worker | search | `publish_step("code_worker", "searching", "正在 ripgrep + AST 检索…")` |
| code_worker | assess | `publish_step("code_worker", "assessing", "正在评估检索完整性…")` |
| code_worker | expand | `publish_step("code_worker", "expanding", "正在扩展搜索…")` |
| sql_worker | generate | `publish_step("sql_worker", "generating", "正在生成 SQL…")` |
| sql_worker | guard | `publish_step("sql_worker", "guarding", "正在安全检查…")` |
| sql_worker | execute | `publish_step("sql_worker", "executing", "正在执行查询…")` |
| sql_worker | verify | `publish_step("sql_worker", "verifying", "正在验证结果…")` |
| synthesis | fuse | `publish_step("synthesis", "fusing", "正在融合多源结果…")` |
| synthesis | generate | `publish_thinking("synthesis", chunk)` + token 流式累积 |
| synthesis | audit | `publish_step("synthesis", "auditing", "正在审核回答质量…")` |
| synthesis | finalize | `publish_step("synthesis", "finalizing")` |

**Synthesis generate 节点特殊处理：**
- 内部调用 `router.astream()` 替代 `llm.ainvoke()`
- `type="thinking"` → `progress.publish_thinking("synthesis", chunk.content)`
- `type="output"` → 累积到 answer 字符串，最终作为 AIMessage 返回（Graph astream 将其转为 synthesis SSE 事件）

---

## 5. 前端详细设计

### 5.1 ThinkingPanel（新增组件）

可折叠的模型思考展示面板，放置在 AI 回答消息上方。

```
折叠状态:  [🧠 模型思考中… (86 tokens)  ▾]
展开状态:  [🧠 模型思考过程              ▴]
           我需要综合 doc_worker 和 code_worker
           的结果来回答用户关于 SPMA 架构的问题。
           doc_worker 返回了 12 条相关文档…
           ▌  (闪烁光标)
```

接口：
```typescript
interface ThinkingPanelProps {
  chunks: string[];           // 思考 token 数组，流式累加
  isStreaming: boolean;       // 是否仍在流式接收
  defaultCollapsed?: boolean; // 默认折叠（true）
}
```

- 默认折叠，用户点击展开
- 展开时使用 autoScroll hook 自动滚动到底部
- chunks 为空且不在流式时不渲染

### 5.2 ProgressTracker v2（改造）

从单状态条升级为子步骤时间线：

```
Before:                          After:
· Supervisor 分类 ✓              ✓ Supervisor 分类 120ms
◉ 文档 Worker 检索中…            ◉ 文档 Worker 1.2s
· 代码 Worker 即将启动…            ✓ 分析查询策略 80ms
                                   ◉ 检索 ES + PGVector 850ms
                                     · 聚合与去重
                                     · 评估完整性
                                 · 代码 Worker
                                   · 分析查询策略
```

- 每个 Worker 展开其子步骤列表
- 当前步骤高亮显示（蓝色 ◉）
- 已完成步骤显示绿色 ✓
- 待执行步骤灰色 ·
- 统计数字（检索数、轮次）动态更新

### 5.3 状态管理扩展

**新增类型（`api.ts`）：**

```typescript
export interface SSEThinkingEvent {
  node: string;       // "synthesis"
  chunk: string;      // 思考 token 片段
  timestamp: string;
}

// SSEWorkerProgressEvent 扩展字段
export interface SSEWorkerProgressEvent {
  worker: WorkerName;
  step: string;       // 新增："routing"|"searching"|...
  message: string;    // 新增：人类可读描述
  stats?: { found?: number; round?: number };  // 新增
  elapsed_ms: number;
}

// SSEEventType 新增
export type SSEEventType = ... | 'thinking' | 'keepalive';
```

**AppContext 扩展：**

```typescript
interface SubStepState {
  name: string;
  message: string;
  status: 'pending' | 'running' | 'done';
  stats?: { found?: number; round?: number };
}

interface WorkerState {
  status: ...;
  sub_steps: SubStepState[];   // 新增
  current_step?: string;       // 新增
}

interface QueryState {
  thinking: { chunks: string[]; isStreaming: boolean };  // 新增
  // ...existing fields
}

// 新增 Actions
| { type: 'SSE_THINKING'; chunk: string }
| { type: 'SSE_KEEPALIVE' }
| { type: 'SSE_WORKER_STEP'; worker: WorkerName; step: string; message: string; stats?: dict }
```

### 5.4 useSSE Hook

新增两个 case：

```typescript
case 'thinking':
  dispatch({ type: 'SSE_THINKING', chunk: (data as SSEThinkingEvent).chunk });
  break;
case 'keepalive':
  // noop — 仅维持连接
  break;
```

---

## 6. 错误处理与降级

**核心原则：进度通道的任何故障都不能影响数据通道。**

| 故障场景 | 处理策略 | 降级行为 |
|---|---|---|
| Redis 不可用 | ProgressPublisher 接受 redis=None；_publish() 静默 catch；StreamMerger 进度通道立即 SENTINEL | 退化为无进度 SSE（=方案A体验） |
| Redis 中途断连 | pubsub.listen() 异常→put(SENTINEL)；数据通道继续正常 | 前端保留最后收到的子步骤状态 |
| LLM astream() 异常 | LLMRouter 降级到 fallback provider；generate_node try/except | SSE error 事件 |
| 双通道时序不一致 | 前端 reducer 幂等处理：worker_result 覆盖为 done，忽略后续 progress | 前端容错 |
| SSE 连接断开 | useSSE 现有重连逻辑（指数退避 3 次） | 自动重连 |
| 客户端取消 | 2 个 task 被 cancel；pubsub.unsubscribe() in finally | 干净取消 |

### 边界情况

- **超快查询（<500ms）**：进度事件可能在 done 之后到达。前端 reducer 在 phase="done" 时忽略 worker_start/progress
- **多轮 reschedule**：同一 worker 两轮执行，第二轮 worker_start 重置状态为 running，子步骤覆盖第一轮
- **空 thinking**：vLLM Qwen 等不返回 thinking tokens。ThinkingPanel 在 chunks 为空且不 streaming 时不渲染
- **超大 stats**：ProgressEvent.stats 只存计数（found/round），不存具体数据，避免 Redis 消息过大

---

## 7. Channel 生命周期

```
创建 → 活跃 → 结束 → 清理

1. SSE 端点创建 query_id，StreamMerger subscribe channel
2. Agent 节点 publish，StreamMerger 消费，SSE 实时推送
3. Graph 执行完毕，progress task 收到 SENTINEL，unsubscribe
4. Redis Pub/Sub 无订阅者自动丢弃消息（无需主动删除 channel）

安全网：异常情况下未 unsubscribe 的连接，5min 后 Redis 自动清理。
```

---

## 8. 测试策略

| 层级 | 测试点 |
|---|---|
| 后端单元 | ProgressPublisher 降级行为；StreamMerger 双通道合并/SENTINEL 终止；LLM astream() thinking/output 解析 + 降级；Redis 故障注入 |
| 前端组件 | ThinkingPanel 折叠/展开/空状态；ProgressTracker 子步骤渲染与状态切换；SSE 乱序事件前端容错 |
| SSE 集成 | Mock SSE server 完整事件序列；重连恢复；Redis 降级全流程 |
| E2E | 真实查询：thinking 面板 + 子步骤更新 + Redis 降级场景 |

---

## 9. 未覆盖（后续迭代）

以下内容不在本次范围内：

- WebSocket 替代 SSE（方案 C 架构已为此做好准备，但本期只做 SSE）
- 多用户实时协作场景（pub/sub 天然支持，但产品功能后续定义）
- 可视化流程图渲染（DAG 图实时高亮当前节点）
- 进度历史回放/持久化（可在后续通过订阅 Redis channel 写入数据库）

---

## 10. 改动文件汇总

### 新增（3 个后端 + 1 个前端）
- `src/spma/api/progress.py`
- `src/spma/api/stream_merger.py`
- `frontend/src/components/detail/thinking-panel.tsx`

### 修改（8 个后端 + 4 个前端）
- `src/spma/llm/providers/base.py`
- `src/spma/llm/providers/anthropic.py`
- `src/spma/llm/providers/openai_compat.py`
- `src/spma/llm/router.py`
- `src/spma/agents/doc/graph.py`
- `src/spma/agents/code/graph.py`
- `src/spma/agents/sql/graph.py`
- `src/spma/agents/synthesis/graph.py`
- `src/spma/api/routes/query.py`
- `frontend/src/types/api.ts`
- `frontend/src/context/app-context.tsx`
- `frontend/src/hooks/useSSE.ts`
- `frontend/src/components/detail/progress-tracker.tsx`
