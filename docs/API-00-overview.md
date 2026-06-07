# API 契约总览与通用约定

> 所属项目：[SPMA 全局概览](designs/SPMA-design-00-global-overview.md)
> 权威架构：[5独立Agent架构设计](designs/SPMA-design-07-agent-architecture.md)
> 文档状态：DESIGN COMPLETE
> 生成日期：2026-06-07

---

## 一、文档导航

| 文档 | 内容 | 受众 |
|------|------|------|
| [API-00 总览与约定](API-00-overview.md) | 通用约定、错误模型、版本策略（本文档） | 全部 |
| [API-01 外部 REST API](API-01-external-rest-api.md) | 用户→API Gateway→Supervisor 的 HTTP 契约 | 前端工程师、集成开发者 |
| [API-02 Agent 通信协议](API-02-agent-communication-protocol.md) | Supervisor↔Worker↔Synthesis 的内部协议 | 后端工程师 |
| [API-03 Worker 契约](API-03-worker-contracts.md) | Doc/Code/SQL Agent 的 I/O Schema | 后端工程师、Agent 开发者 |
| [API-04 Synthesis Agent 契约](API-04-synthesis-agent-contract.md) | Synthesis Agent 的融合审计契约 | 后端工程师 |
| [API-05 数据摄入 API](API-05-data-ingestion-api.md) | 数据摄入管道的触发与管理接口 | 数据工程师、运维 |
| [API-06 基础设施契约](API-06-infrastructure-contracts.md) | 状态存储、缓存、Feature Flag、配置 | 后端工程师、SRE |

---

## 二、架构中的契约边界

```
┌──────────────────────────────────────────────────────────────────────┐
│                        契约边界总览                                    │
│                                                                      │
│  ┌──────────┐     ① REST API       ┌──────────────┐                  │
│  │  User/UI  │ ◄─────────────────► │ API Gateway   │                  │
│  └──────────┘    (OpenAPI 3.1)     └──────┬───────┘                  │
│                                           │                          │
│                             ② 内部 Agent 协议                          │
│                      (JSON-RPC 2.0 inspired)                          │
│                                           │                          │
│                      ┌────────────────────▼──────────────────────┐   │
│                      │           Supervisor Agent                │   │
│                      └──┬──────────────┬──────────────┬─────────┘   │
│                         │ ③ Send API   │              │             │
│                         │ (LangGraph   │              │             │
│                         │  Subgraph)   │              │             │
│              ┌──────────▼┐  ┌─────────▼──┐  ┌───────▼────────┐      │
│              │ Doc Agent │  │ Code Agent │  │   SQL Agent    │      │
│              └─────┬─────┘  └─────┬──────┘  └───────┬────────┘      │
│                    │              │                  │               │
│                    └──────────────┼──────────────────┘               │
│                                   │ ④ WorkerOutput                   │
│                                   │ (TypedDict/Pydantic)              │
│                                   ▼                                  │
│                      ┌────────────────────────┐                      │
│                      │    Synthesis Agent     │                      │
│                      └────────────────────────┘                      │
│                                   │                                  │
│                                   │ ⑤ FinalResponse                  │
│                                   ▼                                  │
│                              User/UI                                 │
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐    │
│  │              ⑥ 数据摄入 API (内部管理接口)                      │    │
│  │  Ingest Pipeline ◄────► PGVector / Redis / Postgres           │    │
│  └──────────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 三、通用约定

### 3.1 编码与格式

| 约定 | 说明 |
|------|------|
| **字符编码** | UTF-8 |
| **日期时间格式** | ISO 8601 / RFC 3339 (`2026-06-07T10:23:45.123Z`) |
| **时区** | 所有时间戳使用 UTC，展示层负责本地化 |
| **语言代码** | BCP 47（`zh-CN`、`en-US`） |
| **JSON 格式** | `application/json`，snake_case 字段名 |
| **数值精度** | 金额/财务数据使用字符串传输（避免浮点精度丢失） |

### 3.2 字段命名规范

```yaml
# 统一使用 snake_case
query_id: "uuid-xxxx"
agent_type: "doc"
max_rounds: 3
has_exact_match: true

# 禁止混用 camelCase / PascalCase
# ❌ queryId, AgentType, MaxRounds
```

### 3.3 版本策略

| 层级 | 版本方式 | 示例 |
|------|---------|------|
| **REST API** | URL 路径版本 | `/api/v1/query` |
| **Agent 协议** | Header `X-Protocol-Version` | `X-Protocol-Version: 1.0` |
| **WorkerOutput** | Schema `$schema` 字段 | `"$schema": "spma/worker-output/1.0"` |
| **数据摄入** | 管道配置版本号 | `"pipeline_version": "1.2.0"` |

**兼容性规则：**
- 新增字段：向后兼容，旧客户端忽略未知字段
- 删除字段：提前一个版本标记 `deprecated`
- 类型变更：作为 Breaking Change，走主版本号升级

---

## 四、通用错误模型

### 4.1 标准错误响应体

```json
{
  "error": {
    "code": "CLASSIFICATION_FAILED",
    "message": "意图分类失败：LLM 服务不可用",
    "details": {
      "source": "supervisor_agent",
      "round": 1,
      "retryable": true,
      "fallback_used": "rule_based_classification"
    },
    "request_id": "550e8400-e29b-41d4-a716-446655440000"
  }
}
```

### 4.2 错误码体系

```
错误码命名规范: <DOMAIN>_<ERROR_TYPE>

DOMAIN 前缀:
  CLS   - 分类相关 (Classification)
  ENT   - 实体抽取相关 (Entity Extraction)
  QRY   - 查询改写相关 (Query Rewriting)
  DOC   - Doc Agent
  CODE  - Code Agent
  SQL   - SQL Agent
  SYN   - Synthesis Agent
  SUP   - Supervisor
  INF   - 基础设施 (Infrastructure)
  AUTH  - 认证授权
  RATE  - 限流
```

| HTTP 状态码 | 错误码 | 说明 | 是否可重试 |
|------------|--------|------|-----------|
| 400 | `INVALID_QUERY` | 查询为空或格式不合法 | 否（修改查询后重试） |
| 400 | `ENTITY_EXTRACTION_EMPTY` | 无法抽取任何实体 | 否（提供更多上下文后重试） |
| 401 | `AUTH_INVALID_TOKEN` | JWT 过期或无效 | 否（重新登录） |
| 403 | `AUTH_INSUFFICIENT_PERMISSION` | 无权限访问指定数据源 | 否 |
| 429 | `RATE_LIMIT_EXCEEDED` | 请求频率超限 | 是（等待 Retry-After） |
| 500 | `SUP_CLASSIFICATION_FAILED` | Supervisor 分类失败 | 是 |
| 500 | `DOC_SEARCH_FAILED` | Doc Agent 检索失败 | 是 |
| 500 | `CODE_SEARCH_FAILED` | Code Agent 检索失败 | 是 |
| 500 | `SQL_EXECUTION_FAILED` | SQL Agent 执行失败 | 是 |
| 500 | `SYN_FUSION_FAILED` | Synthesis Agent 融合失败 | 是 |
| 500 | `INF_REDIS_UNAVAILABLE` | Redis 不可用（已降级） | 否 |
| 500 | `INF_LLM_UNAVAILABLE` | 全部 LLM 不可用 | 是 |
| 502 | `INF_VECTOR_DB_UNAVAILABLE` | 向量数据库不可用 | 是 |
| 503 | `INF_SERVICE_DEGRADED` | 服务降级中 | 是（指数退避） |
| 504 | `INF_QUERY_TIMEOUT` | 查询超时（10s 硬上限） | 是（简化查询后重试） |

### 4.3 部分成功响应

当部分 Agent 成功、部分失败时，返回 HTTP 200 + 标注：

```json
{
  "status": "partial_success",
  "answer": "根据 PRD 文档，用户登录模块…",
  "degraded_sources": ["code"],
  "warnings": [
    {
      "source": "code",
      "code": "CODE_SEARCH_TIMEOUT",
      "message": "代码搜索超时（2s），以下结果可能不完整"
    }
  ],
  "citations": [...],
  "metadata": {...}
}
```

---

## 五、通用元数据结构

### 5.1 请求元数据

```json
{
  "request_id": "550e8400-e29b-41d4-a716-446655440000",
  "timestamp": "2026-06-07T10:23:45.123Z",
  "client_version": "spma-web/1.2.0",
  "session_id": "sess-xxxx",
  "user_id": "user-xxxx"
}
```

### 5.2 响应元数据

```json
{
  "request_id": "550e8400-e29b-41d4-a716-446655440000",
  "response_time_ms": 4200,
  "agent_trace": {
    "supervisor_rounds": 1,
    "doc_rounds": 2,
    "code_rounds": 1,
    "sql_rounds": 3,
    "synthesis_rounds": 1,
    "total_llm_calls": 8,
    "total_tokens": 4500,
    "degradation_level": "L0",
    "convergence_reason": "all_workers_above_threshold"
  },
  "model_info": {
    "classification_model": "claude-haiku-4-5",
    "generation_model": "claude-sonnet-4-6",
    "completeness_model": "claude-haiku-4-5"
  }
}
```

---

## 六、审计日志结构

每次查询生成一条审计日志，写入 PostgreSQL `audit_logs` 表：

```json
{
  "audit_id": "audit-xxxx",
  "request_id": "550e8400-e29b-41d4-a716-446655440000",
  "timestamp": "2026-06-07T10:23:45.123Z",
  "user_id": "user-xxxx",
  "session_id": "sess-xxxx",
  "original_query": "用户登录模块的PRD改了哪些内容？",
  "classification": {
    "sources": ["doc", "code", "sql"],
    "is_cross_source": true,
    "query_type": "trace"
  },
  "entities_extracted": {
    "module": "用户登录",
    "req_ids": [],
    "table_names": [],
    "code_refs": []
  },
  "agent_results": {
    "doc": {"result_count": 5, "confidence": 0.85, "rounds_used": 2},
    "code": {"result_count": 3, "confidence": 0.78, "rounds_used": 1},
    "sql": {"result_count": 2, "confidence": 0.82, "rounds_used": 3}
  },
  "final_answer_snippet": "根据 PRD 文档，用户登录模块…（截断至200字符）",
  "citations_count": 8,
  "user_feedback": null,
  "degradation_level": "L0",
  "latency_ms": 4200,
  "llm_calls": 8,
  "total_tokens": 4500,
  "estimated_cost_usd": 0.035
}
```

> 审计日志在查询结束后异步写入，不阻塞用户响应。

---

## 七、通用安全约定

### 7.1 认证

所有 API 端点需要认证：

```
Authorization: Bearer <JWT_TOKEN>
```

- JWT 由企业 SSO（OIDC/LDAP）签发
- Token 有效期：8 小时
- 刷新策略：静默刷新（过期前 30 分钟）

### 7.2 数据脱敏

所有发送给外部 LLM API 的文本经过脱敏层处理：

| 敏感类型 | 替换为 | 示例 |
|---------|--------|------|
| 手机号 | `<PHONE>` | `13812345678` → `<PHONE>` |
| 邮箱 | `<EMAIL>` | `user@company.com` → `<EMAIL>` |
| 金额 | `<AMOUNT>` | `¥1,234,567.89` → `<AMOUNT>` |
| 内部 IP | `<INTERNAL_IP>` | `10.0.1.100` → `<INTERNAL_IP>` |
| 主机名 | `<HOSTNAME>` | `db-master-01` → `<HOSTNAME>` |

### 7.3 限流

| 限流维度 | 默认值 | 响应 |
|---------|--------|------|
| 每用户每分钟 | 30 请求 | 429 + `Retry-After: 60` |
| 每 IP 每分钟 | 60 请求 | 429 + `Retry-After: 60` |
| 全局每分钟 | 1000 请求 | 429 + `Retry-After: 60` |

---

## 八、行业对标与设计依据

本 API 契约设计参考了以下行业标准与实践：

| 参考来源 | 借鉴内容 |
|---------|---------|
| **Google A2A Protocol (v0.3)** | Agent Card 机制、Task 生命周期、JSON-RPC 2.0 风格、SSE 流式传输 |
| **OpenAI Agents SDK** | Handoff 协议、`outputType` 结构化输出、typed contract pattern |
| **LangGraph** | Send API 并行派发、Subgraph 契约、Reducer 合并、Checkpointer 隔离 |
| **OpenAPI 3.1** | REST API 描述、错误模型、版本策略 |
| **CloudEvents** | 事件格式标准化（数据摄入管道） |
| **OpenTelemetry** | Trace/Span 结构（审计日志） |

### 设计原则

1. **契约优先（Contract-First）：** 先定义接口 Schema，再实现逻辑
2. **类型安全（Type-Safe）：** 所有接口使用 Pydantic/TypedDict 建模，编译期保证兼容性
3. **向后兼容（Backward Compatible）：** 新增字段不断旧客户端，废弃字段提前标记
4. **确定性优先（Determinism-First）：** 纯规则校验（Action Guard、实体完备度）优先于 LLM 判断
5. **不静默失败（No Silent Failure）：** 降级/异常通过标注显式告知用户
6. **可观测内建（Observability-Built-In）：** 每个请求可追踪到每个 Agent 的每轮循环
