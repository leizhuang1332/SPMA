# PRD: Phase 2 — Doc Agent + Synthesis Agent

> **版本:** v1.0 | **日期:** 2026-06-07 | **状态:** PRD 完成
> **父文档:** [PRD-00 主概述](PRD-00-master-overview.md)
> **前置 Phase:** [Phase 1](PRD-02-phase1-sql-agent.md)（SQL Agent 稳定运行）
> **工期:** +1-1.5 人·月（在 Phase 1 基础上增量）

---

## 一、阶段目标

**增加 PRD 文档检索能力 + 跨源结果融合审计能力。** 交付 Doc Agent（BM25+向量混合检索 + 完备度判断循环）和 Synthesis Agent（RRF 融合 + LLM 生成 + 引用完整性检查），让 PM 可以检索和对比历史 PRD 文档。

### 用户故事

| 优先级 | 用户故事 | 验收标准 |
|--------|---------|---------|
| P0 | 作为 PM，我想搜索"用户登录模块的 PRD 历史版本"，看到相关文档片段 | Doc Recall@10 ≥ 0.88 |
| P0 | 作为 PM，当我搜"REQ-187"时，系统应该精确返回该需求的所有 PRD 片段 | req_ids 精确匹配 100% 命中 |
| P0 | 作为用户，当我同时查文档和 SQL 时，回答应该融合两个来源并标注引用 | 引用覆盖率 ≥ 80% |
| P1 | 作为用户，我看到的回答中每条陈述都应该有来源引用 | 引用标注出现在每条陈述后 |
| P1 | 作为用户，当文档和代码的信息矛盾时应该被告知 | 跨源矛盾显式标注 |
| P2 | 作为 PM，当我用短查询（如"支付流程"）搜索时也能找到相关文档 | HyDE 改写后 Recall 不下降 |

---

## 二、输入与依赖

### 2.1 前置依赖

| 依赖项 | 来源 | 状态 | 说明 |
|--------|------|------|------|
| Phase 1 完成 | Phase 1 | ❌ 必须 | SQL Agent 稳定 + PGVector/BGE-M3/vLLM 基础设施可用 |
| Redis 部署 | 内部 | ❌ 需部署 | Agent 热状态存储（Write-through, TTL=5min） |
| PRD 文档源接入 | Confluence/Wiki | ❌ 需获取 | API 权限 + Webhook 注册 |
| PG tsvector + zhparser | PostgreSQL | ❌ 需安装 | 中文全文 BM25 检索 |

### 2.2 人员

| 角色 | 人数 | 时间 |
|------|------|------|
| 后端工程师 | 1 人 | 1-1.5 月 |
| 算法/NLP 工程师 | 0.5 人 | Doc Agent RAG 调优 |

---

## 三、任务拆解

### Task 2.1: PRD 文档摄入管道（1 周）

**目标：** 实现 PRD 文档从 Confluence/Wiki 到 PGVector 的完整摄入管道。

**子任务：**

| # | 子任务 | 产出 | 工时 |
|---|--------|------|------|
| 2.1.1 | Docling/Unstructured 文档解析器集成：支持 Confluence HTML + Markdown | `doc_parser.py` | 1天 |
| 2.1.2 | 递归语义分块器：按标题层级→段落→句子切分，~500 tokens/块，50-token overlap | `doc_chunker.py` | 1天 |
| 2.1.3 | 元数据提取器：req_id, doc_type, version, updated_at, source_url | `doc_metadata.py` | 0.5天 |
| 2.1.4 | BGE-M3 嵌入 + PGVector 写入管道（复用 Phase 1 的 embedder） | `doc_embedder.py` | 0.5天 |
| 2.1.5 | Confluence Webhook 接收器：页面创建/更新/删除事件 → 增量同步 | `webhook_handler.py` | 1天 |
| 2.1.6 | 每日凌晨全量同步兜底任务（APScheduler） | APScheduler job | 0.5天 |
| 2.1.7 | PG tsvector 索引创建 + zhparser 中文分词配置 | SQL migration | 0.5天 |

**文档分块规格：**
```python
class DocChunkSpec:
    chunk_size_tokens: int = 500       # tiktoken cl100k_base
    overlap_tokens: int = 50
    separators: list[str] = ["\n## ", "\n### ", "\n\n", "\n", "。"]
    min_chunk_size_tokens: int = 100
    preserve_metadata: bool = True     # 保留标题层级、表格结构
```

**验收：**
- [ ] 完整摄入至少一个 Confluence Space（≥ 100 页面）
- [ ] BM25 和向量检索均可命中文档片段
- [ ] Webhook 触发后 < 5 分钟内新页面可检索
- [ ] req_id 元数据过滤正常工作（`WHERE req_id = 'REQ-187'`）

---

### Task 2.2: Doc Agent 核心循环（2 周）

**目标：** 实现 Doc Agent 的 BM25+向量混合检索 → 完备度判断 → 线索扩展重搜的完整循环。

**子任务：**

| # | 子任务 | 产出 | 工时 |
|---|--------|------|------|
| 2.2.1 | BM25 检索器：PG tsvector + tsquery + zhparser，Top-20 返回 | `bm25_search.py` | 1天 |
| 2.2.2 | 向量检索器：BGE-M3 embedding → PGVector HNSW 搜索，Top-20 返回 | `vector_search.py` | 0.5天 |
| 2.2.3 | RRF 融合器：等权 RRF（k=60），BM25 Top-20 + 向量 Top-20 → 融合 Top-10 | `rrf_fusion.py` | 1天 |
| 2.2.4 | 实体驱动检索模式选择：req_ids非空→精确元数据过滤；module非空→混合检索；无实体→纯语义 | `retrieval_router.py` | 1天 |
| 2.2.5 | Agent 循环编排：search→assess→(不够→线索扩展→search) 循环 | `doc_agent_graph.py` | 2天 |
| 2.2.6 | 确定性收敛：结果≥5 AND req_ids 命中 → 自动收敛 | 代码规则 | 0.5天 |
| 2.2.7 | LLM 完备度判断（Haiku）：确定性条件不满足时，判断"信息是否充足" | `completeness_check.py` | 1天 |
| 2.2.8 | 线索扩展策略：提取 Round 1 结果中的高频术语/新 req_ids/标题词 → 扩展 query 重搜 | `clue_expander.py` | 1天 |
| 2.2.9 | 分层权重配置：precise/semantic/hybrid 三种模式的可配置权重 YAML | `doc_weights.yaml` | 0.5天 |
| 2.2.10 | Doc Agent WorkerOutput 实现（含 bm25_top20, vector_top20 快照） | `doc_worker_output.py` | 0.5天 |

**Agent 循环图：**
```
search（BM25+向量混合检索）
    │
    ▼
assess（完备度判断）
    │
    ├─ 不够 → 线索扩展 → 回到 search
    └─ 够了 → 返回结果（END）

max_rounds: 3, timeout: 2s
```

**分层权重配置（Phase 2 等权起步，数据积累后调优）：**
```yaml
weights:
  precise:       # req_ids 非空 → BM25 主导
    bm25: 0.8
    vector: 0.2
  semantic:      # 无有效实体 → 向量主导
    bm25: 0.2
    vector: 0.8
  hybrid:        # module 命中 → 等权
    bm25: 0.5
    vector: 0.5
```

**验收（单元）：**
- [ ] RRF 融合算法单元测试通过（等权 k=60）
- [ ] 检索模式选择逻辑正确：req_ids→precise, module→hybrid, 无实体→semantic
- [ ] MockLLM 下三种收敛模式测试通过

**验收（集成）：**
- [ ] Recall@10 ≥ 0.88（50 条 doc 查询测试集）
- [ ] req_ids 精确匹配 100% 命中（10 条精确 ID 查询测试）

---

### Task 2.3: 检索埋点与日志（3 天）

**目标：** 实现 Doc Agent 的三层结构化日志，为后续权重优化积累数据。

**日志 JSON Schema（存入 PostgreSQL `search_logs` 表）：**
```json
{
  "log_id": "uuid",
  "timestamp": "ISO 8601",
  "query": {"query_id": "...", "query_text": "...", "query_type": "hybrid"},
  "entity": {"req_ids": [], "module": "用户登录", ...},
  "agent_rounds": 2, "convergence_reason": "llm_judged_sufficient",
  "bm25_candidates": [...],    // Top-20
  "vector_candidates": [...],  // Top-20
  "rrf_fused": [...],          // Top-10
  "feedback": {...}            // 异步填充
}
```

**验收：**
- [ ] 每条 Doc Agent 检索生成一条完整日志（含 BM25 Top-20 + 向量 Top-20）
- [ ] 日志异步写入，不阻塞检索主链路
- [ ] `agent_rounds` 和 `convergence_reason` 正确记录

---

### Task 2.4: Doc Agent 的 HyDE 改写（3 天）

**目标：** 对短 query + 无实体的文档查询，用 HyDE（假设文档嵌入）提升召回率。

**触发条件（三者同时满足）：**
1. 原始 query ≤ 30 字
2. 实体完备度为 partial 或 bare
3. 目标 Worker 是 Doc Agent

**并行策略：** HyDE 生成的同时原始 query 检索也先跑，HyDE 作为补充检索。两路检索结果通过 RRF 合并。

```python
HYDE_PROMPT = """
根据用户的问题，写一段假设性的文档内容（200-300字），模拟文档中可能如何描述相关信息。
只输出文档内容，不要标注或解释。

用户问题: {query}

假设的文档内容:
"""
```

**验收：**
- [ ] HyDE 触发条件正确判断（短 query + 无实体）
- [ ] HyDE 生成 + 原始 query 并行检索 → RRF 合并
- [ ] HyDE 开启/关闭的 Recall@10 A/B 对比（≥ 5pp 提升才保留）

---

### Task 2.5: Synthesis Agent（1.5 周）

**目标：** 实现 Synthesis Agent——RRF 融合多 Worker 结果 → LLM 生成初稿 → 引用完整性检查。

**子任务：**

| # | 子任务 | 产出 | 工时 |
|---|--------|------|------|
| 2.5.1 | 加权 RRF 融合：多 Worker 的 citations 合并排序（k=60） | `synth_rrf_fusion.py` | 1天 |
| 2.5.2 | Round 1: LLM 生成初稿 — Sonnet 根据融合结果 + 用户问题生成 Markdown 回答（含引用标注） | `synth_generator.py` | 2天 |
| 2.5.3 | Round 2: 自检 — 引用完整性检查 + 跨源一致性检查 + 问题覆盖度检查 | `synth_auditor.py` | 2天 |
| 2.5.4 | 部分失败处理：1/2 Worker 失败 → 使用部分结果 + 标注缺失维度 | `synth_partial.py` | 1天 |
| 2.5.5 | 透明度标注生成：实体不足/Worker超时/引用未验证/跨源矛盾/Token预算耗尽 | `synth_transparency.py` | 1天 |
| 2.5.6 | SynthesisOutput 实现（含 citations_verified, contradictions, coverage_gaps） | `synth_output.py` | 0.5天 |

**LLM 生成 Prompt 结构：**
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

**自检 Prompt 结构（Round 2）：**
```
你是一个严谨的审计员。检查刚才生成的回答:
{audit_target}

检查项目:
1. 引用完整性: 每条陈述都有引用支撑吗？
2. 跨源一致性: Doc/SQL 的信息有矛盾吗？
3. 覆盖度: 用户原始问题 "{original_query}" 的每个方面都被回答了吗？

输出 JSON: {citation_coverage, unverified_citations, contradictions, coverage_gaps, verdict}
```

**验收：**
- [ ] RRF 融合排序正确（多源 citations 按 RRF 分数降序）
- [ ] LLM 生成的回答包含引用标注（如 `[PRD §3.2]`）
- [ ] 引用覆盖率 ≥ 80%（10 条测试 query）
- [ ] 跨源矛盾被检测并标注
- [ ] 部分 Worker 失败时正确标注缺失维度

---

### Task 2.6: Redis 热状态集成（3 天）

**目标：** 将 Agent 状态从进程内存迁移到 Redis（Write-through），为 Phase 3 多 Agent 编排做准备。

**Key 设计：**
```
agent:{user_id}:{session_id}:{query_id}:{agent_type}:state

示例:
  agent:user-001:sess-abc:uuid-123:doc:state
  agent:user-001:sess-abc:uuid-123:sql:state
  agent:user-001:sess-abc:uuid-123:synthesis:state

TTL: 300（5 分钟）
写入方式: Write-through（每次状态变更同步写入）
```

**降级路径：**
```
Redis 可用 → Agent 多轮循环（正常）
Redis 不可用 → Agent 降级为单轮 pipeline 模式
              → logger.warning("Redis unavailable, falling back to single-pass mode")
```

**验收：**
- [ ] Agent 状态变更后 < 5ms 内写入 Redis
- [ ] Redis 不可用时正确降级到单轮 pipeline 模式
- [ ] Redis 恢复后自动切回多轮循环

---

### Task 2.7: 测试 + Eval（1 周）

**目标：** 建立 Doc Agent + Synthesis Agent 的测试体系。

**子任务：**

| # | 子任务 | 工时 |
|---|--------|------|
| 2.7.1 | Doc Agent RAG 质量评估（Recall@10, MRR, Faithfulness）— Ragas + 50 条标注 | 2天 |
| 2.7.2 | Doc Agent 循环 MockLLM 测试（3 种收敛模式） | 1天 |
| 2.7.3 | Synthesis Agent 自检逻辑测试（引用完整性/跨源一致性/覆盖度） | 1天 |
| 2.7.4 | RRF 融合单元测试（等权 + 加权） | 0.5天 |
| 2.7.5 | E2E 测试：Doc+SQL → Synthesis → 最终回答（10 条跨源 query） | 0.5天 |

**验收：**
- [ ] Doc Agent Recall@10 ≥ 0.88
- [ ] Synthesis 引用覆盖率 ≥ 80%
- [ ] Synthesis 跨源矛盾检测正确

---

## 四、阶段输出与交付物

| 交付物 | 路径 | 格式 |
|--------|------|------|
| PRD 文档摄入管道 | `src/ingestion/doc/` | Python |
| Doc Agent 完整代码 | `src/agents/doc_agent/` | Python |
| Synthesis Agent 完整代码 | `src/agents/synthesis_agent/` | Python |
| 检索日志结构 | `src/agents/doc_agent/search_log.py` | Python |
| 分层权重配置 | `config/doc_weights.yaml` | YAML |
| Redis 状态存储 | `src/infrastructure/state_store.py` | Python |
| 单元测试 | `tests/unit/doc_agent/`, `tests/unit/synthesis_agent/` | Python |
| E2E 测试 | `tests/e2e/phase2/` | Python |

---

## 五、验收标准

### 5.1 功能验收

- [ ] Doc Agent: BM25+向量混合检索 → 完备度判断 → 线索扩展循环正常工作
- [ ] Doc Agent: req_ids 精确命中时走元数据过滤（跳过语义搜索）
- [ ] Doc Agent: Recall@10 ≥ 0.88（50 条标注测试集）
- [ ] Synthesis Agent: RRF 融合 + LLM 生成 + 引用完整性检查
- [ ] Synthesis Agent: 引用覆盖率 ≥ 80%
- [ ] 跨源矛盾被检测并显式标注
- [ ] PRD 文档 Webhook 触发后 < 5 分钟可检索

### 5.2 性能验收

- [ ] Doc Agent 单源查询 P50 < 3s, P95 < 6s
- [ ] Doc Agent ≤ 3 轮内强制返回（≤ 2s 超时）
- [ ] Synthesis Agent ≤ 2 轮内强制返回（≤ 2s 超时）
- [ ] BM25 检索 P99 < 50ms
- [ ] 向量检索 P99 < 100ms

### 5.3 基础设施验收

- [ ] Redis 状态存储正常（TTL=5min, Write-through）
- [ ] Redis 不可用时正确降级到单轮 pipeline
- [ ] 检索日志完整记录（BM25 Top-20 + 向量 Top-20 + RRF Top-10）

---

## 六、风险与缓解

| 风险 | 概率 | 缓解 |
|------|------|------|
| Doc Agent Recall@10 不达标（< 0.80） | 中 | Phase 2 等权 RRF 是最稳健选择；若不达标则提前引入分层权重优化 |
| PG tsvector BM25 中文召回差 | 中 | 评估 zhparser 分词质量；若不达标提前引入 Elasticsearch（原计划 Phase 3） |
| HyDE 生成的假设文档引入噪声 | 中 | 始终保留原始 query 的检索结果并行跑，HyDE 只作为补充信号 |
| 检索日志量过大影响存储 | 低 | 只记录 Top-20 + Top-10 摘要，不全量记录；日志 TTL=30 天自动清理 |
| Redis 内存不足 | 低 | Agent 状态 TTL=5min 自动过期；单 query 状态 < 10KB |
