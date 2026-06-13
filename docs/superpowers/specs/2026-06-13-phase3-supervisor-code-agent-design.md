# Phase 3 实现设计：Supervisor Agent + Code Agent

> **日期:** 2026-06-13 | **状态:** 设计完成
> **来源 PRD:** [PRD-04-phase3-supervisor-code-agent.md](../../prds/PRD-04-phase3-supervisor-code-agent.md)
> **实现策略:** 自底向上（方案 A）—— Code Agent → Code 摄入管道 → Supervisor → 集成测试 → 基础设施

---

## 一、实现策略

按 PRD 任务顺序自底向上交付：

```
Week 1-2: Code Agent 独立开发+测试 (ripgrep循环, AST扩展, 搜索词构造)
Week 3:   Code 摄入管道 (git clone, webhook, file_path_cache)
Week 4-5: Supervisor (分类+抽取+编排循环) 集成 3 Worker
Week 6:   基础设施 (trace存储, Dashboard, token预算)
Week 7:   E2E 测试 + 性能调优
```

**选择理由：** Code Agent 可以独立验证 Recall@10 ≥ 0.80，不依赖 Supervisor；符合 Phase 1→2 的既有开发节奏；1 人即可执行。

---

## 二、Code Agent 设计

### 2.1 Graph 结构

参照 Doc Agent 的 `route→search→aggregate→assess→expand` 循环模式。4 节点 + 条件边：

```
route → search → assess → [expand → search 循环]
```

| 节点 | 职责 | LLM? | 关键输出 |
|------|------|------|---------|
| **route** | file_path_cache 查表 → 确定候选仓库（500→5） | ❌ | candidate_repos[] |
| **search** | 搜索词构造 + ripgrep 分层执行 | 按需(Haiku翻译) | ripgrep_results[], fallback_layer |
| **assess** | L1确定性→L2调用深度→L3 LLM兜底 | 仅L3(Haiku) | assessment, convergence_reason |
| **expand** | read_file + TreeSitter → calls/called_by/imports → 新搜索词 | ❌ | expanded_context[] |
| **should_continue** | assessment==converge OR round≥max → END | ❌ | "expand" \| "END" |

构建函数签名：
```python
def build_code_agent_graph(file_path_cache, ripgrep_executor, ast_parser, llm) -> StateGraph
```

### 2.2 搜索词构造管线

```
Phase 0: 仓库路由
  file_path_cache.query(keyword) → candidate_repos (500→5)

Phase 1: 搜索词构造
  ├─ code_refs 非空 → exact_terms (精确文件/函数名)
  ├─ req_ids 非空 → git log --grep REQ-XXXXX → 关联文件
  └─ module 非空 → 中文→英文翻译
      ├─ 同义词映射表命中 → exact_terms
      └─ LLM 辅助翻译(Haiku) → exact_terms

Phase 2: ripgrep 分层搜索
  fallback_layer=0: ripgrep -w -F "exact_term" --json
  fallback_layer=1: ripgrep -w "stem_split_term" --json
  fallback_layer=2: ripgrep -i "fuzzy_pattern" --json
  fallback_layer=3: Haiku 生成新搜索词 → ripgrep
```

### 2.3 完备度判断——3 级递进

| 级别 | 条件 | 结论 | LLM? |
|------|------|------|------|
| **L1 确定性** | 结果≥3 AND code_refs 精确命中 | 立即收敛 | ❌ |
| **L2 调用深度** | 结果≥3 AND (call_depth≥2 OR 本轮无新增文件) | 收敛 | ❌ |
| **L3 LLM 兜底** | 以上都不满足 | Haiku 判断充足/不足 | ✅ |

### 2.4 收敛条件

```python
def should_converge(state: CodeAgentState) -> bool:
    # 1. 精确命中 → 直接收敛
    if state.get("fallback_layer") == 0 and len(state["ripgrep_results"]) >= 3:
        return True
    # 2. 调用深度达到上限
    if state.get("call_depth", 0) >= 2:
        return True
    # 3. 本轮无新增文件
    if state.get("new_files_this_round", 0) == 0:
        return True
    return False  # 触发 L3 LLM 完备度判断
```

### 2.5 文件清单

在 `src/spma/agents/code/` 下实现：

| 文件 | 职责 |
|------|------|
| `graph.py` | 构建 StateGraph |
| `router.py` | 仓库路由（Phase 0） |
| `term_builder.py` | 搜索词构造（Phase 1） |
| `searcher.py` | ripgrep 分层执行（Phase 2） |
| `completeness.py` | 3 级完备度判断 |
| `ast_expander.py` | AST 调用图扩展 |

---

## 三、Code 摄入管道设计

### 3.1 组件

| 文件 | 职责 | 核心依赖 |
|------|------|---------|
| `git_manager.py` | clone/pull/webhook 接收 | subprocess git |
| `file_path_cache.py` | `git ls-files` → DB 缓存 | PostgreSQL trigram |
| `ast_parser.py` | TreeSitter → calls/called_by/imports | tree-sitter |
| `gitlog_req_extractor.py` | commit message → REQ-XXXXX | git log --grep |

### 3.2 新增数据库表

```sql
-- 文件路径缓存——供 repo_router 快速定位
CREATE TABLE file_path_cache (
    id SERIAL PRIMARY KEY,
    repo_name VARCHAR(255) NOT NULL,
    file_path TEXT NOT NULL,
    file_type VARCHAR(50),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(repo_name, file_path)
);
CREATE INDEX idx_fpc_file_path ON file_path_cache USING gin(file_path gin_trgm_ops);

-- 代码元数据——AST 调用图
CREATE TABLE code_metadata (
    id SERIAL PRIMARY KEY,
    repo_name VARCHAR(255) NOT NULL,
    file_path TEXT NOT NULL,
    function_name VARCHAR(255),
    calls JSONB DEFAULT '[]',
    called_by JSONB DEFAULT '[]',
    imports JSONB DEFAULT '[]',
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(repo_name, file_path, function_name)
);

-- 仓库注册表——目录→模块映射
CREATE TABLE repo_registry (
    id SERIAL PRIMARY KEY,
    repo_name VARCHAR(255) NOT NULL UNIQUE,
    repo_url TEXT NOT NULL,
    local_path TEXT NOT NULL,
    dir_module_map JSONB DEFAULT '{}',
    languages JSONB DEFAULT '[]',
    last_indexed_at TIMESTAMPTZ,
    enabled BOOLEAN DEFAULT true
);
```

### 3.3 Webhook 增量更新流程

```
1. Git push → Webhook POST 到达
2. git_manager.handle_webhook() 解析 payload
3. 10s 防抖（合并同一 push 的多次 webhook）
4. git pull 仓库到最新
5. file_path_cache.incremental_update(changed_files)
6. ast_parser.parse_files(changed_files)
7. gitlog_req_extractor.extract_new_links()
8. 写入 search_logger
```

### 3.4 关键设计选择

- **不用 ES 索引代码** — 代码搜索走 ripgrep 实时查，摄入管道只建文件路径缓存和 AST 元数据
- **Trigram 索引** — `gin_trgm_ops` 做模糊路径匹配，支撑 repo_router 快速定位
- **Webhook 10s 防抖** — 与 `config/ingestion.yaml` 配置一致

---

## 四、Supervisor Agent 设计

### 4.1 Graph 结构

8 节点 + 条件边，使用 LangGraph Send API 并行派发：

```
classify_and_extract → rewrite → dispatch → [doc/code/sql 并行] → score → [reschedule 循环 / converge]
```

| 节点 | 职责 | LLM? | 输出 |
|------|------|------|------|
| **classify_and_extract** | 共享 Haiku 调用：分类+实体 | ✅ Haiku 1次 | classification, entities |
| **rewrite** | 标准化+扩展(≤30字)+分解(跨源) | 按需 Haiku | rewritten_queries |
| **dispatch** | 构造 WorkerDispatch[] → Send() 并行派发 | ❌ | Send(doc/code/sql_worker) |
| **doc/code/sql_worker** | 调用编译好的子图 `.ainvoke()` | 各Agent内部 | worker_outputs (reducer合并) |
| **score** | 三维评分(count+confidence+exact)×query_type权重 | ❌ | quality_scores |
| **reschedule** | 从成功Worker提取实体→注入失败Worker | ❌ | reschedule_count++, hints |

### 4.2 分类+实体抽取 Prompt（核心——共享 Haiku 调用）

```
你是一个企业级查询路由器和分析师。你需要同时完成两项任务：
1. 判断用户问题需要查询哪些数据源
2. 从问题中抽取结构化的检索实体

# === 数据源定义 ===
- doc: PRD文档、产品需求、功能规格、需求变更记录、设计文档
- code: 代码实现、函数、类、方法、文件路径、bug修复、架构实现
- sql: 业务数据查询、统计报表、指标分析、数据量/频率/趋势

# === 分类规则（按优先级）===
1. 含需求ID格式 [REQ-XXXXX] 或 REQ-XXXXX → sources 至少包含 "doc"
2. 含表名(如 users/orders/products)、列名、SQL关键词 → sources 至少包含 "sql"
3. 含统计词(多少/数量/占比/趋势/排行/TOP) → sources 至少包含 "sql"
4. 含文件路径(*.py/*.java/*.go/*.ts)、函数名、类名、代码关键词(bug/异常/报错/实现) → sources 至少包含 "code"
5. 含"影响"/"对应"/"关联"/"改了哪些"/"涉及"等跨域关系词 → is_cross_source=true
6. 极短模糊查询(≤8字)且无明确指向 → sources=["doc","code","sql"], query_type="search"
7. 问"为什么"/"怎么做"/"逻辑" → query_type="explain"

# === 实体抽取规则 ===
- req_ids: 匹配 REQ-\d+ 或 "需求XXX" 格式
- code_refs: 匹配文件路径(*.py/*.java/*.go/*.ts)、函数名(下划线/驼峰)、类名(大写开头)
- table_names: 匹配已知表名列表 {known_tables}，中文表名→英文表名
- module: 匹配功能域: 认证/支付/订单/用户/库存/消息/搜索/报表/管理后台
- time_range: 匹配时间表达式: "上周"/"本月"/"最近X天"/"2024年X月"/"从X到Y"
- person: 匹配人名模式: "XX写的"/"XX改的"/"XX负责"/英文名
- 未找到的字段: 设为 null 或空列表，不要编造

# === 用户问题 ===
{user_query}

# === 对话历史（如有）===
{conversation_history}

# === 输出格式 ===
严格按以下 JSON Schema 输出：
{
  "sources": ["doc", "code", "sql"],
  "is_cross_source": true/false,
  "query_type": "search|data_query|trace|explain",
  "entities": {
    "module": "string|null",
    "req_ids": ["string"],
    "time_range": "string|null",
    "version": "string|null",
    "table_names": ["string"],
    "column_names": ["string"],
    "metrics": ["string"],
    "group_by": "string|null",
    "code_refs": ["string"],
    "person": "string|null",
    "doc_types": ["string"]
  }
}
```

### 4.3 规则兜底（4 条硬规则）

LLM 分类后逐条检查，有遗漏则补刀：
- 含"多少/统计/报表/数据" → 补 sql
- 含 REQ-XXXXX → 补 doc
- 含 .py/.java/.go/.ts 或函数名模式 → 补 code
- 极短模糊查询(<8字) → 三源全查

### 4.4 降级路径

```
Haiku 不可用 → Qwen3-8B → 全部 LLM 不可用 → 纯规则分类+抽取
```

### 4.5 质量评分——三维 × 权重矩阵（确定性代码）

| 维度 | 计算方式 |
|------|---------|
| count (0-0.4) | min(1.0, result_count / 3) × query_type_weight |
| confidence (0-0.4) | Worker 自评 confidence × query_type_weight |
| exact_match (0-0.5) | has_exact_match ? 1 × weight : 0 |

| query_type | count | confidence | exact_match | 阈值 |
|-----------|-------|-----------|-------------|------|
| data_query | 0.3 | 0.3 | 0.4 | 0.6 |
| search | 0.4 | 0.4 | 0.2 | 0.6 |
| trace | 0.2 | 0.3 | 0.5 | 0.6 |

### 4.6 重调度策略

```
score < 0.6 AND reschedule_count < 2 → 从成功Worker提取discovered_entities
  → 注入失败Worker的hints_from_other_workers → 重派

score ≥ 0.6 OR reschedule_count ≥ 2 → 强制收敛 → END
```

### 4.7 其他 Prompt

**查询扩展**（短查询 ≤30 字时触发）：
```
为以下用户查询生成 3-5 个相关的搜索关键词或术语（仅输出关键词列表，用逗号分隔）。
查询: {query}
同义词参考: {synonym_examples}
关键词:
```

**跨源查询分解**（is_cross_source=true 时触发）：
```
将以下复杂查询分解为 2-4 个独立的子查询，每个子查询面向单一数据源。
已抽取实体: {entities}
用户查询: {query}
输出 JSON: [{"query": "子查询", "target": "doc|code|sql"}, ...]
```

**上下文感知改写**（多轮对话时触发）：
```
如果当前消息含指代词或省略了上下文，请重写为自包含的独立查询。
对话历史: {conversation_history}
上一轮实体: {previous_entities}
当前消息: {current_query}
重写后的查询:
```

### 4.8 结构化输出 Pydantic 模型

```python
from pydantic import BaseModel, Field

class EntityOutput(BaseModel):
    module: str | None = None
    req_ids: list[str] = Field(default_factory=list)
    time_range: str | None = None
    version: str | None = None
    table_names: list[str] = Field(default_factory=list)
    column_names: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    group_by: str | None = None
    code_refs: list[str] = Field(default_factory=list)
    person: str | None = None
    doc_types: list[str] = Field(default_factory=list)

class ClassifyAndExtractOutput(BaseModel):
    sources: list[Literal["doc", "code", "sql"]]
    is_cross_source: bool
    query_type: Literal["search", "data_query", "trace", "explain"]
    entities: EntityOutput
```

### 4.9 文件清单

在 `src/spma/agents/supervisor/` 下实现：

| 文件 | 职责 |
|------|------|
| `graph.py` | 构建 StateGraph + Send API 编排 |
| `classifier.py` | LLM 分类+实体抽取（shared Haiku call） |
| `classifier_rules.py` | 4 条硬规则兜底 |
| `classifier_fallback.py` | Haiku→Qwen3-8B→纯规则降级 |
| `query_rewriter.py` | 标准化+扩展+分解+上下文改写 |
| `dispatcher.py` | 构造 WorkerDispatch + Send API 派发 |
| `quality.py` | 三维质量评分（确定性代码） |
| `rescheduler.py` | 重调度决策 + hints 提取 |

---

## 五、集成设计

### 5.1 两级组合（非单一大图）

```
POST /api/v1/query
  ├─ Level 1: Supervisor 图.ainvoke()
  │           内部 Send API 并行调用 Doc/Code/SQL 编译子图
  │           产出 → WorkerOutput[]
  └─ Level 2: Synthesis 图.ainvoke()
              输入 WorkerOutput[] + original_query
              产出 → Final Answer
```

**为什么两级：** Supervisor 和 Synthesis 各自独立可测；单源简单查询可跳过 Synthesis；checkpointer namespace 天然隔离。

### 5.2 Checkpointer Namespace 隔离

```python
configs = {
    "supervisor": {"configurable": {"thread_id": sid, "checkpoint_ns": "supervisor"}},
    "doc_worker":  {"configurable": {"thread_id": sid, "checkpoint_ns": "doc"}},
    "code_worker": {"configurable": {"thread_id": sid, "checkpoint_ns": "code"}},
    "sql_worker":  {"configurable": {"thread_id": sid, "checkpoint_ns": "sql"}},
    "synthesis":   {"configurable": {"thread_id": sid, "checkpoint_ns": "synthesis"}},
}
```

### 5.3 Token 预算分配

| 查询类型 | 总LLM调用上限 | Supervisor | Worker(s) | Synthesis |
|---------|-------------|-----------|-----------|-----------|
| 单源简单 | 8 | 分类+抽取(1) + 重调度(1) | 内部(4) | 生成+审计(2) |
| 单源复杂 | 12 | 分类+抽取(1) + 扩展+重调度(2) | 内部(6) | 生成+审计+重试(3) |
| 跨源 | 20 | 分类+抽取+分解(2) + 重调度(2) | 3 Workers×4=12 | 融合+生成+审计(4) |
| 三源全查 | 25 | 分类+抽取+分解(3) + 重调度(2) | 3 Workers×5=15 | 融合+生成+审计+重试(5) |

### 5.4 错误处理矩阵

| 故障场景 | 检测方式 | 降级行为 | 用户可见 |
|---------|---------|---------|---------|
| Haiku 不可用 | API 超时/429/5xx | Qwen3-8B → 纯规则 | 无感知 |
| 全部 LLM 不可用 | API 超时 | 纯规则分类+抽取 | "AI分类暂时不可用" |
| Doc Agent 超时 | asyncio.wait_for(5s) | 返回已有结果 + L2 | 部分结果标注 |
| Code Agent 超时 | asyncio.wait_for(2s) | 返回已有 ripgrep_results | 部分结果标注 |
| SQL Agent 超时 | asyncio.wait_for(3s) | 返回已有 schema 结果 | 部分结果标注 |
| Worker 全部失败 | 所有 worker_outputs 为空 | 生成反问建议 | "未能找到相关信息" |
| ripgrep 进程崩溃 | CalledProcessError | 降级到 ES 代码索引 | 降级结果标注 |
| git 仓库不可访问 | GitError | 跳过该仓库 | 部分仓库不可用 |

### 5.5 API 端点

| 端点 | 方法 | 功能 |
|------|------|------|
| `/api/v1/query` | POST | 全链路查询（Supervisor→Workers→Synthesis） |
| `/api/v1/query/{query_id}/trace` | GET | 从 Postgres 读取完整 Agent 执行轨迹 |
| `/api/v1/code/search` | POST | Code Agent 独立查询（跳过 Supervisor） |
| `/api/v1/admin/ingest/code` | POST | 触发代码摄入 |

---

## 六、基础设施设计

### 6.1 Postgres 冷 Trace 存储

参照 `SearchLogger` 的 async queue + DB write 模式。查询结束后异步写入，不阻塞 Agent 循环。

```sql
CREATE TABLE agent_traces (
    query_id UUID PRIMARY KEY,
    session_id VARCHAR(255),
    original_query TEXT NOT NULL,
    classification JSONB,
    entities JSONB,
    rewritten_queries JSONB,
    worker_outputs JSONB,
    quality_scores JSONB,
    reschedule_count INT DEFAULT 0,
    final_answer TEXT,
    synthesis_output JSONB,
    total_latency_ms INT,
    total_llm_calls INT,
    total_tokens INT,
    convergence_reason VARCHAR(255),
    degradation_level VARCHAR(10),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE agent_rounds (
    id SERIAL PRIMARY KEY,
    query_id UUID REFERENCES agent_traces(query_id),
    agent_type VARCHAR(50),
    round_num INT,
    action VARCHAR(100),
    results_summary JSONB,
    assessment VARCHAR(50),
    confidence FLOAT,
    latency_ms INT,
    llm_calls INT,
    tokens_used INT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

```python
class AgentTraceLogger:
    # 参照 SearchLogger 模式
    def __init__(self, db_pool, max_queue=500)
    async def start()                    # 启动后台 worker
    async def log_query(query_id, state) # 查询结束后写入
    async def log_round(query_id, agent, round_num, snapshot)
    async def get_trace(query_id) -> AgentTrace
    async def stop()                     # 排空队列后停止
```

### 6.2 Agent Dashboard（Langfuse + Grafana）

| 面板 | 类型 | 数据源 |
|------|------|--------|
| 收敛轮次分布 | Histogram per agent | agent_rounds |
| 虚假信心率趋势 | Line chart | agent_rounds |
| Token 成本热力图 | Heatmap by query_type | agent_traces |
| 降级/早停率 | Stacked area | agent_traces.degradation_level |
| Supervisor 重调度率 | Gauge | agent_traces.reschedule_count |
| 意图分类准确率 | Time series | 人工标注 vs 实际分类 |
| Agent 超时率 | Gauge by agent | agent_rounds |
| P50/P95/P99 延迟 | Time series by query_type | OpenTelemetry → Grafana |

### 6.3 告警规则

| 告警 | 条件 | 级别 |
|------|------|------|
| 虚假信心率过高 | 滚动1h > 15% | 🔥 Critical |
| 重调度率过高 | 滚动1h > 30% | ⚠️ Warning |
| Agent 超时率过高 | 滚动1h > 5% | ⚠️ Warning |
| 分类准确率下降 | 日环比 > 10% | 🔥 Critical |
| P95 延迟超标 | 滚动30min > 12s | ⚠️ Warning |
| Trace 写入队列满 | 队列使用率 > 80% | ℹ️ Info |

---

## 七、测试策略

| 层级 | 内容 | 工时 |
|------|------|------|
| 单元 | Code Agent 搜索词构造、ripgrep执行器、完备度判断 | 2天 |
| 单元 | Supervisor 分类+抽取、查询改写、质量评分 | 2天 |
| 集成 | Code Agent 循环 MockLLM 测试（3种收敛模式） | 1天 |
| 集成 | Supervisor 编排循环 MockLLM 测试（单轮收敛/重调度/强制收敛） | 1天 |
| 集成 | Send API 并行派发（3 Worker 并行 + fan-in） | 1天 |
| E2E | Supervisor→Workers→Synthesis 全链路（20条跨源query） | 1天 |
| 评估 | 分类准确率（100条标注）、实体完备度（100条标注） | 1天 |
| 评估 | Code Agent Recall@10（50条code查询） | 1天 |

---

## 八、文件变更清单

### 新增文件

```
src/spma/agents/code/graph.py
src/spma/agents/code/router.py
src/spma/agents/code/term_builder.py
src/spma/agents/code/searcher.py
src/spma/agents/code/completeness.py
src/spma/agents/code/ast_expander.py

src/spma/agents/supervisor/graph.py
src/spma/agents/supervisor/classifier.py
src/spma/agents/supervisor/classifier_rules.py
src/spma/agents/supervisor/classifier_fallback.py
src/spma/agents/supervisor/query_rewriter.py
src/spma/agents/supervisor/dispatcher.py
src/spma/agents/supervisor/quality.py
src/spma/agents/supervisor/rescheduler.py

src/spma/ingestion/code/git_manager.py
src/spma/ingestion/code/file_path_cache.py
src/spma/ingestion/code/ast_parser.py
src/spma/ingestion/code/gitlog_req_extractor.py

src/spma/observability/trace_logger.py
src/spma/llm/token_budget.py

data/classification_eval.json
data/entity_eval.json
data/synonym_map.json

tests/unit/agents/code/
tests/unit/agents/supervisor/
tests/integration/test_code_agent_loop.py
tests/integration/test_supervisor_loop.py
tests/integration/test_parallel_dispatch.py
tests/e2e/phase3/
tests/eval/test_classification.py
tests/eval/test_code_recall.py
```

### 修改文件

```
src/spma/agents/supervisor/prompts.py  # 替换为生产级 prompt
src/spma/api/routes/query.py           # 添加全链路查询端点
config/alerts.yaml                      # 补充 Phase 3 告警规则
```

---

## 九、验收标准

### 功能
- [ ] Supervisor: 意图分类准确率 ≥ 95%
- [ ] Supervisor: 实体 correctness ≥ 90%, completeness ≥ 85%
- [ ] Supervisor: 重调度逻辑正确（< 0.6 → 重派，≥ 2 次 → 强制收敛）
- [ ] Code Agent: Recall@10 ≥ 0.80
- [ ] Code Agent: code_refs 精确命中时直接返回（不走 fuzzy）
- [ ] 三源跨源全链路：Supervisor→Doc+Code+SQL→Synthesis 正常

### 性能
- [ ] 跨源查询 P50 < 6s, P95 < 12s
- [ ] Supervisor 5s 超时后正确返回部分结果
- [ ] Code Agent 2s 超时后正确返回当前结果

### 可观测性
- [ ] Agent Dashboard 所有面板正常展示
- [ ] Postgres 冷 trace 完整记录每个 Agent 的执行轨迹
- [ ] 告警规则配置完成
