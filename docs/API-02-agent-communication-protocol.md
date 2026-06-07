# API 契约：Agent 间通信协议

> 所属项目：[SPMA 全局概览](designs/SPMA-design-00-global-overview.md)
> 权威架构：[5独立Agent架构设计](designs/SPMA-design-07-agent-architecture.md)
> 契约边界：**Supervisor Agent ↔ Worker Agents (Doc/Code/SQL) ↔ Synthesis Agent**
> 协议：LangGraph Send API + Pydantic TypedDict
> 版本：1.0

---

## 一、协议设计参考

本协议参考以下行业标准：

| 参考 | 借鉴内容 |
|------|---------|
| **Google A2A Protocol** | Agent Card、Task 对象、异步任务生命周期 |
| **OpenAI Agents SDK** | `handoff` 结构化输入、`outputType` 契约 |
| **LangGraph Send API** | 动态并行派发、Subgraph 隔离、Reducer 合并 |
| **JSON-RPC 2.0** | 请求/响应结构（inspiration，非直接实现） |

---

## 二、Agent 间交互拓扑

```
┌─────────────────────────────────────────────────────────────────┐
│                    Agent 间交互 DAG                               │
│                                                                 │
│                    ┌──────────────────┐                          │
│                    │ Supervisor Agent │  ← 编排中枢              │
│                    └──┬──────┬──────┬─┘                          │
│                       │      │      │                            │
│              Send API │      │      │ Send API                   │
│          ┌────────────▼┐  ┌──▼──────▼──┐  ┌────────────┐        │
│          │  Doc Agent  │  │ Code Agent │  │ SQL Agent  │        │
│          │  (Subgraph) │  │ (Subgraph) │  │ (Subgraph) │        │
│          └──────┬──────┘  └─────┬──────┘  └─────┬──────┘        │
│                 │               │               │                │
│                 └───────────────┼───────────────┘                │
│                         WorkerOutput                             │
│                                 │                                │
│                                 ▼                                │
│                    ┌──────────────────────┐                      │
│                    │  Synthesis Agent     │                      │
│                    │  (Subgraph)          │                      │
│                    └──────────────────────┘                      │
│                                                                 │
│   规则:                                                          │
│   - Agent 间不互调（网状调用推迟到 Phase 5+）                      │
│   - Supervisor → Workers: 1:N 并行派发                            │
│   - Workers → Supervisor: N:1 fan-in 收集                         │
│   - Supervisor → Synthesis: 1:1 顺序调用                          │
└─────────────────────────────────────────────────────────────────┘
```

---

## 三、Supervisor → Worker：Send API 派发协议

### 3.1 派发数据结构

Supervisor 通过 LangGraph `Send` API 向 Worker Agent 子图派发任务：

```python
from langgraph.types import Send
from pydantic import BaseModel, Field
from typing import Literal, Optional
from uuid import UUID

class WorkerDispatch(BaseModel):
    """Supervisor → Worker Agent 的任务派发契约"""
    
    # ── 任务标识 ──
    task_id: str = Field(..., description="派发任务唯一 ID，格式: {query_id}:{agent_type}")
    query_id: UUID = Field(..., description="用户查询 ID")
    agent_type: Literal["doc", "code", "sql"] = Field(..., description="目标 Worker 类型")
    
    # ── 检索参数 ──
    original_query: str = Field(..., description="用户原始问题")
    rewritten_query: str = Field(..., description="Supervisor 改写后的检索 query")
    sub_queries: list[SubQuery] = Field(default_factory=list, description="分解后的子查询")

    # ── 实体注入 ──
    entities: WorkerEntities = Field(..., description="Supervisor 抽取的实体（Worker 视角）")
    
    # ── 收敛约束 ──
    max_rounds: int = Field(..., description="最大检索轮数")
    timeout_ms: int = Field(..., description="Worker 超时（毫秒）")
    token_budget: int = Field(..., description="本 Worker 的 Token 预算（LLM 调用次数）")
    
    # ── 上下文（重调度时使用） ──
    previous_results: list[PreviousWorkerResult] = Field(
        default_factory=list,
        description="上一轮结果（Supervisor 重调度时注入）"
    )
    hints_from_other_workers: dict = Field(
        default_factory=dict,
        description="其他 Worker 发现的线索（跨源桥接实体）"
    )
    
    # ── 运行时配置 ──
    feature_flags: WorkerFeatureFlags = Field(..., description="Worker 的 feature flag")
    model_override: Optional[str] = Field(None, description="模型覆盖（降级时使用）")

class SubQuery(BaseModel):
    """分解后的子查询"""
    query: str = Field(..., description="子查询文本")
    target: Literal["doc", "code", "sql"] = Field(..., description="目标数据源")
    priority: int = Field(default=1, ge=1, le=5)

class WorkerEntities(BaseModel):
    """Supervisor 抽取的实体（Worker 视角的子集）"""
    
    # 跨源实体
    module: Optional[str] = None
    req_ids: list[str] = Field(default_factory=list)
    time_range: Optional[str] = None
    version: Optional[str] = None
    
    # Agent 专属实体
    table_names: list[str] = Field(default_factory=list)
    column_names: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    group_by: Optional[str] = None
    code_refs: list[str] = Field(default_factory=list)
    person: Optional[str] = None
    doc_types: list[str] = Field(default_factory=list)

class PreviousWorkerResult(BaseModel):
    """上一轮 Worker 结果（重调度时传给 Worker）"""
    agent_type: Literal["doc", "code", "sql"]
    result_count: int
    confidence: float = Field(ge=0, le=1)
    has_exact_match: bool
    key_findings: list[str] = Field(default_factory=list)
    extracted_entities: dict = Field(default_factory=dict)

class WorkerFeatureFlags(BaseModel):
    """Worker 的运行时开关"""
    agentic_mode: bool = True  # true=多轮循环, false=单轮 pipeline
    enable_hyde: bool = False
    enable_decomposition: bool = False
    enable_step_back: bool = False
```

### 3.2 LangGraph Send 派发实现

```python
# supervisor_graph.py

def dispatch_to_workers(state: SupervisorState):
    """Supervisor 的条件边：构造 Send 列表并行派发"""
    sends = []
    
    for agent_type in state.classification.sources:
        dispatch = WorkerDispatch(
            task_id=f"{state.query_id}:{agent_type}",
            query_id=state.query_id,
            agent_type=agent_type,
            original_query=state.original_query,
            rewritten_query=state.rewritten_queries.get(agent_type, state.original_query),
            entities=build_worker_entities(state.entities, agent_type),
            max_rounds=get_max_rounds(agent_type),
            timeout_ms=get_timeout_ms(agent_type),
            token_budget=get_token_budget(state.query_type, agent_type),
            feature_flags=state.feature_flags,
        )
        
        sends.append(
            Send(
                node=agent_type,               # 目标子图节点名
                arg={
                    "dispatch": dispatch,       # 注入子图状态
                    "config": {
                        "configurable": {
                            "namespace": f"{state.query_id}:{agent_type}"
                        }
                    }
                }
            )
        )
    
    return sends
```

### 3.3 Worker 接收派发（子图入口）

```python
# doc_agent_graph.py

class DocAgentInputState(TypedDict):
    """Doc Agent 子图的输入状态——接收 Supervisor 的 WorkerDispatch"""
    dispatch: WorkerDispatch

def doc_agent_entry(state: DocAgentInputState) -> DocAgentState:
    """子图入口：将 WorkerDispatch 转换为 Doc Agent 内部状态"""
    d = state["dispatch"]
    return DocAgentState(
        round=1,
        query=d.rewritten_query,
        original_query=d.original_query,
        entities=d.entities,
        max_rounds=d.max_rounds,
        timeout_ms=d.timeout_ms,
        token_budget=d.token_budget,
        results=[],
        assessment="",
        confidence=0.0,
        has_exact_match=False,
        llm_calls=0,
        latency_ms=0,
    )
```

---

## 四、Worker → Supervisor：WorkerOutput 协议

### 4.1 标准输出结构

所有 Worker Agent 返回统一格式的 `WorkerOutput`：

```python
from pydantic import BaseModel, Field
from typing import Literal, Optional
from uuid import UUID

class Citation(BaseModel):
    """引用元数据"""
    source_type: Literal["prd", "code", "sql"] = Field(...)
    source_id: str = Field(..., description="doc_id, file_path:line, 或 table.column")
    snippet: str = Field(..., max_length=200, description="引用原文片段")
    relevance_score: float = Field(default=0.0, ge=0, le=1)
    metadata: dict = Field(default_factory=dict, description="额外元数据（版本、时间等）")

class WorkerOutput(BaseModel):
    """Worker Agent → Supervisor 的标准输出契约"""
    
    # ── 标识 ──
    $schema: str = "spma/worker-output/1.0"
    task_id: str = Field(..., description="派发任务 ID")
    query_id: UUID = Field(...)
    worker_type: Literal["doc", "code", "sql"] = Field(...)
    
    # ── 结果 ──
    result_count: int = Field(..., ge=0)
    results: list[dict] = Field(default_factory=list, description="检索/SQL 结果列表")
    citations: list[Citation] = Field(default_factory=list)
    
    # ── 质量信号 ──
    confidence: float = Field(..., ge=0, le=1, description="Worker 自评信心")
    has_exact_match: bool = Field(False, description="是否命中精确匹配实体")
    
    # ── 执行元数据 ──
    rounds_used: int = Field(..., description="内部消耗的检索轮数")
    convergence_reason: str = Field(..., description="收敛原因")
    total_llm_calls: int = Field(0)
    total_tokens: int = Field(0)
    latency_ms: int = Field(0)
    
    # ── 原始输入留存 ──
    original_query: str = Field(..., description="原始检索 query")
    
    # ── 降级信息 ──
    degradation: Optional[DegradationInfo] = None
    
    # ── 跨源桥接实体 ──
    discovered_entities: DiscoveredEntities = Field(
        default_factory=DiscoveredEntities,
        description="Worker 在检索过程中发现的新实体（可桥接到其他源）"
    )

class DegradationInfo(BaseModel):
    """降级信息"""
    level: Literal["L0", "L1", "L2", "L3"] = "L0"
    reason: str = ""
    fallback_strategy: str = ""
    impact_description: str = ""

class DiscoveredEntities(BaseModel):
    """Worker 检索过程中发现的新实体——用于跨源桥接"""
    req_ids: list[str] = Field(default_factory=list)
    table_names: list[str] = Field(default_factory=list)
    code_refs: list[str] = Field(default_factory=list)
    module: Optional[str] = None
    person: Optional[str] = None
```

### 4.2 WorkerOutput JSON 示例

```json
{
  "$schema": "spma/worker-output/1.0",
  "task_id": "uuid-xxxx:doc",
  "query_id": "550e8400-e29b-41d4-a716-446655440000",
  "worker_type": "doc",
  "result_count": 5,
  "results": [
    {
      "id": "doc_001:chunk_3",
      "source": "用户登录模块 PRD v2.3",
      "snippet": "## 3.2 OAuth2.0 登录流程\n用户点击登录按钮后跳转至授权页面…",
      "score": 0.92,
      "metadata": {
        "req_id": "REQ-2024-0187",
        "version": "v2.3",
        "updated_at": "2026-05-15"
      }
    }
  ],
  "citations": [
    {
      "source_type": "prd",
      "source_id": "doc_001:chunk_3",
      "snippet": "## 3.2 OAuth2.0 登录流程\n用户点击登录按钮后…",
      "relevance_score": 0.92,
      "metadata": {"req_id": "REQ-2024-0187", "version": "v2.3"}
    }
  ],
  "confidence": 0.85,
  "has_exact_match": false,
  "rounds_used": 2,
  "convergence_reason": "llm_judged_sufficient",
  "total_llm_calls": 2,
  "total_tokens": 1200,
  "latency_ms": 1200,
  "original_query": "用户登录 PRD需求 功能规格 登录流程",
  "degradation": null,
  "discovered_entities": {
    "req_ids": ["REQ-2024-0187"],
    "table_names": [],
    "code_refs": [],
    "module": "用户登录"
  }
}
```

---

## 五、WorkerOutput 的 Reducer 合并

Supervisor 使用 LangGraph 的 reducer 机制合并并行 Worker 的输出：

```python
import operator
from typing import Annotated

class SupervisorCollectState(TypedDict):
    """Supervisor 收集 Worker 结果的状态——使用 reducer 合并"""
    worker_outputs: Annotated[list[WorkerOutput], operator.add]  # reducer: 追加
    # 每个 Worker 返回的 WorkerOutput 自动追加到此列表
```

LangGraph 自动将多个 Worker 返回的 `WorkerOutput` 合并为 `worker_outputs` 列表，Supervisor 的收集节点从该列表读取。

---

## 六、Supervisor → Synthesis Agent：合成派发协议

```python
class SynthesisDispatch(BaseModel):
    """Supervisor → Synthesis Agent 的任务派发"""
    
    # ── 任务标识 ──
    task_id: str = Field(...)
    query_id: UUID = Field(...)
    
    # ── 输入 ──
    original_query: str = Field(..., description="用户原始问题")
    worker_outputs: list[WorkerOutput] = Field(..., description="所有 Worker 的结果")
    classification: ClassificationResult = Field(..., description="意图分类结果")
    
    # ── 收敛约束 ──
    max_rounds: int = Field(default=2)
    timeout_ms: int = Field(default=2000)
    token_budget: int = Field(default=4)
    
    # ── 质量要求 ──
    min_citation_coverage: float = Field(default=0.8, ge=0, le=1)
    require_cross_source_check: bool = Field(default=True)
```

---

## 七、Checkpointer 隔离契约

每个 Agent 子图使用独立的 checkpointer namespace：

```python
# Supervisor 在 Send API 中注入 namespace
config = {
    "configurable": {
        "namespace": f"{query_id}:{agent_type}"  # 如 "uuid-xxx:doc"
    }
}

# LangGraph 自动将不同 namespace 的状态写入独立存储 key
# PostgreSQL 中:
# checkpoint:spma:{query_id}:supervisor
# checkpoint:spma:{query_id}:doc
# checkpoint:spma:{query_id}:code
# checkpoint:spma:{query_id}:sql
# checkpoint:spma:{query_id}:synthesis
```

**Namespace 格式：** `{query_id}:{agent_type}`

同一 query 下不同 Agent 的子图状态互不干扰。查询结束后，namespace 下的所有 checkpoint 通过 TTL 自动清理（Redis TTL=5min, Postgres TTL=30d）。

---

## 八、错误与异常在 Agent 间的传播

### 8.1 Worker 内部异常 → WorkerOutput

Worker 发生异常时，不抛出给 Supervisor，而是包装为降级信息：

```python
def handle_worker_exception(e: Exception, task_id: str) -> WorkerOutput:
    """将 Worker 内部异常转化为带降级标注的 WorkerOutput"""
    return WorkerOutput(
        $schema="spma/worker-output/1.0",
        task_id=task_id,
        worker_type=extract_worker_type(task_id),
        result_count=0,
        results=[],
        citations=[],
        confidence=0.0,
        has_exact_match=False,
        rounds_used=0,
        convergence_reason="error",
        degradation=DegradationInfo(
            level="L2",
            reason=str(e),
            fallback_strategy="empty_result",
            impact_description="该数据源暂时不可用"
        )
    )
```

### 8.2 Agent 间错误传播表

| 场景 | 传播方式 | Supervisor 行为 |
|------|---------|----------------|
| Worker 超时 | `WorkerOutput(degradation.level="L2")` | 使用部分结果 + 标注 |
| Worker 全部失败 | 所有 WorkerOutput 的 `result_count=0` | 返回友好错误信息 |
| Supervisor 重调度超限 | 强制收敛 | 取最佳历史结果 |
| Checkpointer 写入失败 | 降级到进程内存 | 继续执行，本次无持久化 |
| Token 预算耗尽 | `TokenBudgetExhausted` | 强制收敛，标注"为控制成本…" |
