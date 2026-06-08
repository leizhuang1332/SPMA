# Design: Phase 2 — Doc Agent + Synthesis Agent

> **日期:** 2026-06-08 | **状态:** DESIGN COMPLETE
> **父文档:** [PRD-03 Phase 2 概述](../../prds/PRD-03-phase2-doc-synthesis-agent.md)
> **权威架构:** [SPMA-design-07 5独立Agent架构](../../docs/designs/SPMA-design-07-agent-architecture.md) — 如有冲突以此为准
> **上游输出:** [SPMA-design-01 Supervisor Agent](../../docs/designs/SPMA-design-01-supervisor-agent.md)

---

## 一、设计决策汇总

| # | 决策点 | PRD 原计划 | 最终设计 | 理由 |
|---|--------|-----------|---------|------|
| 1 | BM25 存储 | PG tsvector + zhparser | **Elasticsearch + ik_smart** | ik_smart 中文分词更成熟，避免 zhparser 部署风险 |
| 2 | 向量存储 | PGVector 存完整 chunk | **PGVector 仅存向量+ID**，ES 为文本权威源 | 消除双写不一致风险，减少 PGVector 存储 |
| 3 | RRF 权重策略 | 等权起步，后续调优 | **直接分层权重** (precise/semantic/hybrid) | Supervisor 实体提取器已就绪，模式选择条件具备 |
| 4 | 完备度判断 | 2 级（确定性 + LLM） | **3 级递进**：确定性→向量阈值(Top-3>0.85)→Haiku 兜底 | 减少 ~60% 查询的 LLM 调用，降低延迟 |
| 5 | HyDE | 同时作用于 BM25+向量 | **仅向量检索** | HyDE 生成文本对 BM25 关键词检索引入噪声 |
| 6 | 线索扩展 | 纯规则扩展 | **规则(R2) → LLM(R3) 阶梯式**，统一完备度判断 | 规则扩展快速补搜，LLM 只在确实需要时介入 |
| 7 | 自检处理 | 未定义 | **分级处理**：pass/fix(修正一次)/contradiction(标注)/gap(标注) | 不同问题需要不同策略，矛盾不是 LLM 能解决的 |
| 8 | 多源支持 | Confluence 单源 | **多源模型**（Confluence/Markdown/Notion/Google Docs） | 提前预留扩展空间，不增加实现复杂度 |
| 9 | source_id 格式 | 未定义 | `{source_type}:{native_id}` | 前缀编码源类型，一次查询即可定位 |

---

## 二、总体架构

```
                          ┌─────────────────────────┐
                          │    Supervisor Agent      │
                          │    (Phase 1 已有)         │
                          └───┬───────┬───────┬─────┘
                              │       │       │
              ┌───────────────▼┐  ┌───▼──┐  ┌─▼──────────────┐
              │   Doc Agent    │  │ Code │  │   SQL Agent    │
              │   (Phase 2)    │  │(Ph3) │  │   (Phase 1)    │
              └───────┬────────┘  └──────┘  └────────────────┘
                      │
        ┌─────────────┼─────────────┐
        │             │             │
  ┌─────▼──────┐ ┌───▼────┐ ┌─────▼──────────┐
  │ ES BM25    │ │PGVector│ │  Redis 热状态   │
  │ ik_smart   │ │BGE-M3  │ │  Write-through  │
  │ 文本权威源  │ │仅向量+ID│ │  TTL=5min      │
  └────────────┘ └────────┘ └────────────────┘
        │             │
        └──────┬──────┘
               │
  ┌────────────▼──────────────────────────────┐
  │         Synthesis Agent (Phase 2)         │
  │  RRF融合 → LLM生成 → 分级自检             │
  └───────────────────────────────────────────┘
```

---

## 三、数据模型

### 3.1 ES 索引（文本权威源）

```
索引: spma_docs

字段:
  chunk_id:     keyword    ← 主键，UUID
  source_id:    keyword    ← "confluence:123456789"
  source_type:  keyword    ← "confluence" | "markdown" | "notion" | "gdoc"
  req_ids:      keyword[]  ← ["REQ-187", "PRD-2025-042"]
  content:      text        ← ik_smart 分词，完整 chunk 文本
  doc_type:     keyword    ← "prd" | "design" | "spec"
  version:      keyword    ← "v2.3"
  updated_at:   date
  chunk_index:  integer    ← chunk 在文档中的序号
  page_title:   text       ← 文档标题
```

### 3.2 PGVector 表（仅向量）

```sql
CREATE TABLE chunk_embeddings (
    chunk_id    UUID PRIMARY KEY,
    source_id   TEXT NOT NULL,           -- 冗余，用于按文档删除
    embedding   vector(1024)             -- BGE-M3 嵌入
);

CREATE INDEX idx_chunk_source ON chunk_embeddings(source_id);
CREATE INDEX idx_chunk_embedding ON chunk_embeddings
    USING hnsw (embedding vector_cosine_ops);
```

### 3.3 ES ↔ PGVector 关联

`chunk_id` 是唯一关联键。PGVector 冗余 `source_id` 以便删除时无需回查 ES。

**检索流程：** ES Top-20 + PGVector Top-20 → RRF 按 chunk_id 融合去重 → 用 chunk_ids 批量 ES mget 取回完整内容 + 元数据。

### 3.4 source_id 与 req_id 关系

| | source_id | req_id |
|------|------|------|
| 含义 | 文档物理标识 | 文档内容中的需求编号 |
| 粒度 | 一个文档 | 文档内的片段 |
| 来源 | 源系统原生 ID | Parser 从正文提取 |
| 关系 | 一个 page 包含多个 req_id | 一个 req_id 可跨多个 page |

不建关联表——req_ids 作为 ES 的 multi-value keyword 字段天然支持多对多检索。

---

## 四、文档摄入管道

### 4.1 流程

```
Confluence Webhook / 全量同步
        │
        ▼
┌──────────────────────────┐
│  Parser 层                │
│  source_type → 对应Parser│
│  conflu: Docling          │
│  md:     Markdown原生     │
│  notion: Notion API       │
│  gdoc:   Google Docs API  │
│  提取: page_id, req_ids   │
└──────────┬───────────────┘
           │
           ▼
┌──────────────────────────┐
│  Semantic Chunker         │
│  500 tokens/块, 50 overlap│
│  分界符: ## → ### → ¶ → 。│
│  统一 chunk_id = uuid     │
└──────────┬───────────────┘
           │
     ┌─────┴─────┐
     │           │
     ▼           ▼
┌────────┐  ┌──────────────┐
│   ES   │  │   PGVector   │
│BM25索引│  │ 仅存向量+ID   │
│完整文本│  │ BGE-M3 嵌入   │
└────────┘  └──────────────┘
     │           │
     └─────┬─────┘
           │
     chunk_id 统一
```

### 4.2 更新与删除

**增量更新（单页修改/删除）：**

```
Webhook 事件
  → 查 ES: source_id → 旧 chunk_ids
  → 并行删除: ES delete_by_query(source_id) + PGVector DELETE WHERE source_id
  → 如果是更新: 重新分块 + 双写
  → 目标: < 5 分钟可检索
```

**全量重建（凌晨兜底）：**

```
凌晨任务:
  → 新建 ES 索引 spma_docs_v2
  → 全量重新分块 + 双写
  → alias 切换: spma_docs → spma_docs_v2
  → 旧索引延迟 1 小时删除
```

**一致性保证：**

| 场景 | 策略 |
|------|------|
| 单页更新 | 删旧写新（并行删除 ES + PGVector） |
| 单页删除 | 只删不写 |
| 全量重建 | 新索引 alias 切换，零窗口期 |
| 双写部分失败 | 重试 3 次；仍失败则该 chunk 标记 `degraded: vector_missing` 或 `degraded: bm25_missing` |
| 去重 | 同一 source_id + content hash 的 chunk 幂等写入 |

---

## 五、Doc Agent 核心循环

### 5.1 LangGraph 节点图

```
                        ┌─────────┐
                        │  START   │
                        └────┬─────┘
                             │
              ┌──────────────▼──────────────┐
              │  route (检索模式选择)         │
              │  req_ids非空 → precise       │
              │  module非空  → hybrid        │
              │  无实体      → semantic      │
              └──────────────┬──────────────┘
                             │
              ┌──────────────▼──────────────┐
              │  search (混合检索)            │
              │  ES BM25 Top-20              │
              │  + PGVector 向量 Top-20       │
              │  [+ HyDE 向量补充检索]         │
              │  → RRF 等权融合 → Top-10     │
              └──────────────┬──────────────┘
                             │
              ┌──────────────▼──────────────┐
              │  aggregate (累计去重)          │
              │  本轮 + 前轮 → chunk_id去重   │
              └──────────────┬──────────────┘
                             │
              ┌──────────────▼──────────────┐
              │  assess (完备度判断)           │
              │  L1: 确定收敛(≥5+req命中)     │
              │  L2: 向量阈值(Top3>0.85)     │
              │  L3: Haiku判断                │
              └──────┬──────────┬────────────┘
                     │          │
            ┌────────┘          └────────┐
            ▼                            ▼
    ┌──────────────┐            ┌────────────────┐
    │   END        │            │ expand (线索扩展)│
    │  返回结果     │            │ R2: 规则扩展     │
    └──────────────┘            │ R3: LLM扩展      │
                                └────────┬───────┘
                                         │
                                         ▼
                                回到 search (max 3轮)
```

### 5.2 收敛契约

| 参数 | 值 |
|------|-----|
| 最大轮数 | ≤3 |
| 超时（含执行） | 2s |
| L1 收敛 | 累计结果 ≥ 5 AND req_ids 命中 → 自动收敛，不调 LLM |
| L2 收敛 | 累计结果 ≥ 5 AND Top-3 向量相似度 > 0.85 → 自动收敛 |
| L3 收敛 | L1/L2 不满足 → Haiku 判断 → 收敛或进入扩展 |
| 超时策略 | 任意节点超时 → 返回当前累计结果 + "⏱️ 部分结果"标注 |

### 5.3 分层权重配置

```yaml
# config/doc_weights.yaml
weights:
  precise:       # req_ids非空 → BM25主导
    bm25: 0.8
    vector: 0.2
  semantic:      # 无有效实体 → 向量主导
    bm25: 0.2
    vector: 0.8
  hybrid:        # module命中 → 等权
    bm25: 0.5
    vector: 0.5

rrf:
  k: 60

hyde:
  max_query_chars: 30
  min_entity_completeness: partial
  parallel: true
  target: vector_only               # HyDE 只作用于向量检索

thresholds:
  vector_similarity_converge: 0.85
  min_results_converge: 5
  max_rounds: 3
  timeout_ms: 2000
```

### 5.4 检索模式选路逻辑

```python
def route(entities: WorkerEntities) -> str:
    """实体驱动的检索模式选择。"""
    if entities.req_ids:
        return "precise"     # BM25主导 + ES term query 精确过滤
    if entities.module:
        return "hybrid"      # 等权混合
    return "semantic"        # 向量主导
```

precise 模式下，ES 侧在 BM25 之外额外加 `terms: {req_ids: [...]}` 过滤，确保 req_id 精确命中。

### 5.5 HyDE 触发与并行策略

**触发条件（三者同时满足）：**
1. 原始 query ≤ 30 字
2. 实体完备度为 partial 或 bare
3. 目标 Worker 是 Doc Agent

**并行策略：** HyDE 生成的同时原始 query 也先跑。原始 query 走 ES BM25 + PGVector，HyDE 生成后只走 PGVector——三路结果 RRF 合并。HyDE 对 Recall@10 的提升需 ≥ 5pp 才正式保留。

### 5.6 线索扩展

| 轮次 | 策略 | 输入 | 延迟 |
|------|------|------|------|
| R2 | 规则扩展 | Top-5 提取: frequency≥2 专有名词 + 新 req_ids + 最高标题词 → OR 拼接 | ~0ms |
| R3 | LLM 扩展 | Haiku 基于累计结果 + 原始 query 生成 2-3 个搜索方向 | ~200ms |

R2→R3 的条件：R2 累计结果经过同一套 3 级完备度判断仍为不足。

### 5.7 状态模型

```python
class DocAgentState(TypedDict, total=False):
    # 输入
    original_query: str
    entities: WorkerEntities
    max_rounds: int              # 默认 3
    timeout_ms: int              # 默认 2000

    # 逐轮
    round: int
    current_query: str
    bm25_candidates: list[dict]       # 本轮 ES Top-20
    vector_candidates: list[dict]     # 本轮 PGVector Top-20
    fused_results: list[dict]         # RRF Top-10
    accumulated_results: list[dict]   # 跨轮去重累计

    # 完备度
    assessment: str
    convergence_reason: str           # "deterministic_req_ids" | "vector_threshold" | "llm_judged_sufficient"
    has_exact_match: bool

    # 输出
    final_results: list[Citation]
    rounds_used: int
    total_latency_ms: int
```

---

## 六、Synthesis Agent

### 6.1 LangGraph 节点图

```
                    ┌─────────┐
                    │  START   │
                    └────┬─────┘
                         │
                         ▼
              ┌──────────────────────┐
              │  fuse (加权RRF融合)    │
              │  多Worker citations   │
              │  chunk_id 去重        │
              │  SQL权重1.2 > Doc 1.0 │
              └──────────┬───────────┘
                         │
                         ▼
              ┌──────────────────────┐
              │  generate (LLM生成)   │
              │  Sonnet → Markdown    │
              │  含引用标注 [类型:ID]  │
              └──────────┬───────────┘
                         │
                         ▼
              ┌──────────────────────┐
              │  audit (分引自检)      │
              │  ① 引用完整性         │
              │  ② 跨源一致性         │
              │  ③ 问题覆盖度         │
              └──────┬───────┬───────┘
                     │       │
        ┌────────────┘       └─────────┐
        ▼                              ▼
  ┌───────────┐                ┌──────────────┐
  │ PASS      │                │ ACTION        │
  │ 输出终稿   │                │ fix: 修正一次  │
  └───────────┘                │ contradiction: │
                               │ 标注通过       │
                               │ gap: 标注通过   │
                               └───────┬──────┘
                                       │
                                  fix: → generate → audit
```

### 6.2 分级自检处理

```
audit JSON → 分类:

引用覆盖率 ≥ 80% AND 无矛盾 AND 无覆盖缺口
  → PASS

引用覆盖率 < 80% AND 无矛盾
  → fix: 带上审计结果让 LLM 修正一次，重新 audit
  → 修正后仍不足 → warn 标注通过

存在跨源矛盾
  → contradiction: 不修正（LLM 解决不了事实矛盾）
  → 终稿中显式标注 "⚠️ 跨源矛盾: [具体冲突]"

存在覆盖缺口
  → gap: 终稿末尾列出 "❓ 以下方面未能回答: [...]"
```

### 6.3 加权 RRF 融合

| Worker 来源 | 默认权重 | 原因 |
|------------|---------|------|
| Doc Agent | 1.0 | 文本检索 |
| SQL Agent | 1.2 | 数据结果精确度更高 |
| Code Agent | 1.0 | Phase 3 启用 |

同一 chunk 出现在多个 Worker 结果中 → 取最高权重 Worker 的排名参与 RRF。

### 6.4 部分 Worker 失败处理

```
1/2 Worker 成功:
  → 使用单源结果生成初稿
  → 标注: "⚠️ [Doc/SQL] Agent 未能返回结果，回答仅基于部分来源"
  → 引用覆盖率不满足 80% 时 warn 标注通过（不强修）
```

### 6.5 透明度标注

| 标注 | 触发条件 |
|------|---------|
| `⏱️ 部分Worker超时` | Worker 2s 内未返回 |
| `⚠️ 仅基于[源类型]结果` | 某个 Worker 失败 |
| `❌ 引用未验证` | 陈述找不到对应引用 |
| `⚡ 跨源矛盾: ...` | Doc/SQL 信息冲突 |
| `❓ 方面未回答: ...` | 问题覆盖缺口 |
| `📊 Token预算耗尽` | token_budget 用完 |

### 6.6 生成与自检 Prompt

**生成 Prompt：**

```
你是一个企业知识助手。根据以下检索结果，回答用户问题。

用户问题: {original_query}

检索结果:
[来自文档] {doc_results}
[来自数据库] {sql_results}

要求:
1. 用 Markdown 格式组织回答
2. 每条陈述必须标注引用来源 [源类型: 标识符]
3. 区分"确定的事实"和"推测的结论"
4. 如果跨源信息存在矛盾，显式标注
5. 如果有未能回答的部分，在末尾列出
```

**自检 Prompt：**

```
你是一个严谨的审计员。检查刚才生成的回答:
{audit_target}

检查项目:
1. 引用完整性: 每条陈述都有引用支撑吗？
2. 跨源一致性: Doc/SQL 的信息有矛盾吗？
3. 覆盖度: 用户原始问题 "{original_query}" 的每个方面都被回答了吗？

输出 JSON:
{
  citation_coverage: 0.xx,
  unverified_claims: [...],
  contradictions: [{claim_a, claim_b, source_a, source_b}],
  coverage_gaps: [...],
  verdict: "pass" | "fix" | "contradiction" | "gap"
}
```

### 6.7 收敛参数

| 参数 | 值 |
|------|-----|
| max_rounds | ≤2 |
| timeout_ms | 2000 |
| 收敛条件 | citation_coverage ≥ 0.8 AND contradictions.length == 0 |
| 超时策略 | 返回初稿 + 标注 |

---

## 七、Redis 热状态与降级

### 7.1 两层存储

```
正常路径:  Redis (主存储, TTL=300s, Write-through)
降级路径:  进程内存 (Python dict, 仅当前查询生命周期)
```

### 7.2 Key 设计

```
agent:{user_id}:{session_id}:{query_id}:{agent_type}:state

存储内容（最小化——不存 chunk 文本）:
{
  "agent_type": "doc",
  "round": 2,
  "accumulated_chunk_ids": ["uuid-abc", ...],
  "convergence_reason": null,
  "state": "searching",
  "started_at": "ISO8601",
  "updated_at": "ISO8601"
}
```

### 7.3 降级与恢复

```
health_check 失败:
  → agent 降级为单轮 pipeline 模式 (max_rounds=1)
  → degradation_level = "L3"
  → logger.warning("Redis unavailable, falling back to single-pass mode")

health_check 恢复:
  → 自动切回 Redis 读写
  → degradation_level = "L0"
  → max_rounds 恢复默认
```

---

## 八、测试与 Eval 体系

### 8.1 测试分层

| 层级 | 内容 | 工具 |
|------|------|------|
| 单元 | RRF 融合、路由、完备度判断、HyDE 触发、Chunker | Fake (内存 ES/PGVector/Redis) + Mock LLM |
| 集成 | 3 种收敛模式完整循环、ES+PGVector 双写一致性、Synthesis 自检 | Testcontainers (ES + PGVector + Redis) + Mock LLM |
| E2E | Doc+SQL→Synthesis 跨源查询 | 真实服务 |
| RAG 评估 | Recall@10, MRR, Faithfulness, HyDE A/B | 50 条标注测试集 + Ragas |

### 8.2 Mock 策略

| Mock 对象 | 实现方式 |
|----------|---------|
| LLM (Haiku/Sonnet) | 预设 `query_hash → response` 映射 |
| ES | Fake 类，内存 dict 模拟 term/match 查询 |
| PGVector | Fake 类，内存 dict + numpy 余弦相似度 |
| Redis | Fake 类，内存 dict 模拟读写 |

### 8.3 质量指标

| 指标 | 目标 |
|------|------|
| Recall@10 | ≥ 0.88 |
| MRR | ≥ 0.80 |
| Faithfulness (Ragas) | ≥ 0.90 |
| HyDE Recall 提升 | ≥ 5pp 才保留 |
| 引用覆盖率 | ≥ 80% |

---

## 九、性能目标

| 指标 | 目标 |
|------|------|
| Doc Agent 单源 P50 | < 3s |
| Doc Agent 单源 P95 | < 6s |
| Doc Agent ≤ 3 轮强制返回 | ≤ 2s 超时 |
| Synthesis Agent ≤ 2 轮强制返回 | ≤ 2s 超时 |
| ES BM25 P99 | < 50ms |
| PGVector P99 | < 100ms |

---

## 十、交付物清单

| 交付物 | 路径 |
|--------|------|
| ES 客户端封装 | `src/spma/retrieval/es_client.py` |
| RRF 融合器 | `src/spma/retrieval/rrf_fusion.py` |
| 混合检索编排 | `src/spma/retrieval/hybrid_search.py` |
| 检索日志 | `src/spma/retrieval/search_logger.py` |
| 文档摄入管道 | `src/spma/ingestion/doc_pipeline.py` |
| Doc Agent 完整实现 | `src/spma/agents/doc/` |
| Synthesis Agent 完整实现 | `src/spma/agents/synthesis/` |
| Redis 状态存储 | `src/spma/infrastructure/state_store.py` |
| 分层权重配置 | `config/doc_weights.yaml` |
| ES 索引 mapping | `config/es_mapping.yaml` |
| 单元测试 | `tests/unit/agents/doc/`, `tests/unit/agents/synthesis/`, `tests/unit/retrieval/` |
| 集成测试 | `tests/integration/test_doc_agent_loop.py`, `tests/integration/test_synthesis_loop.py` |
| E2E 测试 | `tests/e2e/test_cross_source.py` |
| RAG 评估 | `tests/eval/test_doc_rag.py` |
