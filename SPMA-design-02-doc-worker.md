# Design: Doc Agent 设计（PRD 文档检索Agent）

> 所属项目：[SPMA 全局概览](SPMA-design-00-global-overview.md)
> 权威架构：[5独立Agent架构设计](SPMA-design-07-agent-architecture.md) — **如有冲突以此为准**
> 相关模块：[Supervisor Agent](SPMA-design-01-supervisor-agent.md) — 负责通过 Send API 下发检索参数给本 Agent
> 模块职责：作为**检索 Agent**，对 PRD 产品文档执行多轮自主检索循环——BM25+向量混合检索 → 完备度判断 → 不够则线索扩展重搜 → 够了返回结果

---

## 模块在架构中的位置

```
Supervisor Agent
    │
    │ Send API
    ▼
┌─────────────────────────────────────────┐
│           Doc Agent  ← 本文档范围         │
│  (检索Agent, ≤3轮, 2s超时)               │
│  ┌─────────────────────────────────┐    │
│  │ Round 1: BM25 + 向量混合检索      │    │
│  └──────────────┬──────────────────┘    │
│  ┌──────────────▼──────────────────┐    │
│  │ 完备度判断:                       │    │
│  │  结果≥5 AND req_ids命中 → 收敛    │    │
│  │  结果≥5 AND LLM判断充足 → 收敛    │    │
│  │  不够 → 线索扩展重搜              │    │
│  └──────────────┬──────────────────┘    │
│  ┌──────────────▼──────────────────┐    │
│  │ 够了 → 返回结果 + WorkerOutput    │    │
│  └─────────────────────────────────┘    │
└─────────────────────────────────────────┘
    │
    ├── Code Agent
    └── SQL Agent
```

---

## Agent 收敛契约

| 参数 | 值 |
|------|-----|
| **Agent 类型** | 检索 Agent |
| **最大轮数** | ≤3 |
| **收敛条件** | 结果≥5条 AND (req_ids精确匹配 OR LLM判断"信息充足") |
| **超时(含执行)** | 2s |
| **超时策略** | 返回当前Top-K+标注 |
| **确定性收敛** | 结果≥5 AND req_ids命中 → 自动收敛（不调LLM） |
| **LLM 兜底** | 确定性条件不满足 → Haiku判断是否充足（~300ms） |

### Agent 循环图

```python
# Doc Agent 独立构建 LangGraph 子图
doc_graph = StateGraph(DocAgentState)  # 继承 AgentState
doc_graph.add_node("search", bm25_vector_search)
doc_graph.add_node("assess", completeness_check)
doc_graph.add_conditional_edges("assess", should_continue, {
    "retry": "search",    # 不够 → 线索扩展重搜
    "done": END,          # 够了 → 返回
})
```

### Agent 状态数据模型

```python
class DocAgentState(AgentState):
    """Doc Agent 专属状态"""
    round: int                    # 当前检索轮次
    query: str                    # 本轮检索query（可能被改写）
    action: str                   # "bm25_vector_search" | "metadata_filter" | ...
    results: list[dict]           # 本轮检索结果摘要 {id, source, snippet, score}
    assessment: str               # LLM完备度判断 ("sufficient" | "insufficient: missing X")
    confidence: float             # LLM自评信心 0-1
    has_exact_match: bool         # 是否命中精确匹配实体（req_ids）
    llm_calls: int                # 本轮LLM调用次数
    latency_ms: int               # 本轮延迟
```

---

## 一、检索策略

Doc Agent 使用 **BM25 关键词检索 + BGE-M3 语义向量检索** 的混合策略，通过 RRF（Reciprocal Rank Fusion）融合排序。每个检索轮次内，Agent 自主判断结果完备度，不足则扩展线索重新检索。

### 1.1 为什么混合检索

PRD 文档的查询场景有两类：
- **精确查询：** "REQ-187"、"PRD v2.3"——关键词匹配更有效
- **语义查询：** "用户登录怎么做的"、"支付流程的异常处理"——向量检索更有效

单一策略无法同时覆盖。BM25 捕获精确的词汇匹配，BGE-M3 捕获语义相关性，两者互补。

### 1.2 实体驱动的检索模式选择

从 Supervisor 下发的实体中提取检索参数，实体丰富度决定了检索路径：

| 实体可用性 | 检索模式 | 延迟 |
|-----------|---------|------|
| `req_ids` 非空 | **精确模式：** 元数据过滤 `WHERE req_id IN (...)`，跳过语义搜索 | ~10ms |
| `req_ids` 为空，`module` 非空 | **混合模式：** `module` 作为语义搜索锚点 + `time_range`/`doc_types` 元数据过滤 | ~50ms |
| 无有效实体 | **纯语义模式：** 原始 query 直接做向量检索 | ~50ms |

### 1.3 实体用法详表

| 实体 | 用法 | 优先级 | 示例 |
|------|------|-------|------|
| `req_ids` | 精确匹配——元数据过滤 `WHERE req_id IN (...)` | **最高** | `req_id = "REQ-2024-0187"` → 直接返回该需求的所有 PRD 片段 |
| `module` | 语义搜索+关键词——向量检索的 query text | 高 | `"用户登录 需求规格 PRD"` |
| `time_range` | 元数据过滤——限定文档更新时间范围 | 中 | `WHERE updated_at > '2026-05-29'` |
| `version` | 版本过滤——Confluence 版本历史或 Git tag 对应的文档快照 | 中 | `WHERE version = 'v2.3'` |
| `doc_types` | 文档类型过滤 | 低 | `WHERE doc_type IN ('PRD', '技术方案')` |

### 1.4 Agent 循环内的检索行为

在 Agent 循环中，完备度判断（assess 节点）决定是否继续搜索：

```
Round 1: 使用 Supervisor 下发的实体进行首次检索
    ↓
完备度判断:
  ├─ 结果≥5 AND req_ids 命中 → 收敛 ✓（确定性，不调LLM）
  ├─ 结果≥5 AND LLM判断"信息充足" → 收敛 ✓
  └─ 不满足 → Round 2: 线索扩展重搜
                                ↓
                          完备度判断:
                            ├─ 收敛 ✓
                            └─ 不满足 → Round 3: 最后一轮，强制返回
```

**线索扩展策略（Round 2+）：**
- 提取 Round 1 结果中的新关键词（高频术语、相关需求ID、文档标题词）
- 用新关键词扩展原始 query → 重新 BM25+向量检索
- 放宽元数据过滤条件（扩大时间范围、去掉 doc_types 限制）

### 1.5 混合检索权重确定

#### 1.5.1 RRF 的权重机制

标准 RRF 公式 `Σ 1/(k + rank_i(d))` 默认各检索器等权投票。加权变体：

```
weighted_RRF(d) = Σ w_i / (k + rank_i(d))
```

其中 `w_bm25` 和 `w_vector` 是各检索器的权重，`k` 为平滑常数（默认 60）。

**设计决策：Phase 1 不加权。** RRF 对权重不敏感（k=60 提供了良好的平滑），等权起步是最稳健的选择。权重应由线上数据驱动确定，而非在设计阶段拍板。

#### 1.5.2 权重的工程确定流程

```
埋点采集 → 构建标注集 → 离线评估 → A/B 验证 → 上线 & 持续迭代
```

**第一阶段：埋点采集（积累 2–4 周日志）**

每条 query 需记录三层数据：

| 数据层 | 关键字段 | 用途 |
|--------|---------|------|
| 检索快照 | query_text、query_type（precise/semantic/hybrid）、entity 实体信息 | 按 query 类型分桶评估 |
| 候选集 | BM25 Top-20、向量 Top-20、RRF 融合后 Top-10 | 复盘"换权重后 Top-10 会变成什么" |
| 用户反馈 | Supervisor 引用的 chunk 列表、用户赞/踩、复制回答、session 解决/放弃 | 标注真值的信号来源 |

保留 BM25 和向量各自的 Top-20 而不是只保留融合后的 Top-10——如果只存 10 条，换权重后的结果变化就不可复盘了。日志异步写入 Kafka/ClickHouse，不阻塞检索主链路。

**第二阶段：构建标注数据集**

不需要大规模人工标注。核心思路是把 RAG 系统可观测的反馈信号转化为 relevance score：

| 反馈信号 | → relevance | 逻辑 |
|----------|------------|------|
| Supervisor 引用 + 用户赞/复制 | 3（highly relevant） | 检索被 LLM 使用 + 用户确认 |
| Supervisor 引用，用户无负面信号 | 2（relevant） | 检索被 LLM 使用，用户接受 |
| 进入 Top-10 但未被 Supervisor 引用 | 1（marginally relevant） | 检索出来了但 LLM 没选——可能相关但不够好 |
| 进入 Top-10，未被引用，用户重新措辞同一问题 | 0（not relevant） | Top-10 没产出好答案，用户被迫换 query |
| 未进入 Top-10 | 缺失值（不参与评估） | LLM 没看到，无法判断 |

从积累的日志中提取 500+ 条 query 的自动标注，再从其中采样 200 条让 PM/QA 做人工二次确认（用 Label Studio 或简单表单）。两步组合的性价比最高——自动标注提供量、人工确认提供精度。

**第三阶段：离线评估（网格搜索）**

在标注集上用 NDCG@10 评估不同权重组合的效果，网格扫描 `w_bm25 ∈ [0.3, 1.0]`、`w_vector ∈ [0.3, 1.0]`，步长 0.1。

**必须按 query_type 分层评估，不能只看全局均值：**

| query 类型 | 预期最优权重倾向 | 原因 |
|-----------|-----------------|------|
| precise（req_ids 命中） | BM25 主导（0.8–1.0） | 精确 ID 匹配是 BM25 的强项，向量不一定认得短 ID |
| semantic（纯自然语言） | 向量主导（0.7–0.8） | 长自然语言 query 需要语义理解 |
| hybrid（module 命中） | 等权或接近等权 | 既有语义需求又有术语匹配需求 |

**结论：最终上线的是分层权重配置，而非全局固定权重。** 分层依据就是 Supervisor 下发的实体可用性（`req_ids` 非空 → precise、`module` 非空 → hybrid、无有效实体 → semantic）。

**第四阶段：A/B 实验验证**

离线最优 ≠ 线上最优。通过 query_id hash 将用户随机分流（50:50），对照组用当前权重，实验组用新权重，观察至少一周。

| 核心指标 | 含义 | 判定标准 |
|---------|------|---------|
| 答案采纳率 | 用户赞/复制/问完就走的比例 | > 5% 提升才值得上线 |
| Session 解决率 | 用户搜完即离开的比例 | 不能变差 |
| 放弃率 | 问了但没给任何正面反馈的比例 | 不能升高 |
| P95 延迟 | 检索延迟 | 硬约束，劣化 > 10% 则一票否决 |

#### 1.5.3 埋点日志结构详情

每条 query 在 Doc Agent 检索入口处生成一条结构化 JSON 日志，写入 Kafka（topic: `spma.search.logs`）或直接写入 ClickHouse。日志按三层组织：输入快照、候选集快照、用户反馈。

**日志 JSON Schema：**

```json
{
  "$schema": "spma/search_log/1.0",
  
  // ==================== 元数据 ====================
  "log_id": "550e8400-e29b-41d4-a716-446655440000",
  "timestamp": "2026-06-05T10:23:45.123Z",
  "worker_version": "1.2.3",
  "latency_ms": 48,

  // ==================== 输入快照 ====================
  "query": {
    "query_id": "uuid-xxxx",
    "query_text": "用户登录流程的异常处理",
    "query_type": "hybrid",
    "trigger": "supervisor_dispatch"
  },

  // ==================== 实体信息（Supervisor 下发） ====================
  "entity": {
    "req_ids": [],
    "module": "用户登录",
    "time_range": null,
    "doc_types": ["PRD"]
  },

  // ==================== Agent 循环信息 ====================
  "agent_rounds": 2,
  "convergence_reason": "llm_judged_sufficient",

  // ==================== BM25 检索结果 ====================
  "bm25_candidates": [
    {
      "doc_id": "doc_001",
      "chunk_id": 3,
      "rank": 1,
      "score": 12.5,
      "snippet": "## 登录异常处理\n当用户连续3次密码错误…",
      "metadata": {
        "title": "用户登录模块 PRD",
        "version": "v2.3",
        "updated_at": "2026-05-15"
      }
    }
  ],

  // ==================== BGE-M3 向量检索结果 ====================
  "vector_candidates": [
    {
      "doc_id": "doc_005",
      "chunk_id": 1,
      "rank": 1,
      "score": 0.92,
      "snippet": "登录流程的异常分支包括：密码错误、账户锁定…",
      "metadata": {
        "title": "登录模块异常处理方案",
        "version": "v2.3",
        "updated_at": "2026-05-20"
      }
    }
  ],

  // ==================== RRF 融合后最终返回给用户的 Top-10 ====================
  "rrf_fused": [
    {
      "doc_id": "doc_001",
      "chunk_id": 3,
      "rrf_score": 0.032,
      "bm25_rank": 1,
      "vector_rank": 2,
      "snippet": "## 登录异常处理\n当用户连续3次…",
      "metadata": { "title": "用户登录模块 PRD", "version": "v2.3" }
    }
  ],

  // ==================== 用户反馈信号（异步填充，非检索主链路） ====================
  "feedback": {
    "supervisor_cited_chunks": [
      {"doc_id": "doc_011", "chunk_id": 4},
      {"doc_id": "doc_003", "chunk_id": 5}
    ],
    "user_feedback": "thumbs_up",
    "user_copied_answer": true,
    "user_reformulated": false,
    "session_id": "sess-xxxx",
    "session_outcome": "resolved",
    "subsequent_queries": [],
    "time_to_next_query_sec": null
  }
}
```

**字段设计要点：**

| 设计点 | 说明 |
|--------|------|
| BM25/向量各存 Top-20 | 融合后只保留 Top-10 返回给用户，但各检索器的 Top-20 要完整保留。如果只存融合后的 10 条，换了权重后 Top-10 的变化就不可复盘了 |
| `agent_rounds` + `convergence_reason` | Agent 循环的关键可观测数据——几轮收敛的？为什么收敛？用于监控 Agent 质量和调优收敛条件 |
| `feedback` 异步填充 | 检索时只写输入+候选集，用户反馈在 session 结束时回填。避免阻塞检索主链路，同时保证反馈信号完整 |
| `snippet` 保留原文片段 | 离线复盘时可以直接对比两个检索器的结果质量，不需要回源查数据库 |
| `metadata` 精简 | 只带标题、版本、更新时间等关键元数据，不冗余拷贝文档内容。完整内容通过 doc_id+chunk_id 回源获取 |
| `query_type` 三分类 | `precise`（req_ids 命中）、`semantic`（无实体）、`hybrid`（module 命中）。这是后续分层评估的主维度 |

#### 1.5.4 标注数据集构建标准与格式

标注数据集是离线评估的"真值"——用来回答"如果用户搜了 X，哪些文档应该排在前面"。

**标注标准（relevance rubric）：**

| 分数 | 含义 | 判定标准 | 典型场景 |
|------|------|---------|---------|
| 3 | **完全相关**（perfect） | 文档片段直接回答了用户问题，无需其他补充 | 搜"REQ-187 登录错误处理" → 命中 REQ-187 PRD 的错误处理章节 |
| 2 | **高度相关**（relevant） | 文档片段包含关键信息，但可能缺少部分细节 | 搜"支付回滚机制" → 命中了事务处理文档，但没覆盖退款回滚 |
| 1 | **相关但不够**（marginally） | 主题相关但无法直接回答问题，需二次查找 | 搜"登录流程" → 命中了用户权限管理文档（提到了登录但非主题） |
| 0 | **不相关**（irrelevant） | 与查询主题完全无关 | 搜"支付" → 命中了一个只讲登录界面的文档 |
| — | **未曝光**（unjudged） | 文档不在用户可见范围内，无法判断 | 不参与评估计算 |

**自动标注规则（从日志生成）：**

SPMA 的 Doc Agent 是一个被 Supervisor Agent 调用的检索组件——用户看不到原始 chunk 列表，看到的是 Supervisor 用检索结果生成的最终回答。因此不存在"搜索结果页"上的点击和停留行为，自动标注的信号来源必须是 RAG 系统实际可观测的行为。

**可用的反馈信号（按可靠程度排序）：**

| 信号 | 来源 | 可靠程度 | 说明 |
|------|------|---------|------|
| Supervisor 引用的 chunk | Supervisor Agent 在生成回答时，记录引用了哪些 chunk 的哪些片段 | **最高** | 被 LLM 选中写进回答 = 检索出来的内容真正用上了 |
| 用户显式反馈 | 聊天界面上的 👍/👎 按钮 | 高 | 直接表达满意度，但覆盖率低（多数用户不点） |
| 用户复制回答内容 | 前端检测用户选中并复制了回答文本 | 高 | 复制意味着答案有用 |
| Session 结果 | 用户问完就走 vs 立即追问 | 中 | "问完就走"可能是满意，也可能是放弃了；"追问"通常意味首轮检索不够精准 |
| 用户重新措辞同一问题 | 短时间内用不同措辞问同一主题 | 中 | 暗示上一轮检索没把好结果排进上下文窗口 |

**自动标注规则：**

```
规则输入：一条 search_log + Supervisor 返回的 chunk 引用列表 + 用户反馈信号
规则输出：{ query_id, doc_id, chunk_id, relevance }
```

| 规则 | 条件 | → relevance | 依据 |
|------|------|------------|------|
| R1 | chunk 被 Supervisor 在回答中**引用**，且用户给了 👍 或复制了回答 | **3** | 检索结果被 LLM 选中 + 用户明确认可 |
| R2 | chunk 被 Supervisor 在回答中**引用**，用户无负面信号 | **2** | 检索结果被 LLM 选中，用户未反对 |
| R3 | chunk 进入 rrf_fused Top-10 但**未被** Supervisor 引用，且同类 query 中其他 chunk 被引用 | **1** | 被检索出来但 LLM 没选——可能相关但不够好，或者被更好的 chunk 挤掉了 |
| R4 | chunk 进入 rrf_fused Top-10，未被引用，且用户立即**重新措辞同一问题** | **0** | 整个 Top-10 都没产出好答案，用户被迫换 query |
| R5 | chunk 未进入 rrf_fused（靠后或未曝光） | **缺失值**（unjudged） | LLM 没看到，无法判断 |

**标注流程与质量保证：**

```
日志积累（2–4周）
    │
    ├──→ 自动标注（规则引擎）
    │       │
    │       └──→ 约 350 条 query（覆盖高频+长尾）
    │
    └──→ 人工二次确认（采样 150 条）
            │
            ├── 100 条：验证自动标注的准确性（计算 auto vs human 一致率）
            └── 50 条：  覆盖自动标注无法处理的 case（新类型 query、冷门模块）

一致率 ≥ 85% → 自动标注可靠，后续可降低人工抽检比例
一致率 < 85%  → 排查规则问题（引用判定阈值、用户反馈覆盖率），修正后重新标注
```

**与离线评估的衔接：**

标注集存储为独立 JSON 文件或数据库表，评估脚本直接加载。标注集和日志是**两套独立的数据**——日志是原始信号，标注集是经过校验的真值。评估脚本从标注集读取 query 和 relevance label，从日志中读取对应的 BM25/向量排名，计算不同权重组合下的 NDCG@10。

```
标注集（真值）  ─┐
                 ├──→ 评估脚本 ──→ NDCG@10 per 权重组合
日志（排名快照） ─┘
```

#### 1.5.5 离线评估与权重调整

有了标注集之后，权重调整的本质是：**对每一组候选权重 (w_bm25, w_vector)，用标注集模拟"如果当时用这个权重，用户会看到什么结果"，然后算 NDCG@10，挑最高的。**

**第一步：重放排名**

对于标注集中的每一条 query，日志里已经存了 BM25 和向量各自的 Top-20 排名。评估脚本不需要重新跑检索——直接读日志，代入新的权重重新计算 RRF 分数，重新排序：

```
对每条 query：
  1. 从日志读取 BM25 Top-20（含 rank）和向量 Top-20（含 rank）
  2. 对每组候选权重 (w_bm25, w_vector)：
     a. 对每个候选 chunk 计算 weighted_rrf = w_bm25/(60+bm25_rank) + w_vector/(60+vector_rank)
        （未进入某个检索器 Top-20 的 chunk，该检索器 rank 视为 100）
     b. 按 weighted_rrf 降序排列，取 Top-10
     c. 对照标注集，计算这次排序的 NDCG@10
  3. 记录该权重组合在当前 query 上的 NDCG@10
```

**第二步：按 query_type 分层评估**

不对所有 query 算一个全局平均 NDCG，而是按 query_type 分组计算：

```
输出示例（NDCG@10，每组最高分加粗）：

weight (bm25, vec)   precise(n=120)   semantic(n=200)   hybrid(n=180)   全局(n=500)
─────────────────────────────────────────────────────────────────────────────────
(0.5, 0.5)           0.72             0.68              0.74             0.71
(0.3, 0.7)           0.58             0.76              0.71             0.69
(0.7, 0.3)           0.81             0.61              0.70             0.70
(0.8, 0.2)           0.85             0.55              0.67             0.68
(0.2, 0.8)           0.52             0.78              0.69             0.67
...
```

**结论：分层权重优于全局固定权重。** 全局最优 0.5:0.5 对 hybrid 最好，但对 precise 和 semantic 分别有 13% 和 8% 的提升空间。

**第三步：产出分层权重配置**

```yaml
# config/doc_agent_weights.yaml
weights:
  precise:       # req_ids 非空
    bm25: 0.8
    vector: 0.2
  semantic:      # 无有效实体
    bm25: 0.2
    vector: 0.8
  hybrid:        # module 命中
    bm25: 0.5
    vector: 0.5
```

Doc Agent 运行时根据 Supervisor 下发的实体自动选择对应权重组：

```
if req_ids 非空 → precise
elif module 非空 → hybrid
else → semantic
```

#### 1.5.6 持续迭代机制

- **稳定期：** 每月自动跑一次离线评估，NDCG 偏离当前线上权重最优点超过 3% 时触发新一轮 A/B
- **重大变更时（新 embedding 模型、分块策略调整、大批量文档入库）：** 立即重跑离线评估
- **自动化：** 周级定时任务跑网格搜索并输出差异报告，不自动切换——权重切换应是有意识的人工决策
- **最简看板：** 按 query_type 聚合"Supervisor 引用的 chunk 主要来自 BM25 还是向量"。如果某类型下 80% 的引用都来自向量，BM25 权重可能偏高了

> **Phase 规划：** Phase 1 使用 RRF 等权融合，配合埋点积累数据；Phase 2 基于日志分析结果引入分层权重；Phase 3 可选引入 Cross-encoder Reranker（如 BGE-Reranker-v2）对 RRF Top-20 精排。

---

## 二、Agent Action Guard

Doc Agent 可调用的工具受白名单限制：

```python
ALLOWED_ACTIONS = {
    'doc': ['bm25_search', 'vector_search', 'metadata_filter', 
            'completeness_check', 'expand_clues', 'return_results'],
}
```

---

## 三、反事实分析：去掉实体对 Doc Agent 的影响

```
有实体:  req_ids → 元数据精确过滤 (WHERE req_id='REQ-187') ✓ 100% 精准
         module  → 语义搜索 "用户登录 PRD 需求"            ✓ 缩小到功能域
         
无实体:  原始query → 纯语义搜索                              ⚠ 全靠embedding质量
```

**真正损失的场景：需求 ID 精确查询。** "REQ-187" 这种短字符串，BGE-M3 的嵌入向量跟文档分块里的 "REQ-2024-0187" 的语义相似度不保证能排进 Top-K。

> **量化估计：** 去掉实体后，Doc Agent 的 Recall@10 下降约 10-15 个百分点（主要来自 req_ids 精确匹配的损失）。

---

## 四、数据摄入（Doc Agent 视角）

```
PRD 文档 (Confluence/Wiki)
  → Docling/Unstructured 解析
  → 递归语义分块（按段落+标题自然边界切割，目标 ~500 tokens/块，50-token overlap；使用 tiktoken cl100k_base tokenizer）
  → BGE-M3 嵌入 → PGVector
  → 触发方式：Webhook（Confluence 页面更新事件）或定时全量同步（每日凌晨）
```

> 完整的数据摄入管道设计见 [数据摄入管道设计](SPMA-design-05-data-ingestion.md)。

---

## 五、跨源关联

Doc Agent 通过 `req_ids` 实体与 Code Agent 和 SQL Agent 连接：

```
用户问题: "REQ-187 改了哪些代码和表？"
         │
         ├─ Doc Agent: req_id="REQ-187" → 返回 PRD 变更内容
         │
         ├─ Code Agent: req_id="REQ-187" → git log --grep → 找到变更的代码文件
         │
         └─ SQL Agent: req_id="REQ-187" → 在表注释/数据字典中搜索
```

Doc Agent 是跨源溯源的**起点**——需求 ID 是三种数据源之间最强的关联键。

---

## 六、查询改写对 Doc Agent 的特定收益

| 改写方案 | 对 Doc Agent 的收益 | Phase |
|---------|---------------------|-------|
| 标准化 | 解决"登录不了"→"认证失败"的术语映射 | Phase 1 |
| 扩展 | 短 query（如"支付流程"）扩展为含相关术语的搜索词 | Phase 1 |
| HyDE | **收益最大**——PRD 文档长（500 tokens/块）、用户 query 短（平均 ~15 字），HyDE 生成的假设文档 bridge 了这个长度差距 | Phase 2 |
| 分解 | 跨源查询时，为 Doc Agent 生成专注文档维度的检索词 | Phase 2 |

> 查询改写的完整设计见 [Supervisor Agent 设计 - 查询改写](SPMA-design-01-supervisor-agent.md#八查询改写设计round-1)。

---

## 七、Worker 输出格式

Doc Agent 返回给 Supervisor 的输出遵循标准 WorkerOutput 格式：

```python
class WorkerOutput:
    worker_type: str = "doc"
    result_count: int          # 返回的文档片段数
    results: list[dict]        # 文档片段列表
    citations: list[Citation]  # 每条结果的引用元数据
    confidence: float          # Agent自评信心 (0-1)
    has_exact_match: bool      # 是否命中 req_ids 精确匹配
    rounds_used: int           # 使用的检索轮数
    original_query: str        # 原始检索query
```

---

## 八、回滚机制

Doc Agent 有独立 feature flag，可秒级回退到单次 BM25+向量检索模式：

```yaml
agents:
  doc_agentic: false  # false=BM25+向量单次, true=agentic完备度判断+多轮
```

**回滚触发：** 虚假信心率 > 15% OR P99 延迟恶化 > 30% OR Token 成本恶化 > 50%。
