# SPMA 模块间参数契约

> 配套文件：[api-contract.yaml](api-contract.yaml) — 外部 API | [api-design-decisions.md](api-design-decisions.md) — API 设计决策
>
> 本文档定义系统**内部**所有模块之间的接口契约。每个接口包含：协议、输入/输出 Schema、错误处理、超时、依赖和副作用。

---

## 模块拓扑

```
                          ┌─────────────────────────────────┐
                          │        Degradation Manager      │
                          │  (cross-cutting, §8)            │
                          └──────────┬──────────────────────┘
                                     │ health signals ↑↓ degradation state
                                     │
  ┌──────────┐   IF1    ┌───────────▼───────────┐
  │   API    │─────────▶│   Supervisor Agent     │
  │ Gateway  │          │                        │
  │          │◀─────────│ ┌────────────────────┐ │
  │  §1      │   IF10   │ │Query Understanding │ │
  └──────────┘          │ │  · classify        │ │
                         │ │  · extract entities│ │
                         │ │  · assess complete │ │
                         │ │  · rewrite query   │ │
                         │ └────────┬───────────┘ │
                         │          │ IF2         │
                         │ ┌────────▼───────────┐ │
                         │ │Task Decomposition  │ │
                         │ │  · plan workers    │ │
                         │ │  · resolve deps    │ │
                         │ │  · set timeouts    │ │
                         │ └────────┬───────────┘ │
                         └──────────┼─────────────┘
                                    │
                    ┌───────────────┼───────────────┐
                    │ IF3           │ IF5           │ IF7
                    ▼               ▼               ▼
              ┌──────────┐  ┌──────────┐  ┌──────────┐
              │   Doc    │  │   Code   │  │   SQL    │
              │  Worker  │  │  Worker  │  │  Worker  │
              │   §3     │  │   §4     │  │   §5     │
              └────┬─────┘  └────┬─────┘  └────┬─────┘
                   │ IF4         │ IF6         │ IF8
                   └──────────────┼──────────────┘
                                  │
                        ┌─────────▼─────────┐
                        │  Synthesis Layer  │
                        │  · RRF fusion     │
                        │  · dedup          │
                        │  · LLM generate   │
                        │  §6               │
                        └────────┬──────────┘
                                 │ IF9
                                 ▼
                          ┌──────────┐
                          │   API    │
                          │ Gateway  │
                          └──────────┘

  ┌──────────────────┐                   ┌──────────────────┐
  │ Ingestion Engine │──IF11/12/13──────▶│    PGVector      │
  │     §7           │                   │    + Redis       │
  └──────────────────┘                   └──────────────────┘
           ▲
           │ IF14 (manual trigger from Admin API)

  ┌──────────────────┐
  │ Feedback Pipeline│──IF15──▶ Quality Metrics DB
  │     §9           │
  └──────────────────┘
```

---

## §0 通用约定

### 0.1 协议选择

| 调用场景 | 协议 | 理由 |
|---------|------|------|
| Gateway → Supervisor | Python 函数调用 | 同进程（LangGraph 编排），无序列化开销 |
| Supervisor → Workers | Python async 函数调用 | 同进程，但并行执行（asyncio.gather） |
| Workers → PGVector/Redis | PostgreSQL wire / Redis wire | 标准数据库协议 |
| Workers → LLM API | HTTPS + Anthropic SDK | 外部 API |
| Degradation Manager ↔ 各组件 | 进程内 event bus | 发布/订阅，零延迟 |
| Ingestion Engine → PGVector | PostgreSQL wire | 批量写入 |
| 跨服务拆分后 | gRPC + Protobuf | 当前 v1 不需要，契约设计预留 |

### 0.2 通用基类

```python
from pydantic import BaseModel, Field
from typing import Generic, TypeVar, Optional
from datetime import datetime
import uuid

T = TypeVar("T")

class SPMAContext(BaseModel):
    """每次查询携带的全局上下文，贯穿整个流水线"""
    query_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: Optional[str] = None
    user_id: str
    user_name: str
    auth_method: str  # "sso" | "api_key"
    started_at: datetime = Field(default_factory=datetime.utcnow)
    trace_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    degradation_level: int = 0  # 初始为 L0，DegradationManager 可修改

class SPMAError(BaseModel):
    """内部错误传播结构"""
    code: str                    # E0001-E5999
    component: str               # 哪个组件产生的错误
    message: str                 # 日志消息（英文结构化）
    user_message: str            # 用户消息（中文）
    retryable: bool
    cause: Optional[str] = None  # 上游异常信息

class SPMAComponent(BaseModel):
    """所有模块的基类"""
    component_name: str
    health_status: str = "up"   # "up" | "degraded" | "down"
```

### 0.3 追踪约定

每个接口调用必须：
1. 携带 `trace_id`（OpenTelemetry span context）
2. 记录调用延迟（毫秒）
3. 在降级状态下标注 `degradation_level`

---

## §1 API Gateway（外部→内部适配）

### IF1: API Gateway → Supervisor Agent

**方向：** Gateway → Supervisor  
**协议：** Python 函数调用（同进程）  
**触发：** 用户 HTTP 请求到达 `POST /api/v1/query` 或 `/query/stream`

#### 输入 Schema

```python
class GatewayQueryRequest(BaseModel):
    """Gateway 将 HTTP 请求转为内部调用格式"""
    # 来自 HTTP body
    query: str = Field(..., min_length=1, max_length=2000)
    session_id: Optional[str] = None
    max_sources: Optional[list[str]] = None  # ["doc","code","sql"]，不传则自动分类
    timeout_ms: int = Field(default=30000, ge=5000, le=60000)
    stream: bool = False

    # 来自认证层（Gateway 注入）
    context: SPMAContext  # 认证信息 + trace_id + query_id

    # 来自会话管理（Gateway 注入）
    conversation_history: list["ConversationTurn"] = []

class ConversationTurn(BaseModel):
    """多轮对话中的一轮"""
    turn_index: int
    user_query: str
    system_answer: str
    classification: Optional["ClassificationResult"] = None  # 上一轮的分类结果
    entities: Optional["ExtractedEntities"] = None            # 上一轮的实体（用于上下文继承）
    timestamp: datetime
```

#### 输出 Schema

```python
class GatewayQueryResponse(BaseModel):
    """Supervisor 完成后 Gateway 组装 HTTP 响应"""
    query_id: str
    answer: str                                    # Markdown
    sources: list["Source"]
    suggested_followups: list[str] = []
    degradation: "DegradationInfo"
    latency_ms: int
    data_freshness: Optional["DataFreshness"] = None
    sql_executed: Optional[str] = None
    needs_confirmation: bool = False
    confirmation_prompt: Optional["SQLConfirmationPrompt"] = None
```

#### 错误处理

| Gateway 错误 | 内部错误码 | HTTP Status | 动作 |
|-------------|-----------|-------------|------|
| query 为空 | E1011 | 400 | 不路由到 Supervisor，直接返回 |
| 认证失败 | E5001 | 401 | Gateway 层拦截，不进入流水线 |
| 限流触发 | E0030 | 429 | Gateway 层拦截 |
| 全链路超时 | E0010 | 503 | 返回 L4 静态 FAQ |

#### 超时契约
- Gateway 到 Supervisor 的调用超时 = `timeout_ms` + 2s（序列化余量）
- 若 Supervisor 在 `timeout_ms - 2s` 时仍未返回 → Gateway 主动断开，返回 202（异步模式）或 503

#### 副作用
- 无论成功失败，写入审计日志（`event_type: query.submitted | query.completed | query.failed`）
- 有 session_id 时更新会话的 `updated_at`

---

## §2 Supervisor Agent

### IF2: Query Understanding → Task Decomposition（内部）

**方向：** Supervisor 内部两个子模块之间  
**协议：** Python 函数调用（同进程，无序列化）

#### 输入 Schema（Query Understanding 输出）

```python
class ClassificationResult(BaseModel):
    """意图分类 + 实体抽取 + 完备度评估的联合输出"""
    # ── 分类结果 ──
    sources: list[str]          # ["doc","code","sql"]
    is_cross_source: bool
    query_type: str             # "trace" | "search" | "data_query" | "explain"

    # ── 实体抽取 ──
    entities: "ExtractedEntities"

    # ── 完备度评估（确定性代码，非LLM） ──
    completeness: str           # "rich" | "partial" | "bare"
    completeness_score: int     # 0-40+，各实体权重之和

    # ── 分类元数据 ──
    classification_method: str  # "llm_structured" | "llm_fallback" | "rule_only"
    classifier_model: str       # "claude-haiku-4.5" | "qwen3-8b" | "regex"
    classifier_latency_ms: int

    # ── 查询改写（可选，取决于触发条件） ──
    rewritten_queries: dict[str, str] = {}
    # key: worker_name ("doc"/"code"/"sql"), value: 改写后的检索 query
    # 包含标准化 → 扩展 → 分解 → HyDE 流水线的结果
    rewrite_pipeline_applied: list[str] = []
    # ["normalization", "expansion", "decomposition", "hyde"] 中实际触发了哪些
```

#### ExtractedEntities（完整版）

```python
class ExtractedEntities(BaseModel):
    """从用户查询中抽取的结构化实体（12 种类型）"""
    # 跨源通用
    module: Optional[str] = None          # "用户登录"
    req_ids: list[str] = []               # ["REQ-2024-0187"]
    time_range: Optional[str] = None      # "上周"
    version: Optional[str] = None         # "v2.3"

    # SQL 相关
    table_names: list[str] = []           # ["users","orders"]
    column_names: list[str] = []          # ["status","amount"]
    metrics: list[str] = []               # ["新增用户数","订单总额"]
    group_by: Optional[str] = None        # "按状态"

    # 代码相关
    code_refs: list[str] = []             # ["oauth.py","TokenService"]
    person: Optional[str] = None          # "张三"

    # 文档相关
    doc_types: list[str] = []             # ["PRD","技术方案"]
```

#### 输出 Schema（Task Decomposition 输出）

```python
class TaskPlan(BaseModel):
    """决定哪些 Worker 执行、各自什么参数、有无依赖"""
    query_id: str
    tasks: list["WorkerTask"]       # 要执行的 Worker 任务列表
    dependency_graph: dict[str, list[str]] = {}
    # {"code": ["doc"], "sql": []}
    # code 依赖 doc 先完成（Code Worker 用 Doc 输出的 req_ids 搜索）
    # sql 无依赖，和 doc 并行

    overall_timeout_ms: int         # 总预算
    per_worker_timeout_ms: dict[str, int]  # 每个 Worker 的独立预算

class WorkerTask(BaseModel):
    """分发给单个 Worker 的任务"""
    worker_name: str                # "doc" | "code" | "sql"
    query_id: str
    trace_id: str

    # 检索参数（来自 ClassificationResult）
    entities: "ExtractedEntities"
    rewritten_query: str            # 针对该 Worker 改写后的检索 query
    sources_to_search: list[str]    # 冗余但方便 Worker 只读自己的配置

    # 检索策略提示（来自完备度评估）
    retrieval_strategy: str         # "exact" | "hybrid" | "semantic_only"
    # rich   → "exact": 跳过语义搜索，直接元数据/grep 精确检索
    # partial → "hybrid": 元数据过滤 + 语义搜索
    # bare   → "semantic_only": 纯语义搜索兜底

    # 来自上游 Worker 的输出（依赖场景）
    upstream_results: dict[str, "WorkerResult"] = {}
    # 例如 Code Worker.worker_deps=["doc"] → upstream_results={"doc": DocWorkerResult}

    # 降级上下文
    degradation_level: int = 0
```

#### 错误处理

| 失败场景 | 分类错误码 | 处理 |
|---------|-----------|------|
| LLM 分类超时（>500ms） | — | 降级到 rule_only 分类，不报错 |
| LLM 分类 + 规则均失败 | E1001 | 返回错误，不创建 TaskPlan |
| 抽取的实体全为空（bare + 短查询 + 无历史） | E1010/E1011 | 返回反问提示，不创建 TaskPlan |
| 改写流水线全部失败 | — | 使用原始 query，逐个 Worker 单独报 E1002 |

#### 超时契约
- Query Understanding 总预算：500ms（含 LLM 分类 500ms + 规则兜底 5ms）
- 改写流水线：取决于触发方案
  - 标准化：~1ms
  - 扩展：~300ms（Haiku LLM 调用）
  - 分解：~500ms（仅跨源时触发）
  - HyDE：~1500ms（并行执行，不阻塞主检索）
- Task Decomposition：< 5ms（纯规则代码，无 LLM）

---

## §3 Doc Worker

### IF3: Supervisor → Doc Worker

**方向：** Supervisor → Doc Worker  
**协议：** Python async 函数调用  
**并行语义：** 与 Code Worker、SQL Worker 并行（除非 TaskPlan 指定依赖）

#### 输入 Schema

```python
class DocWorkerTask(WorkerTask):
    worker_name: str = "doc"

    # ── 检索参数（从 entities 中提取 Doc Worker 关心的字段）──
    req_ids: list[str]              # 最高优先级——精确匹配
    module_query: str               # entities.module + 原始 query
    time_range: Optional[str]       # 用于元数据过滤
    version: Optional[str]
    doc_types: list[str]
    expanded_query: str             # 改写流水线后的最终 query（含扩展词）

    # ── 检索配置 ──
    top_k: int = 20                 # 初始召回量
    fusion_method: str = "rrf"      # "rrf" | "linear_combination"
    bm25_weight: float = 0.3        # BM25 和向量检索的融合权重
    vector_weight: float = 0.7

    # ── 超时预算 ──
    timeout_ms: int = 1500
```

#### 输出 Schema

```python
class DocWorkerResult(BaseModel):
    worker_name: str = "doc"
    query_id: str
    trace_id: str

    # ── 检索结果 ──
    chunks: list["DocChunk"]
    total_candidates: int               # 融合前候选数
    total_retrieved: int                # 最终返回数

    # ── 提取的结构化信息 ──
    extracted_req_ids: list[str] = []      # 从 chunks 中提取的所有需求 ID
    extracted_changes: list["DocChange"] = []  # 从 chunks 中 LLM 提取的改动列表

    # ── 检索元数据 ──
    retrieval_method: str              # "exact" | "hybrid" | "semantic_only"
    bm25_latency_ms: int
    vector_latency_ms: int
    llm_extraction_latency_ms: int = 0 # LLM 提取 req_ids 和 changes 的耗时
    total_latency_ms: int

    # ── 降级状态 ──
    degradation_triggered: bool = False
    degradation_reason: Optional[str] = None

class DocChunk(BaseModel):
    chunk_id: str
    content: str                        # 原始文本片段
    embedding: Optional[list[float]]     # 1024维 BGE-M3（可选，仅调试用）
    metadata: "DocChunkMetadata"
    retrieval_score: float              # RRF 融合后的分数
    retrieval_source: str               # "bm25" | "vector" | "exact"

class DocChunkMetadata(BaseModel):
    title: str
    source_url: str
    doc_type: str           # "PRD" | "技术方案" | "接口文档" | "会议纪要"
    req_id: Optional[str]
    version: Optional[str]
    section: Optional[str]  # 文档中的章节
    updated_at: datetime
    indexed_at: datetime

class DocChange(BaseModel):
    """LLM 从文档 chunks 中提取的改动点"""
    description: str            # "增加 OAuth2.0 登录"
    req_id: Optional[str]       # "REQ-2024-0187"
    affected_modules: list[str] = []  # ["用户登录", "认证"]
    confidence: float           # LLM 提取置信度
```

#### 错误处理

| 失败场景 | 错误码 | 返回值 | 对下游影响 |
|---------|--------|--------|-----------|
| PGVector 不可达（L2 降级） | E4001 | 返回空 chunks + degradation_triggered=true | Synthesis 标注"缺少文档维度" |
| BM25+向量均无结果 | — | 返回 total_retrieved=0，非错误 | 正常情况 |
| LLM 提取超时 | E2001 | 返回 chunks 但不返回 extracted_changes | 丢失 req_ids 桥梁信息 |
| Confluence API 不可达（摄入用，检索不直接调） | — | 不影响检索（走已索引数据） | 数据新鲜度下降 |

#### 超时契约
- 总预算：1500ms（与设计文档 Trace 一致）
- BM25 检索：< 50ms
- 向量检索：< 200ms
- RRF 融合：< 5ms
- LLM 提取 req_ids/changes：< 1200ms（最耗时部分）
- 若 LLM 提取超时 → 返回 chunks 不含 extracted_changes（部分成功）

#### 依赖
- **PGVector** — 必须（存储 doc_chunks + embedding 索引）
- **BGE-M3 嵌入服务** — 必须（query embedding）
- **LLM（Haiku）** — 可选（req_ids/changes 提取，失败可降级）
- **Redis** — 可选（缓存近期相同 query 的检索结果，TTL=5min）

---

## §4 Code Worker

### IF5: Supervisor → Code Worker

**方向：** Supervisor → Code Worker  
**协议：** Python async 函数调用  
**并行语义：** 与 Doc Worker、SQL Worker 并行（可能依赖 Doc Worker 的 req_ids 输出）

#### 输入 Schema

```python
class CodeWorkerTask(WorkerTask):
    worker_name: str = "code"

    # ── 检索参数 ──
    code_refs: list[str]            # ["oauth.py","TokenService"] — 精确路径
    req_ids: list[str] = []         # 来自用户输入或 Doc Worker 输出
    module_query: str               # 用于语义搜索
    person: Optional[str]           # git log --author
    time_range: Optional[str]       # git log --since/--until
    version: Optional[str]          # 分支/tag
    rewritten_query: str            # 改写流水线后的最终 query

    # ── 检索配置 ──
    top_k: int = 20
    search_repos: list[str] = []    # 限定仓库列表（来自 Repo Router）
    search_file_patterns: list[str] = []  # ["auth/", "login/"]（来自 module→路径映射）

    # ── 超时预算 ──
    timeout_ms: int = 1200          # 设计文档修正后的值（原 200ms 不现实）
    # 分解: grep ~10ms + embedding ~50ms + git log ~500ms + LLM ~600ms
```

#### 输出 Schema

```python
class CodeWorkerResult(BaseModel):
    worker_name: str = "code"
    query_id: str
    trace_id: str

    # ── 检索结果 ──
    chunks: list["CodeChunk"]           # 检索到的代码片段
    grep_results: list["GrepHit"]       # grep 精确匹配结果
    total_grep_hits: int
    total_embedding_hits: int

    # ── 结构化提取 ──
    discovered_tables: list[str] = []   # 在代码中引用的数据库表名
    discovered_req_ids: list[str] = []  # 在代码注释/commit 中发现的额外需求 ID

    # ── 检索元数据 ──
    primary_method: str             # "grep" | "semantic"
    supplementary_method: Optional[str]  # "semantic" | None
    grep_latency_ms: int
    git_log_latency_ms: int
    embedding_latency_ms: int = 0
    total_latency_ms: int

    # ── 降级状态 ──
    degradation_triggered: bool = False
    degradation_reason: Optional[str] = None
    index_staleness_warning: bool = False  # 元数据索引落后于 HEAD

class CodeChunk(BaseModel):
    chunk_id: str
    content: str                        # 完整源代码（函数/类级别）
    language: str
    embedding: Optional[list[float]]    # 仅 semantic 路径时有值
    metadata: "CodeChunkMetadata"
    retrieval_score: float
    retrieval_method: str               # "grep" | "semantic" | "git_log"

class CodeChunkMetadata(BaseModel):
    file_path: str                      # "src/auth/oauth.py"
    line_start: int
    line_end: int
    function_name: Optional[str]
    class_name: Optional[str]
    repo: str
    branch: str
    commit_hash: str
    author: str
    updated_at: datetime
    imports: list[str] = []
    calls: list[str] = []               # 被调函数
    called_by: list[str] = []           # 调用者
    req_ids: list[str] = []             # 关联需求 ID

class GrepHit(BaseModel):
    """grep/AST 精确匹配结果"""
    file_path: str
    line_number: int
    line_content: str
    match_type: str                     # "function_def" | "class_def" | "import" | "comment" | "string_literal"
    surrounding_context: str            # 匹配行前后各 3 行
```

#### 错误处理

| 失败场景 | 错误码 | 返回值 | 对下游影响 |
|---------|--------|--------|-----------|
| Git 仓库不可达 | E4002 | 返回空 + degradation_triggered | Synthesis 标注"缺少代码维度" |
| grep 零结果（兜底到 semantic） | — | 正常路径，primary_method="semantic" | 标注检索方式 |
| grep + semantic 均零结果 | — | 返回 total=0 | 正常情况 |
| AST 解析失败（语法错误代码） | — | 跳过该文件，记录日志，不影响其他文件 | 单文件丢失 |
| 索引过期检测 | E4010 | index_staleness_warning=true，触发实时 grep 兜底 | 透明标注 |

#### 超时契约
- 总预算：1200ms
- grep 检索：< 10ms（ripgrep 原生速度，设计文档验证）
- git log：< 500ms（--grep + --since 组合查询）
- semantic 检索：< 50ms（仅在 grep 不足时触发）
- LLM 提取 tables/req_ids：< 600ms

#### 依赖
- **PGVector** — 必须（code_chunks 元数据表 + B-tree 索引）
- **Git 仓库文件系统** — 必须（实时 grep 和 git log）
- **LLM（Haiku）** — 可选（提取 discovered_tables 和 discovered_req_ids）
- **Redis** — 可选（缓存 git log 结果）

---

## §5 SQL Worker

### IF7: Supervisor → SQL Worker

**方向：** Supervisor → SQL Worker  
**协议：** Python async 函数调用  
**并行语义：** 与 Doc Worker、Code Worker 并行

#### 输入 Schema

```python
class SQLWorkerTask(WorkerTask):
    worker_name: str = "sql"

    # ── 检索参数 ──
    table_names: list[str]          # entities.table_names — 精确表名
    column_names: list[str]         # entities.column_names
    metrics: list[str]              # entities.metrics
    group_by: Optional[str]         # entities.group_by
    time_range: Optional[str]       # entities.time_range
    module_query: str               # 用于 Schema RAG 语义搜索
    rewritten_query: str            # 改写流水线后的最终 natural language query

    # ── 跨源上下文 ──
    upstream_req_ids: list[str] = []  # 来自 Doc Worker 的 req_ids
    upstream_tables: list[str] = []   # 来自 Code Worker 的 discovered_tables

    # ── 检索配置 ──
    top_k_schema: int = 10          # Schema RAG 召回表数量
    sql_generation_model: str = "claude-sonnet-4"
    max_self_healing_rounds: int = 3

    # ── 超时预算 ──
    timeout_ms: int = 1500
```

#### 输出 Schema

```python
class SQLWorkerResult(BaseModel):
    worker_name: str = "sql"
    query_id: str
    trace_id: str

    # ── Schema 检索结果 ──
    schema_results: list["SchemaChunk"]
    relevant_tables: list[str]          # RAG 或精确方式找到的相关表名

    # ── SQL 生成与执行 ──
    generated_sql: Optional[str] = None
    sql_generation_rounds: int = 0      # 自修复循环实际轮数
    sql_execution_result: Optional["SQLExecutionResult"] = None

    # ── 安全与确认 ──
    needs_confirmation: bool = False    # 触发用户确认闸门
    confirmation_prompt: Optional["SQLConfirmationPrompt"] = None
    sql_guard_passed: bool = False
    sql_guard_errors: list[str] = []

    # ── 检索元数据 ──
    schema_retrieval_method: str    # "exact" | "hybrid" | "semantic_only"
    schema_retrieval_latency_ms: int
    sql_generation_latency_ms: int
    sql_execution_latency_ms: int
    total_latency_ms: int

    # ── 数据质量 ──
    data_freshness: Optional["SQLDataFreshness"] = None
    quality_warnings: list[str] = []    # NULL比例、异常值等

    # ── 降级状态 ──
    degradation_triggered: bool = False
    degradation_reason: Optional[str] = None

class SchemaChunk(BaseModel):
    """单个表的 Schema 信息"""
    table_name: str
    ddl: str                            # 完整的 CREATE TABLE 语句
    columns: list["ColumnSchema"]
    business_metadata: Optional["BusinessMetadata"]  # 业务元数据增强
    few_shot_examples: list["SQLExample"] = []
    embedding: Optional[list[float]]     # DDL+comment 的 embedding

class ColumnSchema(BaseModel):
    column_name: str
    data_type: str
    is_nullable: bool
    column_default: Optional[str]
    column_comment: Optional[str]       # 数据库原生注释

class BusinessMetadata(BaseModel):
    """从列注释、代码enum、PRD文档中提取的业务语义"""
    column_name: str
    business_meaning: str               # "订单状态"
    enum_values: dict[str, str] = {}    # {"pending":"待支付","paid":"已支付"}
    business_rules: list[str] = []      # ["只有paid状态才计入营收"]
    related_tables: list[str] = []      # 外键关联的表
    source: str                         # "column_comment" | "code_enum" | "doc_extraction"

class SQLExample(BaseModel):
    """Few-shot 示例（经过人工审核）"""
    natural_language: str
    sql: str
    business_rule: str                  # 这个查询隐含的业务规则
    curated_by: Optional[str]           # 审核人
    usage_count: int                    # 被引用次数

class SQLExecutionResult(BaseModel):
    """SQL 在只读副本上的执行结果"""
    sql: str                            # 实际执行的 SQL
    rows: list[dict]                    # 结果行（前 100 行）
    total_rows: int
    columns: list[str]
    column_types: dict[str, str]
    execution_time_ms: int
    replica_lag_seconds: float          # 只读副本延迟
    data_snapshot_at: datetime          # 数据快照时间
    null_column_stats: dict[str, float] # 每列 NULL 比例
    numeric_outliers: dict[str, dict]   # 数值列异常值检测

class SQLConfirmationPrompt(BaseModel):
    """展示给用户的 SQL 确认信息"""
    sql: str
    tables_affected: list[str]
    risk_level: str                     # "low" | "medium" | "high"
    risk_reasons: list[str]             # ["涉及财务指标聚合", "全表扫描"]
    estimated_rows: Optional[int]       # EXPLAIN 估算
    estimated_cost: Optional[str]       # EXPLAIN 估算代价

class SQLDataFreshness(BaseModel):
    """SQL 数据源新鲜度"""
    last_schema_sync: datetime
    schema_lag_seconds: int             # Schema 索引延迟
    replica_lag_seconds: float          # 只读副本复制延迟
    last_vacuum: Optional[datetime]     # 上次 VACUUM（PG）
    last_analyze: Optional[datetime]    # 上次 ANALYZE（PG，统计信息新鲜度）
```

#### 错误处理

| 失败场景 | 错误码 | 处理 |
|---------|--------|------|
| Schema RAG 无结果 | — | 返回空 schema_results，无法生成 SQL |
| LLM SQL 生成失败 | E3001 | 自修复循环（最多 3 次） |
| SQL Guard 拦截 | E3002 | 返回 sql_guard_errors + 自修复循环 |
| 只读副本连接失败 | E4003 | 返回 schema 但不执行 SQL |
| SQL 执行超时（>10s） | E3003 | 终止查询，返回部分结果 |
| 自修复 3 次后仍失败 | E3004 | 返回最后一次 SQL + 错误说明 |
| 触发用户确认闸门 | — | needs_confirmation=true，暂停等待 |
| DDL/DML 操作拦截 | E3002 | 永久拦截，不进入自修复 |

#### 超时契约
- 总预算：1500ms（不含用户确认等待时间）
- Schema RAG：< 100ms
- LLM SQL 生成：< 800ms
- SQL Guard 校验：< 5ms（SQLGlot 本地解析）
- 只读副本执行：< 500ms（含连接池获取）
- 自修复循环：每轮额外 < 800ms（最多 3 轮）
- 若 3 轮自修复后仍未通过 → 返回 E3004，总延迟约 3900ms（超出预算但可接受）

#### 依赖
- **PGVector** — 必须（schema_chunks + 业务元数据）
- **数据库只读副本** — 必须（SQL 执行 + Schema 验证）
- **LLM（Sonnet）** — 必须（SQL 生成 + 自修复）
- **Redis** — 可选（缓存热点 Schema 检索结果）

---

## §6 Synthesis Layer

### IF9/IF10: Workers → Synthesis → Gateway

**方向：** Synthesis 聚合所有 Worker 结果，生成最终回答  
**协议：** Python 函数调用

#### 输入 Schema

```python
class SynthesisInput(BaseModel):
    query_id: str
    trace_id: str
    original_query: str                     # 用户原始问题

    # ── 上游输出 ──
    classification: ClassificationResult    # Supervisor 的分类/实体/完备度
    doc_result: Optional[DocWorkerResult]   # None = Doc Worker 未执行或完全失败
    code_result: Optional[CodeWorkerResult]
    sql_result: Optional[SQLWorkerResult]

    # ── 降级上下文 ──
    degradation_level: int
    degradation_events: list["DegradationEvent"] = []  # 本次查询中触发的降级事件

    # ── 生成配置 ──
    synthesis_model: str = "claude-sonnet-4"
    max_answer_tokens: int = 2000
    include_citations: bool = True
```

#### 输出 Schema

```python
class SynthesisOutput(BaseModel):
    query_id: str
    trace_id: str

    # ── 最终回答 ──
    answer: str                             # Markdown 格式，含引用标注
    citations: list["Citation"]             # 可点击的引用列表

    # ── 融合元数据 ──
    sources: list["Source"]                 # RRF 融合去重后的排序结果
    total_raw_sources: int                  # 融合前各 Worker 返回的总数
    total_fused_sources: int                # 去重融合后
    fusion_method: str = "rrf"

    # ── 降级信息 ──
    degradation: "DegradationInfo"          # 含 user_notice（透明标注）
    missing_dimensions: list[str] = []      # ["doc"] 表示缺少文档维度的结果

    # ── 建议追问 ──
    suggested_followups: list[str] = []

    # ── 性能 ──
    worker_latencies: dict[str, int]        # 各 Worker 实际延迟
    synthesis_latency_ms: int               # 融合+LLM生成耗时
    total_latency_ms: int

    # ── LLM 元数据 ──
    llm_model: str
    llm_prompt_tokens: int
    llm_completion_tokens: int

class Citation(BaseModel):
    """可点击的引用标注"""
    text: str                       # 引用显示文本 "[PRD §3.2]"
    source_type: str                # "doc" | "code" | "sql"
    url: Optional[str]              # 可跳转链接（Confluence URL / Git file URL）
    chunk_id: str                   # 对应的 chunk ID
```

#### 融合处理流程

```python
def synthesize(input: SynthesisInput) -> SynthesisOutput:
    """
    合成流水线（确定性步骤 + LLM 最终生成）：

    Step 1: 收集所有 Worker 结果
    Step 2: RRF（Reciprocal Rank Fusion）跨源去重融合
    Step 3: 按 relevance_score 降序排列，取 top_k
    Step 4: 构建 LLM prompt（system + context chunks + user query）
    Step 5: LLM 生成最终回答（Markdown + 引用标注）
    Step 6: 构建降级信息和透明标注
    Step 7: 生成建议追问
    """
```

#### RRF 融合权重

```python
RRF_WEIGHTS = {
    "doc":  1.0,   # 默认等权
    "code": 1.0,
    "sql":  1.0,
}
# 特定 query_type 下可以调整：
# "data_query" → sql=1.5, doc=0.8, code=0.5
# "trace"      → doc=1.2, code=1.2, sql=0.8
```

#### 错误处理

| 失败场景 | 处理 |
|---------|------|
| 仅 1/3 Worker 成功 | 用部分结果生成回答 + missing_dimensions 标注 |
| 仅 1/3 Worker 成功且结果为空 | 仍然生成回答，明确告知用户"未在 [缺失维度] 中找到相关信息" |
| 0/3 Worker 成功 | 触发 L3 缓存兜底；缓存无结果 → L4 静态 FAQ |
| LLM 生成超时（>2000ms） | 返回结构化 sources 列表（无自然语言回答）+ "回答生成超时，以下是原始检索结果" |
| LLM 生成 500 错误 | 切换备用模型（Qwen3-32B）重试 |

#### 超时契约
- RRF 融合：< 10ms
- LLM 上下文构建：< 5ms
- LLM 生成回答：< 1500ms
- 建议追问生成：< 500ms（可与主回答并行）
- Synthesis 总预算：2000ms

#### 依赖
- **LLM（Sonnet，主）** — 必须
- **LLM（Qwen3-32B，备用）** — 主 LLM 失败时使用
- **Redis** — 可选（检查热点问答缓存，命中则跳过 LLM 生成）

---

## §7 Ingestion Engine（数据摄入管道）

### IF11: Doc Ingestion → PGVector

**方向：** Ingestion Scheduler → Confluence/Wiki → 解析器 → PGVector  
**协议：** PostgreSQL wire（写入）+ REST（Confluence API 读取）  
**触发：** Webhook（实时）+ APScheduler 每日凌晨全量

```python
class DocIngestionInput(BaseModel):
    source: str = "doc"
    scope: str                         # "incremental" | "full" | "single"
    target_url: Optional[str] = None   # scope=single 时指定 Confluence 页面 URL
    force: bool = False                # 跳过新鲜度检查
    trace_id: str

class DocIngestionOutput(BaseModel):
    task_id: str
    documents_processed: int
    chunks_created: int
    chunks_updated: int
    chunks_deleted: int
    errors: list["IngestionError"]
    started_at: datetime
    completed_at: datetime
    duration_seconds: float

class DocChunkToIndex(BaseModel):
    """写入 PGVector 的文档分块"""
    chunk_id: str
    content: str                        # 分块文本（~500 tokens）
    content_hash: str                   # SHA256，用于增量更新的变更检测
    embedding: list[float]              # BGE-M3(content)
    metadata: DocChunkMetadata
    source_url: str
    indexed_at: datetime
```

### IF12: Code Ingestion → PGVector

**方向：** Ingestion Scheduler → Git repos → AST Parser → PGVector  
**触发：** Git Webhook（push 事件）+ 全量索引（首次部署时）

```python
class CodeIngestionInput(BaseModel):
    source: str = "code"
    scope: str                         # "incremental" | "full" | "single"
    repo: str                          # 仓库名
    target_file: Optional[str] = None  # scope=single 时指定文件
    commits: list[str] = []            # scope=incremental 时 push 包含的 commit hash 列表
    trace_id: str

class CodeIngestionOutput(BaseModel):
    task_id: str
    repo: str
    files_processed: int
    functions_indexed: int
    functions_updated: int
    functions_deleted: int             # 代码删除 → 清理旧 chunks
    errors: list["IngestionError"]
    started_at: datetime
    completed_at: datetime
    duration_seconds: float

class CodeChunkToIndex(BaseModel):
    """写入 PGVector code_chunks 表的记录（注意：不存 embedding）"""
    chunk_id: str
    content: str                        # 完整源代码
    content_hash: str
    file_path: str
    line_start: int
    line_end: int
    function_name: Optional[str]
    class_name: Optional[str]
    language: str
    repo: str
    branch: str
    commit_hash: str
    author: str
    imports: list[str]
    calls: list[str]
    called_by: list[str]
    req_ids: list[str]                  # 从 git log commit message 中提取
    updated_at: datetime
    indexed_at: datetime
```

### IF13: SQL Schema Ingestion → PGVector

**方向：** Ingestion Scheduler → DB information_schema → PGVector  
**触发：** APScheduler 定时轮询（10min 间隔）+ 手动触发（Admin API → POST /admin/reindex）

```python
class SQLSchemaIngestionInput(BaseModel):
    source: str = "sql"
    scope: str                         # "full" | "incremental" | "single"
    target_table: Optional[str] = None  # scope=single 时指定表名
    include_few_shots: bool = True     # 是否刷新 few-shot 示例
    trace_id: str

class SQLSchemaIngestionOutput(BaseModel):
    task_id: str
    tables_processed: int
    columns_indexed: int
    schema_chunks_updated: int
    errors: list["IngestionError"]
    started_at: datetime
    completed_at: datetime
    duration_seconds: float

class SchemaChunkToIndex(BaseModel):
    """写入 PGVector 的 Schema 分块"""
    chunk_id: str
    table_name: str
    ddl: str                            # CREATE TABLE 语句
    columns: list[ColumnSchema]
    business_metadata: list[BusinessMetadata]
    embedding: list[float]              # BGE-M3(ddl + column_comments)
    few_shot_examples: list[SQLExample]
    indexed_at: datetime
```

### IF14: Manual Reindex Trigger（Admin API → Ingestion Engine）

**方向：** Admin API → Ingestion Scheduler  
**协议：** 进程内函数调用（当前）或 REST（拆分后）

```python
class ReindexRequest(BaseModel):
    source: str                         # "doc" | "code" | "sql"
    scope: str                          # "full" | "incremental" | "single"
    target: Optional[str] = None        # scope=single 时的目标
    triggered_by: str                   # 触发人
    trace_id: str

class ReindexResponse(BaseModel):
    task_id: str
    source: str
    scope: str
    status: str                         # "accepted" | "rejected"
    rejection_reason: Optional[str]     # 同数据源已有进行中任务 → 409
```

---

## §8 Degradation Manager（降级管理器，横切）

### IF-DEG: 健康信号收集 + 降级状态广播

**方向：** 所有组件 → Degradation Manager → 所有组件  
**协议：** 进程内 event bus（当前）或 Redis Pub/Sub（拆分后）

```python
# ── 健康信号（组件 → Degradation Manager）──

class HealthSignal(BaseModel):
    component: str          # "supervisor" | "doc_worker" | "code_worker" | "sql_worker" | "synthesis" | "pgvector" | "redis" | "llm_primary" | "llm_fallback"
    status: str             # "up" | "degraded" | "down"
    checked_at: datetime
    metrics: Optional["ComponentMetrics"]  # 可选的详细指标
    failure_count: int = 0  # 连续失败次数（熔断器）

class ComponentMetrics(BaseModel):
    error_rate_5m: Optional[float]       # 5分钟错误率
    p99_latency_ms: Optional[int]
    connection_pool_available: Optional[int]

# ── 降级决策（Deterministic + Auto-recovery）──

class DegradationDecision(BaseModel):
    level: int              # 0-4
    trigger: str            # 触发条件文本
    affected_components: list[str]
    auto_recovery_enabled: bool
    auto_recovery_condition: str   # "llm_primary.health_check.pass(consecutive=3)"
    decided_at: datetime

# ── 降级状态广播（Degradation Manager → 所有组件）──

class DegradationBroadcast(BaseModel):
    current_level: int
    previous_level: int
    trigger_reason: str
    affected_components: list[str]
    user_notice: str                # 展示给用户的透明标注
    auto_recovery_eta: Optional[str]
    switched_at: datetime
```

#### 降级触发条件（Design doc §多级降级策略）

```python
DEGRADATION_RULES = [
    # L0 → L1: 主 LLM 异常
    DegradationRule(
        condition="llm_primary.error_rate_5m > 0.3 OR llm_primary.consecutive_timeouts >= 3",
        action=DegradationAction(
            new_level=1,
            switch_llm_to="llm_fallback",
            affected=["supervisor", "synthesis"],
            user_notice="⚠️ 主模型暂时不可用，已切换到本地模型，回答质量可能略有下降。"
        )
    ),
    # L0 → L2: 向量库异常
    DegradationRule(
        condition="pgvector.p99_latency_ms > 500 OR pgvector.status == 'down'",
        action=DegradationAction(
            new_level=2,
            retrieval_fallback="bm25_only",
            affected=["doc_worker"],
            user_notice="⚠️ 文档检索当前降级为关键词搜索，部分结果可能不够精准。"
        )
    ),
    # L2 → L3: 大面积故障
    DegradationRule(
        condition="doc_worker.status == 'down' AND code_worker.status == 'down' AND sql_worker.status == 'down'",
        action=DegradationAction(
            new_level=3,
            fallback="redis_cache",
            affected=["supervisor", "all_workers"],
            user_notice="⚠️ 后端服务暂时不可用，正在展示缓存的热门问答。"
        )
    ),
    # L3 → L4: 完全不可用
    DegradationRule(
        condition="redis.status == 'down' AND all_workers.status == 'down'",
        action=DegradationAction(
            new_level=4,
            fallback="static_faq",
            affected=["all"],
            user_notice="⚠️ 系统暂时不可用，以下是常见问题。请稍后重试或联系管理员。"
        )
    ),
]

# 自动恢复：降级条件解除后持续 3 个健康检查周期（每个 30s）→ 逐级恢复
```

---

## §9 Feedback Pipeline（反馈闭环）

### IF15: Feedback → Quality Metrics

**方向：** API Gateway → Feedback Processor → Quality Metrics DB  
**协议：** PostgreSQL write

```python
class FeedbackEvent(BaseModel):
    query_id: str
    user_id: str
    rating: str                        # "positive" | "negative"
    reason: Optional[str]              # "inaccurate" | "incomplete" | "irrelevant" | "too_slow" | "other"
    comment: Optional[str]
    submitted_at: datetime

    # ── 关联的查询上下文（方便归因分析）──
    query_text: str
    classification_sources: list[str]
    entities: Optional[dict]
    degradation_level: int
    worker_latencies: dict[str, int]

class FeedbackAnalysisOutput(BaseModel):
    """每日/每周汇总输出"""
    period: str                         # "daily" | "weekly"
    total_feedback: int
    positive_rate: float
    negative_rate: float
    negative_by_reason: dict[str, int]  # {"inaccurate": 12, "too_slow": 5}
    top_problematic_queries: list[dict] # 最多负反馈的查询模式
    improvement_suggestions: list[str]  # LLM 自动分析的建议
```

---

## §10 所有接口延迟预算汇总

| 接口 | 组件 | 预算 | 实际来源 |
|------|------|------|---------|
| IF1 | Gateway → Supervisor | 32s（30s timeout + 2s margin） | 用户可配 |
| IF2 | Query Understanding | 500ms | 设计文档 LLM 分类 |
| IF2 | Task Decomposition | 5ms | 纯规则 |
| IF3/4 | Doc Worker | 1500ms | 设计文档 Trace |
| IF5/6 | Code Worker | 1200ms | 设计文档修正版 |
| IF7/8 | SQL Worker | 1500ms（+2400ms 自修复） | 设计文档 Trace |
| IF9 | Synthesis | 2000ms | 设计估算 |
| — | **端到端 P95** | **~5000ms** | 设计文档目标 ✅ |
| — | **端到端 P99** | **~8000ms** | 设计文档目标 ✅ |
| IF11 | Doc Ingestion | 异步，< 5min 增量 | 设计文档 |
| IF12 | Code Ingestion | 异步，< 5min 增量 | 设计文档 |
| IF13 | SQL Ingestion | 异步，< 10min 增量 | 设计文档 |

---

## 附录 A：跨源查询 trace 中的接口调用序列

```
用户: "REQ-187 改了哪些代码和表？"

IF1: Gateway → Supervisor
  → IF2: Classify: sources=["doc","code","sql"], entities={req_ids:["REQ-187"]}
  → IF2: completeness="rich" (req_ids 权重10) → retrieval_strategy="exact" for all workers
  → IF2: TaskPlan {tasks: [doc, code, sql], deps: {code: ["doc"], sql: ["doc"]}}

IF3: Supervisor → Doc Worker (timeout=1500ms)
  → PGVector: WHERE req_id='REQ-187' (精确匹配，< 50ms)
  → LLM: 提取 changes + req_ids  → DocWorkerResult.chunks + extracted_req_ids

IF5: Supervisor → Code Worker (timeout=1200ms, waits for Doc)
  → upstream_results={"doc": DocWorkerResult}
  → grep: "REQ-187" across all repos (git log --grep)
  → AST: 解析变更文件 → 提取 code_refs + tables
  → CodeWorkerResult.chunks + discovered_tables=["users","user_sessions"]

IF7: Supervisor → SQL Worker (timeout=1500ms, waits for Doc)
  → upstream_results={"doc": DocWorkerResult}
  → upstream_tables=["users","user_sessions"] (from Code Worker, but SQL runs parallel)
  → Schema RAG: exact match on table_names from upstream
  → 注意: Code 和 SQL 并行执行，所以 SQL 可能收不到 Code 的 discovered_tables
  → SQL 使用 Doc 的 req_ids + 自己的 module_query 做 Schema RAG
  → SQLWorkerResult.schema_results + generated_sql (可能 needs_confirmation)

IF9: Synthesis
  → RRF 融合 3 个 Worker 的结果
  → LLM 生成结构化回答（含引用标注）
  → 生成 degradation.user_notice（如果 L>0）
  → SynthesisOutput

IF10: Synthesis → Gateway → HTTP Response
```

---

## 附录 B：Pydantic 模型文件组织建议

```
src/spma/contracts/
├── __init__.py                # 导出所有公共类型
├── common.py                  # SPMAContext, SPMAError, SPMAComponent
├── gateway.py                 # GatewayQueryRequest, GatewayQueryResponse
├── supervisor.py              # ClassificationResult, ExtractedEntities, TaskPlan, WorkerTask
├── doc_worker.py              # DocWorkerTask, DocWorkerResult, DocChunk, DocChange
├── code_worker.py             # CodeWorkerTask, CodeWorkerResult, CodeChunk, GrepHit
├── sql_worker.py              # SQLWorkerTask, SQLWorkerResult, SchemaChunk, SQLExecutionResult, SQLConfirmationPrompt
├── synthesis.py               # SynthesisInput, SynthesisOutput, Citation
├── degradation.py             # HealthSignal, DegradationDecision, DegradationBroadcast
├── ingestion.py               # Doc/Code/SQL IngestionInput/Output, *ToIndex
├── feedback.py                # FeedbackEvent, FeedbackAnalysisOutput
└── entities.py                # ExtractedEntities（被 supervisor 引用但独立）
```

所有类型都是 Pydantic BaseModel——既可以在 LangGraph 中直接传递，也可以序列化为 JSON 通过 REST/gRPC 发送，支持未来的微服务拆分。
