# Design: Phase 1 — SQL Agent（Text-to-SQL 执行 Agent）

> **版本:** v1.0 | **日期:** 2026-06-07 | **状态:** 设计完成
> **父 PRD:** [PRD-02 Phase 1](../prds/PRD-02-phase1-sql-agent.md)
> **上游设计:** [SPMA-design-04 SQL Worker](../docs/designs/SPMA-design-04-sql-worker.md)
> **Phase 0 通关结果:** Plan A（LLM 完备度判断精确率 ≥ 80%，LLM 语义验证保留）

---

## 目录

1. [关键设计决策](#一关键设计决策)
2. [API 契约](#二api-契约)
3. [Agent 循环状态机](#三agent-循环状态机)
4. [SQL Guard 五层校验](#四sql-guard-五层校验)
5. [Schema RAG](#五schema-rag)
6. [质量检测](#六质量检测)
7. [代码结构](#七代码结构)

---

## 一、关键设计决策

以下决策是在 PRD 基础上的细化和确认，若有冲突以此为准。

| # | 决策 | 选项 | 结论 |
|---|------|------|------|
| 1 | Phase 0 通关状态 | Plan A / Plan B | **Plan A** — LLM 语义验证保留，作为确定性收敛之外的兜底 |
| 2 | 用户交互形态 | 纯 API / Streamlit 正式 UI | **纯 API + Streamlit demo** — SQL Agent 是可调用的 API 服务，Streamlit 仅开发测试用 |
| 3 | Qwen3-8B 降级策略 | 仅不可用时切换 / 分级使用 / 动态路由 | **仅 Claude API 不可用时切换** — 平时全走 Claude，Qwen 闲置 |
| 4 | API 模式下确认闸门 | 双步 API / 自动放行 / 配置开关 | **双步 API** — 高风险查询先返回 SQL + confirmation_token，用户确认后执行 |
| 5 | 确认 token 超时处理 | 返回错误+原查询 / 自动续期 / 视为拒绝 | **返回过期错误 + 原查询** — 调用方可自动重新发起，用户无感知 |
| 6 | Schema RAG embedding 策略 | 嵌入 DDL / 嵌入业务描述 | **嵌入业务描述** — DDL 中共享列（id/name/status）会导致向量相似度虚高，改为只嵌入表的业务含义 |
| 7 | 实现策略 | 基础设施先行 / 垂直切片 / 模块独立 | **垂直切片** — 第 1 周用 Mock Schema + SQLite 跑通端到端，然后逐步替换为真实组件 |

---

## 二、API 契约

Phase 1 暴露三个端点，不经过 Supervisor（Phase 3 引入），直接面向调用方。

### 2.1 查询端点

**`POST /api/v1/sql/query`**

```json
// Request
{
  "query": "过去7天各状态的订单数",
  "session_id": "uuid-optional",
  "auto_confirm": false
}
```

**响应：正常收敛**

```json
{
  "status": "completed",
  "sql": "SELECT status, COUNT(*) FROM orders WHERE created_at >= NOW() - INTERVAL '7 days' GROUP BY status",
  "result": {
    "columns": ["status", "count"],
    "rows": [["paid", 847], ["pending", 123], ["cancelled", 34]],
    "row_count": 3,
    "execution_time_ms": 45,
    "replica_lag_ms": 3,
    "data_snapshot_at": "2026-06-07T14:32:18Z"
  },
  "rounds": 1,
  "quality_report": {
    "issues": [],
    "confidence": 1.0
  },
  "worker_output": {
    "worker_type": "sql",
    "execution_sql": "SELECT status, COUNT(*) FROM ...",
    "guard_risk_level": "low",
    "tables_used": ["orders"],
    "columns_used": ["status", "created_at"],
    "data_limitations": []
  }
}
```

**响应：需要确认**

```json
{
  "status": "confirmation_required",
  "confirmation_token": "tok_abc123",
  "sql": "SELECT SUM(amount) FROM orders WHERE status = 'paid'",
  "risk": {
    "level": "medium",
    "reasons": ["涉及财务指标聚合"],
    "tables_involved": ["orders"],
    "estimated_rows": 1
  },
  "expires_at": "2026-06-07T14:35:18Z"
}
```

**响应：被拦截**

```json
{
  "status": "blocked",
  "guard_result": {
    "passed": false,
    "forbidden_operations": ["DELETE"],
    "risk_level": "blocked"
  }
}
```

### 2.2 确认端点

**`POST /api/v1/sql/query/confirm`**

```json
// 确认执行
{
  "confirmation_token": "tok_abc123",
  "action": "execute"
}

// 修改查询
{
  "confirmation_token": "tok_abc123",
  "action": "modify",
  "modified_query": "过去7天各状态的订单数——排除测试订单"
}
```

响应同 `/query` 的 completed 响应。

**token 过期（3 分钟 TTL）：**

```json
{
  "status": "error",
  "error": "confirmation_token_expired",
  "message": "确认令牌已过期（有效期3分钟），请重新提交查询",
  "original_query": "上个月的营收是多少"
}
```

调用方可拿 `original_query` 自动重新发起 `/query`，用户无感知。

### 2.3 Schema 查询端点

**`GET /api/v1/sql/schema?q=users表有哪些字段`**

```json
{
  "table": "users",
  "columns": [
    {
      "column_name": "id",
      "data_type": "integer",
      "is_nullable": false,
      "comment": "用户ID",
      "business_meaning": "用户唯一标识"
    }
  ],
  "business_metadata": {
    "table_description": "用户表，记录账号信息",
    "row_count_estimate": 50000
  }
}
```

---

## 三、Agent 循环状态机

### 3.1 节点与流转

```
        ┌─────────────────────────────────────────────┐
        │          SQL Agent StateGraph               │
        │                                             │
        │  ┌──────────┐                               │
        │  │ generate │◄──────────── error_feedback ───┼── (guard 失败)
        │  └────┬─────┘                               │   (verify 不通过)
        │       │                                     │
        │       ▼                                     │
        │  ┌──────────┐   失败                        │
        │  │  guard   ├──────── error_feedback ───────┼──→ 回到 generate
        │  └────┬─────┘                               │
        │       │ 通过                                │
        │       ▼                                     │
        │  ┌──────────────┐   需要确认                 │
        │  │ confirmation │───→ SUSPEND ──────────────┼──→ 等 POST /confirm
        │  │    gate      │                               │
        │  └──────┬───────┘                               │
        │         │ 不需要确认 / 已确认                    │
        │         ▼                                     │
        │  ┌──────────┐   执行异常                       │
        │  │ execute  ├──────── error_feedback ─────────┼──→ 回到 generate
        │  └────┬─────┘                               │
        │       │ 成功                                 │
        │       ▼                                     │
        │  ┌──────────┐   不通过                        │
        │  │ verify   ├──────── anomaly_feedback ──────┼──→ 回到 generate
        │  └────┬─────┘                               │
        │       │ 通过                                 │
        │       ▼                                     │
        │  ┌──────────┐                               │
        │  │ quality  │  ← 不阻塞，永远通过             │
        │  └────┬─────┘                               │
        │       │                                     │
        │       ▼                                     │
        │      END                                    │
        └─────────────────────────────────────────────┘
```

### 3.2 收敛条件（优先级从高到低）

```
1. 确定性条件（不调 LLM）:
   行数 ∈ [1, 10000]     →  立即收敛 ✓
   行数 = 0 且上轮也是 0  →  收敛 + QualityReport 标记空结果
   行数 > 10000           →  不收敛，反馈"加 LIMIT 或聚合"

2. LLM 语义验证兜底（确定性不满足时调 Haiku，~300ms）:
   Haiku 判断: 执行结果是否语义正确地回答了用户问题？
   ├─ "sufficient"     → 收敛 ✓
   └─ "insufficient"   → 不收敛，用 Haiku 的 missing_info 构造反馈

3. 强制终止（不判断，直接停）:
   当前轮数 >= 5         →  强制收敛，返回最后一轮结果 + QualityReport
   耗时 >= 3s            →  强制收敛，返回最后成功执行的 SQL 结果
```

### 3.3 状态字段

```python
class SQLAgentState(AgentState, total=False):
    # 输入
    query: str
    original_query: str
    entities: WorkerEntities

    # Schema RAG
    schema_search_results: list[SchemaHit]
    business_metadata: dict

    # SQL 生成与校验
    generated_sql: str
    guard_result: GuardResult
    guard_passed: bool

    # 确认闸门
    confirmation_required: bool        # 是否需要用户确认
    confirmation_token: str            # 暂停令牌，Redis 3min TTL
    confirmation_status: str           # "pending" | "approved" | "modified"

    # 执行
    execution_result: QueryResult
    execution_success: bool
    row_count: int

    # 语义验证
    semantic_check: str                # "passed" | "failed: reason"

    # 质量
    quality_report: QualityReport

    # 循环控制
    sql_history: list[str]             # 每轮生成的 SQL
    max_rounds: int                    # 5
    timeout_ms: int                    # 3000
    current_round: int
    start_time: float
```

### 3.4 确认闸门暂停机制

确认闸门不是 LangGraph 的节点，而是**暂停点**：

1. `confirmation_gate` 节点评估 SQL 风险 → 如高风险，设置 `confirmation_required=True`
2. LangGraph 返回 `status: "confirmation_required"` 给 API 层
3. 状态序列化到 Redis，key = `confirmation_token`，TTL = 3 分钟
4. 用户调用 `POST /query/confirm` → 从 Redis 恢复状态 → Agent 从暂停点继续进入 `execute`

过期处理：token 过期后 Redis key 自动删除，确认 API 返回 `confirmation_token_expired` 错误 + `original_query`，调用方可自动重试。

### 3.5 LLM 降级路由

```
SQL 生成:
  try Claude Sonnet → 失败/超时/限流 → Qwen3-8B

语义验证:
  try Claude Haiku → 失败/超时/限流 → Qwen3-8B

Qwen3-8B 也失败 → 返回错误给用户（非静默）
```

---

## 四、SQL Guard 五层校验

### 4.1 每层职责

| 层 | 检测内容 | 工具 | 短路？ |
|----|---------|------|--------|
| **L1 语法** | SQL 是否可解析、中文标点、缺失关键字 | SQLGlot | 是——失败则停 |
| **L2 操作** | DELETE/UPDATE/DROP/INSERT/TRUNCATE/ALTER/CREATE/GRANT/EXECUTE | SQLGlot AST 遍历 | 是——安全原因 |
| **L3 存在性** | 每个表名/列名在 Schema 快照中存在 | Schema 快照 + Levenshtein 纠错 | 是——不存在则无法执行 |
| **L4 性能** | 缺失 WHERE、≥3 JOIN、笛卡尔积风险、缺失 LIMIT | AST 结构检查 | **否**——仅警告 |
| **L5 执行** | 只读副本 + `statement_timeout=2s` + 数据库只读权限兜底 | psycopg2 连接池 | 执行失败反馈 |

### 4.2 L3 模糊纠错

表名/列名不存在时不只是报错，而是做一次快速字符串相似度匹配：

```
用户生成的 SQL 引用了 "oder_items"
  → Schema 快照中没有 "oder_items"
  → Levenshtein 距离最近的: "order_items" (距离=1)
  → 反馈: "表 'oder_items' 不存在，您是否想查 'order_items'？"
  → LLM 在下一轮用 "order_items" 重新生成
```

### 4.3 GuardResult

```python
class GuardResult:
    passed: bool                          # L1+L2+L3 都通过
    syntax_errors: list[str]              # L1
    forbidden_operations: list[str]       # L2（非空 = 立即拦截）
    table_existence_errors: list[str]     # L3（含"您是否想查 X？"建议）
    performance_warnings: list[str]       # L4（不拦截）
    risk_level: str                       # "low" | "medium" | "high" | "blocked"
    requires_user_confirmation: bool      # 综合 L4 + 业务规则判断
```

### 4.4 L5 双重保险

- 应用层：psycopg2 连接只读副本，连接串中 `options='-c default_transaction_read_only=on'`
- 数据库层：只读副本用户只有 SELECT 权限——即使应用层被绕过，数据库权限也会拒绝写操作

---

## 五、Schema RAG

### 5.1 两条检索路径

```
用户 query: "过去7天各状态的订单数"
  │
  ├─ entities.table_names = ["orders"] (非空)
  │   → 路径 A: 精确命中
  │   → 直接用 table_name 从 PGVector 精确查询
  │   → 延迟 ~50ms
  │
  └─ entities.table_names = [] (空)
      → 路径 B: 语义搜索
      → BGE-M3(query) → PGVector HNSW 搜索 top_k=5
      → 延迟 ~100ms
```

两条路径输出格式相同（`list[SchemaHit]`），下游 LLM 无需区分。

### 5.2 Embedding 策略：嵌入业务描述而非 DDL

真实数据库中 `id`、`name`、`status`、`created_at` 在几十张表中反复出现，嵌入 DDL 会导致向量高度相似，语义搜索返回无关表。

**方案：** embedding 文本只包含业务描述，DDL 和列类型作为 payload 存储。

```
存入 PGVector 的 embedding 文本（每表一条）:

"orders 表: 订单表，记录用户下单信息。
 列: order_id(订单ID), user_id(用户ID), status(订单状态: pending/paid/cancelled/refunded),
     amount(订单金额), created_at(创建时间)。
 外键: user_id → users.user_id。
 业务规则: 只有 paid 状态计入营收。"
```

DDL、列类型、外键关系作为 JSONB 列同表存储，一次查询全返回。

### 5.3 PGVector 表结构

```sql
CREATE TABLE schema_chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    table_name TEXT NOT NULL UNIQUE,
    business_description TEXT NOT NULL,       -- 入 embedding 的文本
    ddl TEXT,                                 -- payload
    columns_meta JSONB,                       -- payload
    foreign_keys JSONB,                       -- payload
    few_shot_queries TEXT[],                  -- payload
    embedding VECTOR(1024),                   -- BGE-M3 向量
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX ON schema_chunks USING hnsw (embedding vector_cosine_ops);
```

一次查询拿回所有信息：

```sql
SELECT table_name, business_description, ddl, columns_meta,
       foreign_keys, few_shot_queries,
       1 - (embedding <=> $query_embedding) AS relevance_score
FROM schema_chunks
ORDER BY embedding <=> $query_embedding
LIMIT 5;
```

### 5.4 业务元数据来源

| 来源 | 内容 | 示例 | 新鲜度 |
|------|------|------|--------|
| `information_schema` 列注释 | DDL 注释 | `COMMENT ON COLUMN orders.status IS '订单状态'` | 10min 轮询 |
| 代码 AST 提取 | Enum 定义 | `class OrderStatus: PENDING='pending'` | 每日全量 |
| 人工 curator 标注 | 业务规则 | "排除 user_id < 100 的测试订单" | 手动维护 |

### 5.5 摄入管道

```
information_schema ──(10min 轮询)──→ schema_introspector.py
                                        │
                                        ▼
                                   检测变更（新增表/列/注释）
                                        │
                                        ▼
                                   schema_chunk_builder.py
                                   （构造 SchemaChunk）
                                        │
                                        ▼
                                   BGE-M3 embedding（32条/批）
                                        │
                                        ▼
                                   PGVector upsert（按 table_name 去重）
```

增量更新：只对变更的表重新 embedding + upsert。每日凌晨全量同步兜底。

---

## 六、质量检测

### 6.1 核心原则

检测不阻塞，标注不隐藏。质量检测在结果返回之后运行，把问题标注在响应中。用户看到"结果 + 质量标注"，而非"结果被拦截"。

### 6.2 四类检测

| 检测 | 触发条件 | 标注内容 | 严重度 |
|------|---------|---------|--------|
| **空结果** | `row_count == 0` | "返回0行。可能原因：过滤条件过严、时间范围无数据、表名选错（当前使用: orders 表）" | warning |
| **NULL 异常** | 某列 NULL 占比 > 50% | "列 `remark` NULL 占比 78%，聚合计算已自动排除 NULL 值" | warning |
| **极端值** | 数值列 `max > P99 × 10` | "列 `amount` 存在极端值（最大 ¥999,999 vs 99分位 ¥8,470），平均值可能失真" | warning |
| **数据延迟** | 副本延迟 > 5s | "数据存在约 8 秒延迟，截止 14:32:18，不包含此后数据" | info |

### 6.3 QualityReport

```python
class QualityIssue:
    type: str          # "empty_result" | "null_anomaly" | "outlier" | "stale_data"
    severity: str      # "info" | "warning"
    column: str | None
    detail: str        # 面向用户的中文描述

class QualityReport:
    issues: list[QualityIssue]
    issue_count: int
    confidence: float  # 1.0 - (issue_count × 0.2)，最低 0.0
    data_snapshot_at: str
    replica_lag_ms: int
```

---

## 七、代码结构

### 7.1 需实现的文件

```
src/spma/
├── agents/sql/
│   ├── graph.py          ← Agent 循环 StateGraph（生成引擎）
│   ├── guard.py          ← 五层 SQL 校验
│   ├── executor.py       ← 只读副本执行器 + 连接池
│   ├── generator.py      ← LLM SQL 生成（注入元数据 + error feedback）
│   ├── schema_rag.py     ← PGVector 语义检索 + 精确表名命中
│   ├── verifier.py       ← 确定性收敛 + LLM 语义验证
│   ├── quality.py        ← QualityReport 生成
│   ├── state.py          ← SQLAgentState（含确认闸门字段）
│   ├── prompts.py        ← LLM Prompt 模板
│   ├── confirmation.py   ← 确认闸门规则引擎
│   └── convergence.py    ← 收敛判断逻辑（确定性 + LLM 兜底）
│
├── ingestion/
│   ├── sql_pipeline.py   ← Schema 摄入管道主流程
│   ├── schema/
│   │   ├── introspector.py   ← information_schema 读取
│   │   ├── chunk_builder.py  ← SchemaChunk 构造
│   │   └── embedder.py       ← BGE-M3 批量 embedding + PGVector 写入
│   └── scheduler.py      ← 新增 10min Schema 轮询 job
│
├── models/
│   └── worker_output.py  ← 新增 SQLWorkerOutput 字段
│
├── api/routes/
│   └── query.py          ← 新增 /sql/query + /sql/query/confirm + /sql/schema
│
└── infrastructure/
    ├── state_store.py    ← confirmation_token → Redis 映射
    └── cache.py          ← 内存 Schema 快照缓存
```

### 7.2 测试结构

```
tests/
├── unit/sql_agent/
│   ├── test_guard.py           ← SQL Guard 各层 20+ case
│   ├── test_convergence.py     ← 收敛判断（确定性 + LLM 兜底）
│   ├── test_schema_rag.py      ← Recall@10 + 精确表名命中
│   ├── test_quality.py         ← 质量检测
│   └── test_confirmation.py    ← 确认闸门规则
│
├── integration/
│   └── test_sql_agent_loop.py  ← MockLLM 下三种收敛模式
│
├── e2e/
│   └── test_sql_e2e.py         ← 真实 LLM + 50 条 eval dataset
│
└── eval/
    └── sql_eval_dataset.json   ← 50 条 golden SQL 测试数据
```

### 7.3 与 PRD 任务映射

| PRD 任务 | 对应文件 |
|---------|---------|
| T1.1 基础设施 | 外部依赖（PGVector/BGE-M3/Qwen/Claude API），非代码任务 |
| T1.2 Schema 摄入 | `ingestion/sql_pipeline.py` + `ingestion/schema/*.py` |
| T1.3 SQL Guard | `agents/sql/guard.py` |
| T1.4 Agent 核心循环 | `agents/sql/graph.py` + `generator.py` + `verifier.py` + `convergence.py` + `schema_rag.py` |
| T1.5 确认闸门 | `agents/sql/confirmation.py` + `api/routes/query.py`（确认端点） |
| T1.6 质量检测 | `agents/sql/quality.py` |
| T1.7 WorkerOutput | `models/worker_output.py` |
| T1.8 测试 | `tests/unit/sql_agent/` + `tests/e2e/` + `tests/eval/` |

---

## 八、垂直切片实施顺序

按方案 B（垂直切片），实施顺序如下：

| 切片 | 目标 | 产出 |
|------|------|------|
| **Slice 1** (第 1 周) | 端到端跑通 | Mock Schema + SQLite + Claude Sonnet → 完整 generate→guard→execute→verify 循环，能回答 5 个简单查询 |
| **Slice 2** (第 2 周) | 真实 Schema RAG | 替换 Mock → PGVector + BGE-M3 语义检索；替换 SQLite → PostgreSQL 只读副本 |
| **Slice 3** (第 3 周) | 语义验证 + 确认闸门 | LLM 语义验证上线；高风险查询确认闸门 + 双步 API |
| **Slice 4** (第 3-4 周) | 质量检测 + 测试 | QualityReport 生成；单元测试 + E2E 测试 |
| **Slice 5** (第 4 周) | Schema 摄入管道 | 10min 定时轮询上线；WorkerOutput 补齐 |

---

## 附录：与 PRD 的差异点

| PRD 原文 | 本次设计 | 理由 |
|---------|---------|------|
| `src/agents/sql_agent/` 目录 | `src/spma/agents/sql/` | 沿用现有代码结构 |
| embedding 文本为 DDL | embedding 文本为业务描述 | 避免共享列名导致向量相似度虚高 |
| Qwen3-8B 作为"分级使用"备选 | 仅 Claude 不可用时切换 | 降低路由复杂度，Phase 1 优先简单 |
| Streamlit 作为正式 UI | Streamlit 降为开发 demo | API-first 架构，正式 UI 留给 Phase 3+ |
| 确认闸门在 Agent 循环内 | 确认闸门通过双步 API + Redis 暂停-恢复 | 适配纯 API 模式 |
