# API 契约：外部 REST API

> 所属项目：[SPMA 全局概览](../SPMA-design-00-global-overview.md)
> 契约边界：**User/UI ↔ API Gateway ↔ Supervisor Agent**
> 协议：HTTPS + JSON + SSE（流式）
> 版本：v1

---

## 一、端点总览

| 方法 | 路径 | 说明 | 超时 |
|------|------|------|------|
| `POST` | `/api/v1/query` | 提交查询（非流式） | 30s |
| `POST` | `/api/v1/query/stream` | 提交查询（SSE 流式） | 60s |
| `GET` | `/api/v1/query/{query_id}` | 查询历史记录 | 5s |
| `GET` | `/api/v1/session/{session_id}` | 获取会话上下文 | 5s |
| `DELETE` | `/api/v1/session/{session_id}` | 清除会话历史 | 5s |
| `POST` | `/api/v1/feedback` | 提交用户反馈 | 5s |
| `GET` | `/api/v1/health` | 健康检查 | 2s |
| `GET` | `/api/v1/agent-card` | Agent Card（类似 A2A） | 2s |

---

## 二、核心端点：提交查询

### 2.1 请求

```
POST /api/v1/query
Content-Type: application/json
Authorization: Bearer <JWT>
X-Request-ID: 550e8400-e29b-41d4-a716-446655440000
X-Session-ID: sess-xxxx (可选，多轮对话时传入)
```

**请求体 Schema：**

```json
{
  "$schema": "spma/query-request/1.0",
  "query": "用户登录模块的PRD改了哪些内容？影响了哪些代码文件和数据库表？",
  "context": {
    "session_id": "sess-xxxx",
    "user_id": "user-xxxx",
    "user_role": "developer",
    "preferred_sources": [],
    "max_results_per_source": 10,
    "timeout_ms": 10000
  },
  "hints": {
    "req_ids": [],
    "module": "用户登录",
    "time_range": null,
    "version": null
  }
}
```

**Pydantic 模型：**

```python
from pydantic import BaseModel, Field
from typing import Optional, Literal
from uuid import UUID

class QueryContext(BaseModel):
    """查询上下文"""
    session_id: Optional[str] = Field(None, description="多轮对话的会话 ID")
    user_id: str = Field(..., description="用户标识")
    user_role: Literal["pm", "developer", "dba", "admin"] = "developer"
    preferred_sources: list[Literal["doc", "code", "sql"]] = Field(
        default_factory=list,
        description="用户指定的优先数据源（空=自动）"
    )
    max_results_per_source: int = Field(default=10, ge=1, le=50)
    timeout_ms: int = Field(default=10000, ge=1000, le=30000)

class QueryHints(BaseModel):
    """用户提供的检索提示（可选）"""
    req_ids: list[str] = Field(default_factory=list)
    module: Optional[str] = None
    time_range: Optional[str] = None
    version: Optional[str] = None
    table_names: list[str] = Field(default_factory=list)
    code_refs: list[str] = Field(default_factory=list)

class QueryRequest(BaseModel):
    """查询请求"""
    query: str = Field(..., min_length=1, max_length=2000)
    context: QueryContext
    hints: QueryHints = Field(default_factory=QueryHints)
```

### 2.2 成功响应（200）

```json
{
  "status": "success",
  "request_id": "550e8400-e29b-41d4-a716-446655440000",
  "answer": "## 用户登录模块 PRD 变更分析\n\n### 1. PRD 变更内容\n根据 `REQ-2024-0187`，用户登录模块做了以下变更…[PRD §3.2]\n\n### 2. 影响的代码文件\n- `src/auth/oauth.py:42` — `token_refresh` 函数\n- `src/auth/login.py:15` — `login_oauth` 函数\n\n### 3. 影响的数据库表\n- `users` 表新增 `oauth_provider` 列\n- `user_sessions` 表新增 `refresh_token_expires_at` 列",
  "citations": [
    {
      "source_type": "prd",
      "source_id": "doc_001:chunk_3",
      "snippet": "## 3.2 OAuth2.0 登录流程\n用户点击登录按钮后…",
      "relevance_score": 0.92
    },
    {
      "source_type": "code",
      "source_id": "src/auth/oauth.py:42",
      "snippet": "def token_refresh(token: str) -> Token:",
      "relevance_score": 0.88
    },
    {
      "source_type": "sql",
      "source_id": "users.oauth_provider",
      "snippet": "oauth_provider VARCHAR(50) -- OAuth提供商",
      "relevance_score": 0.85
    }
  ],
  "synthesis_notes": {
    "citations_verified": 8,
    "citations_unverified": 0,
    "contradictions": [],
    "coverage_gaps": []
  },
  "metadata": {
    "response_time_ms": 4200,
    "agent_trace": {
      "supervisor_rounds": 1,
      "doc_rounds": 2,
      "code_rounds": 1,
      "sql_rounds": 3,
      "synthesis_rounds": 1,
      "total_llm_calls": 8,
      "total_tokens": 4500,
      "estimated_cost_usd": 0.035,
      "degradation_level": "L0"
    },
    "data_freshness": {
      "doc_updated_at": "2026-06-05T10:00:00Z",
      "code_indexed_at": "2026-06-07T09:55:00Z",
      "sql_schema_refreshed_at": "2026-06-07T09:50:00Z"
    }
  }
}
```

### 2.3 部分成功响应（200 + 降级标注）

```json
{
  "status": "partial_success",
  "request_id": "...",
  "answer": "根据 PRD 文档，用户登录模块做了以下变更…\n\n⚠️ 代码搜索超时，以下信息可能缺少代码维度的结果。",
  "citations": [...],
  "degraded_sources": [
    {
      "source": "code",
      "error_code": "CODE_SEARCH_TIMEOUT",
      "message": "代码搜索超时（2s），已返回部分结果",
      "rounds_completed": 1,
      "confidence": 0.45
    }
  ],
  "metadata": {
    "degradation_level": "L2",
    "agent_trace": {...}
  }
}
```

### 2.4 错误响应

```json
{
  "status": "error",
  "request_id": "...",
  "error": {
    "code": "INF_QUERY_TIMEOUT",
    "message": "查询超时（10s 硬上限），请简化查询或指定数据源后重试",
    "details": {
      "timeout_at": "2026-06-07T10:23:55.123Z",
      "partial_results_available": true,
      "completed_sources": ["doc"],
      "failed_sources": ["code", "sql"],
      "retryable": true
    }
  }
}
```

---

## 三、流式端点：SSE 查询

### 3.1 请求

```
POST /api/v1/query/stream
Content-Type: application/json
Authorization: Bearer <JWT>
Accept: text/event-stream
```

请求体与 `/api/v1/query` 相同。

### 3.2 SSE 事件流

```
event: classification
data: {"status":"classifying","sources":["doc","code","sql"],"query_type":"trace","entities":{"module":"用户登录"},"latency_ms":480}

event: worker_start
data: {"agent_type":"doc","round":1,"timestamp":"2026-06-07T10:23:45.600Z"}

event: worker_start
data: {"agent_type":"code","round":1,"timestamp":"2026-06-07T10:23:45.601Z"}

event: worker_start
data: {"agent_type":"sql","round":1,"timestamp":"2026-06-07T10:23:45.602Z"}

event: worker_progress
data: {"agent_type":"doc","round":1,"action":"bm25_vector_search","results_so_far":3,"latency_ms":45}

event: worker_progress
data: {"agent_type":"sql","round":2,"action":"generate_sql","status":"guard_passed","latency_ms":350}

event: worker_done
data: {"agent_type":"doc","result_count":5,"confidence":0.85,"rounds_used":2,"has_exact_match":false}

event: worker_done
data: {"agent_type":"code","result_count":3,"confidence":0.78,"rounds_used":1,"has_exact_match":false}

event: worker_done
data: {"agent_type":"sql","result_count":2,"confidence":0.82,"rounds_used":3,"has_exact_match":true}

event: synthesizing
data: {"status":"generating","round":1}

event: answer_chunk
data: {"content":"## 用户登录模块 PRD 变更分析\n\n### 1. PRD 变更内容\n"}

event: answer_chunk
data: {"content":"根据 `REQ-2024-0187`，用户登录模块做了以下变更…\n"}

event: done
data: {"status":"success","request_id":"...","citations_count":8,"total_llm_calls":8,"response_time_ms":4200}
```

### 3.3 SSE 事件类型定义

```python
from enum import StrEnum

class SSEEventType(StrEnum):
    CLASSIFICATION = "classification"       # 分类完成
    WORKER_START = "worker_start"           # Worker 开始执行
    WORKER_PROGRESS = "worker_progress"     # Worker 进度更新
    WORKER_DONE = "worker_done"             # Worker 完成
    WORKER_ERROR = "worker_error"           # Worker 异常
    SUPERVISOR_RESCHEDULE = "supervisor_reschedule"  # 重调度
    SYNTHESIZING = "synthesizing"           # 融合生成中
    ANSWER_CHUNK = "answer_chunk"           # 答案片段
    WARNING = "warning"                     # 警告信息
    DONE = "done"                           # 完成
    ERROR = "error"                         # 致命错误
```

---

## 四、会话管理

### 4.1 获取会话上下文

```
GET /api/v1/session/{session_id}
Authorization: Bearer <JWT>
```

**响应：**

```json
{
  "session_id": "sess-xxxx",
  "user_id": "user-xxxx",
  "created_at": "2026-06-07T09:00:00Z",
  "updated_at": "2026-06-07T10:23:45Z",
  "turns": [
    {
      "turn_id": 1,
      "query": "用户登录模块的PRD是什么？",
      "classification": {"sources": ["doc"], "query_type": "search"},
      "entities": {"module": "用户登录"},
      "timestamp": "2026-06-07T09:00:00Z"
    },
    {
      "turn_id": 2,
      "query": "它影响了哪些代码和表？",
      "rewritten_query": "用户登录模块影响了哪些代码文件和数据库表？",
      "classification": {"sources": ["doc","code","sql"], "query_type": "trace"},
      "entities": {"module": "用户登录"},
      "context_inherited_from": 1,
      "timestamp": "2026-06-07T10:23:45Z"
    }
  ]
}
```

### 4.2 清除会话

```
DELETE /api/v1/session/{session_id}
Authorization: Bearer <JWT>
```

**响应：** `204 No Content`

---

## 五、用户反馈

```
POST /api/v1/feedback
Content-Type: application/json
Authorization: Bearer <JWT>
```

```json
{
  "request_id": "550e8400-e29b-41d4-a716-446655440000",
  "rating": "thumbs_up",
  "comment": "代码引用很精准，但 SQL 部分缺了 user_sessions 表的字段说明",
  "tags": ["missing_info", "sql"]
}
```

**rating 枚举：** `thumbs_up` | `thumbs_down` | `neutral`

---

## 六、健康检查

```
GET /api/v1/health
```

```json
{
  "status": "healthy",
  "version": "1.2.0",
  "uptime_seconds": 345600,
  "components": {
    "api_gateway": "healthy",
    "supervisor_agent": "healthy",
    "doc_agent": "healthy",
    "code_agent": "healthy",
    "sql_agent": "healthy",
    "synthesis_agent": "healthy",
    "pgvector": "healthy",
    "redis": "healthy",
    "llm_api": "healthy",
    "readonly_db": "healthy"
  },
  "degradation": {
    "level": "L0",
    "active_degradations": []
  }
}
```

---

## 七、Agent Card（服务发现）

参考 Google A2A Protocol 的 Agent Card 机制：

```
GET /api/v1/agent-card
```

```json
{
  "name": "SPMA - 企业级多源RAG智能问答系统",
  "description": "统一查询 PRD 文档、代码仓库和 SQL 数据库，支持跨源溯源",
  "version": "1.2.0",
  "endpoint": "https://spma.internal.company.com/api/v1/query",
  "capabilities": {
    "streaming": true,
    "multimodal": false,
    "stateful_session": true,
    "supported_query_types": ["trace", "search", "data_query", "explain"],
    "supported_sources": ["doc", "code", "sql"],
    "max_timeout_ms": 30000
  },
  "skills": [
    {
      "name": "cross_source_trace",
      "description": "跨源溯源查询——追踪需求→代码→数据库的影响链",
      "input_modes": ["text"],
      "output_modes": ["text", "citations"]
    },
    {
      "name": "text_to_sql",
      "description": "自然语言转 SQL——安全地在只读副本上执行数据查询",
      "input_modes": ["text"],
      "output_modes": ["text", "sql", "table"]
    },
    {
      "name": "document_search",
      "description": "PRD 文档检索——混合 BM25 + 语义搜索",
      "input_modes": ["text"],
      "output_modes": ["text", "citations"]
    },
    {
      "name": "code_search",
      "description": "代码检索——ripgrep + AST 调用图扩展",
      "input_modes": ["text"],
      "output_modes": ["text", "code", "citations"]
    }
  ],
  "auth": {
    "schemes": ["bearer_jwt", "api_key"],
    "oidc_provider": "https://sso.internal.company.com"
  },
  "rate_limits": {
    "requests_per_minute_per_user": 30,
    "requests_per_minute_per_ip": 60
  },
  "observability": {
    "metrics_endpoint": "https://spma.internal.company.com/metrics",
    "status_endpoint": "https://spma.internal.company.com/api/v1/health",
    "documentation_url": "https://wiki.internal.company.com/spma"
  }
}
```

---

## 八、OpenAPI 3.1 片段（核心端点）

```yaml
openapi: "3.1.0"
info:
  title: SPMA Query API
  version: "1.0.0"
  description: 企业级多源RAG智能问答系统 REST API

servers:
  - url: https://spma.internal.company.com/api/v1
    description: 生产环境

paths:
  /query:
    post:
      operationId: submitQuery
      summary: 提交查询
      tags: [Query]
      parameters:
        - in: header
          name: X-Request-ID
          required: true
          schema:
            type: string
            format: uuid
        - in: header
          name: X-Session-ID
          required: false
          schema:
            type: string
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/QueryRequest'
      responses:
        '200':
          description: 查询成功
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/QueryResponse'
        '400':
          $ref: '#/components/responses/BadRequest'
        '429':
          $ref: '#/components/responses/RateLimited'
        '504':
          $ref: '#/components/responses/Timeout'

  /query/stream:
    post:
      operationId: submitStreamQuery
      summary: 提交流式查询（SSE）
      tags: [Query]
      parameters:
        - in: header
          name: X-Request-ID
          required: true
          schema:
            type: string
            format: uuid
        - in: header
          name: X-Session-ID
          required: false
          schema:
            type: string
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/QueryRequest'
      responses:
        '200':
          description: SSE 事件流
          content:
            text/event-stream:
              schema:
                type: string

components:
  schemas:
    QueryRequest:
      type: object
      required: [query, context]
      properties:
        query:
          type: string
          minLength: 1
          maxLength: 2000
          example: "用户登录模块的PRD改了哪些内容？"
        context:
          $ref: '#/components/schemas/QueryContext'
        hints:
          $ref: '#/components/schemas/QueryHints'

    QueryResponse:
      type: object
      properties:
        status:
          type: string
          enum: [success, partial_success, error]
        request_id:
          type: string
          format: uuid
        answer:
          type: string
          description: Markdown 格式的回答
        citations:
          type: array
          items:
            $ref: '#/components/schemas/Citation'
        metadata:
          $ref: '#/components/schemas/ResponseMetadata'
```
