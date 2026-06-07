# Design: SPMA 5独立Agent架构

> 所属项目：[SPMA 全局概览](SPMA-design-00-global-overview.md)
> 状态：DESIGN COMPLETE（CEO Review + Eng Review 双通过）
> 前置阅读：[Supervisor Agent](SPMA-design-01-supervisor-agent.md)、[Doc Worker](SPMA-design-02-doc-worker.md)、[Code Worker](SPMA-design-03-code-worker.md)、[SQL Worker](SPMA-design-04-sql-worker.md)

---

## 一、架构概述

在 Supervisor-Worker Pipeline 架构之上，将 5 个组件升级为独立 Agent——每个 Agent 内部具备多轮自主推理能力，通过收敛契约控制循环次数。

### Agent 类型

| Agent | 类型 | 循环模型 |
|-------|------|---------|
| **Supervisor Agent** | 编排 Agent | 分发 → 收集 → 质量评估 → 不够 → 调整参数重分发 |
| **Doc Agent** | 检索 Agent | BM25+向量混合检索 → 完备度判断 → 不够 → 线索扩展重搜 |
| **Code Agent** | 检索 Agent | ripgrep → 完备度判断 → 不够 → 调用链展开重搜 |
| **SQL Agent** | 执行 Agent | Schema RAG → LLM生成SQL → Guard → 执行 → 语义验证 → 不够 → 重生成 |
| **Synthesis Agent** | 审计 Agent | RRF融合 → LLM生成初稿 → 引用完整性检查 → 不够 → 修正 |

### 系统架构图

```
                                    ┌─────────────────────────┐
                                    │     API Gateway / LB     │
                                    └───────────┬─────────────┘
                                                │
                        ┌───────────────────────▼───────────────────────┐
                        │              Supervisor Agent                  │
                        │  ┌─────────────────────────────────────────┐  │
                        │  │ 多轮编排循环 (≤5轮, 超时5s含Worker等待)   │  │
                        │  │  Round 1: LLM分类+抽取 → Send API并行派发 │  │
                        │  │  Round 2+: 收集Worker结果 → 质量评估      │  │
                        │  │            → 评分≥0.6 收敛               │  │
                        │  │            → 评分<0.6 + 重调度<2次 → 重派 │  │
                        │  └─────────────────────────────────────────┘  │
                        └───┬───────────────┬───────────────┬─────────┘
                            │ Send API      │ Send API      │ Send API
              ┌─────────────▼┐  ┌───────────▼┐  ┌─────────▼──────────┐
              │  Doc Agent   │  │ Code Agent │  │    SQL Agent       │
              │  (≤3轮,2s)   │  │ (≤3轮,2s)  │  │    (≤5轮,3s)       │
              │  ┌─────────┐ │  │ ┌────────┐ │  │  ┌──────────────┐  │
              │  │BM25+向量│ │  │ │ripgrep │ │  │  │Schema RAG    │  │
              │  │混合检索  │ │  │ │+AST    │ │  │  │→ LLM SQL生成 │  │
              │  └────┬────┘ │  │ └───┬────┘ │  │  └──────┬───────┘  │
              │  ┌────▼────┐ │  │ ┌───▼────┐ │  │  ┌──────▼───────┐  │
              │  │完备度判断│ │  │ │完备度  │ │  │  │SQL Guard     │  │
              │  │不够→    │ │  │ │不够→   │ │  │  │→ 只读执行    │  │
              │  │线索重搜  │ │  │ │调用链  │ │  │  │→ 语义验证    │  │
              │  │够了→返回│ │  │ │展开重搜 │ │  │  │→ 不够→重生成 │  │
              │  └─────────┘ │  │ │够了→返回│ │  │  └──────────────┘  │
              └──────┬──────┘  │  └────────┘ │  └────────┬───────────┘
                     │         └──────┬──────┘           │
                     └────────────────┼──────────────────┘
                                      │ fan-in 收集
                        ┌─────────────▼─────────────┐
                        │     Synthesis Agent       │
                        │  (≤2轮, 2s)               │
                        │  ┌──────────────────────┐ │
                        │  │ RRF融合 + LLM生成初稿  │ │
                        │  └──────────┬───────────┘ │
                        │  ┌──────────▼───────────┐ │
                        │  │ 自检: 引用完整性      │ │
                        │  │      跨源一致性       │ │
                        │  │      问题覆盖度       │ │
                        │  └──────────────────────┘ │
                        └─────────────┬─────────────┘
                                      │
                        ┌─────────────▼─────────────┐
                        │       降级 & 容灾层        │
                        │  整体查询硬上限 10s          │
                        │  Agent超时→部分结果+标注     │
                        │  Redis不可用→降级pipeline   │
                        └───────────────────────────┘
```

---

## 二、收敛契约

这是整个架构的基石——定义了每个 Agent 何时停止循环。

| Agent | 类型 | 最大轮数 | 收敛条件 | 超时(含执行) | 超时策略 |
|-------|------|---------|---------|-------------|---------|
| **Supervisor** | 编排 | ≤5 | 所有Worker评分≥0.6 OR 重调度2次无改善 | 5s | 取最佳结果+标注"结果可能不完整" |
| **Doc Agent** | 检索 | ≤3 | 结果≥5条 AND (req_ids精确匹配 OR LLM判断"信息充足") | 2s | 返回当前Top-K+标注 |
| **Code Agent** | 检索 | ≤3 | 结果≥3条 AND (调用链深度≤2层 OR 第3轮无新增文件) | 2s | 返回当前结果+标注 |
| **SQL Agent** | 执行 | ≤5 | SQL执行成功 AND 行数∈[1,10000] AND 通过语义验证 | 3s | 返回最后成功执行的SQL结果 |
| **Synthesis** | 审计 | ≤2 | 引用覆盖率≥80% claim AND 无跨源矛盾 | 2s | 返回初稿+标注 |

**整体硬上限：10s** → 所有Agent强制停止 → 返回部分结果 + "⏱️ 查询超时，以下结果可能不完整"。

### 收敛条件设计原则

**确定性收敛优先（代码规则）→ LLM判断兜底（仅在确定性条件不满足时触发）：**

- Doc Agent：`结果≥5 AND req_ids命中` → 自动收敛（不调LLM）
- Code Agent：`结果≥3 AND 第3轮无新增文件` → 自动收敛
- SQL Agent：`执行成功 AND 行数正常` → 自动收敛
- 确定性条件不满足 → 调Haiku判断是否充足（~300ms, ~$0.001/次）

### 时序推演

**最坏情况（跨源三Worker并行，满轮）：**

```
T=0.0s   Supervisor: LLM分类+抽取(500ms) → Send API并行派发
T=0.5s   Doc/Code/SQL Agent并行执行
         ├─ Doc Agent:  3轮 × ~700ms/轮 → per-agent超时2s强制停在2.0s
         ├─ Code Agent: 3轮 × ~600ms/轮 → 1.8s自然完成
         └─ SQL Agent:  5轮 × ~600ms/轮 → per-agent超时3s强制停在3.0s
T=3.5s   Supervisor收集 → 质量评估(200ms) → 质量不够(<0.6)
T=3.7s   Supervisor Round 2: 调整参数 → 重新Send API派发
T=5.9s   第二轮评估 → round≥2 不再重调度 → 强制收敛
T=6.0s   Synthesis: ≤2轮 × 1s/轮 = 2s
T=8.0s   返回最终回答 (< 10s硬上限 ✓)
```

**典型情况（大多数查询1-2轮收敛，P50 < 5s）：**

```
T=0.0s   Supervisor分类+抽取(500ms) → 并行派发
T=3.0s   Worker返回(SQL最慢3s)，质量≥0.6 → 收敛
T=3.2s   Synthesis 1轮生成+1轮自检 = 1s
T=4.2s   返回回答 (P50 < 5s ✓)
```

---

## 三、Supervisor 质量函数

Supervisor 的收敛依赖 Worker 评分——这是编排循环的核心决策逻辑。

### 质量评分流程

```
                      ┌──────────────────────────────┐
                      │ 收集所有 Worker 返回结果       │
                      └──────────────┬───────────────┘
                                     │
                                     ▼
                      ┌──────────────────────────────┐
                      │ 对每个 Worker 输出做三维评分    │
                      │                              │
                      │ 维度1: 结果数量 (0-0.3)        │
                      │   0条→0.0  <3条→0.1          │
                      │   <10条→0.2  ≥10条→0.3       │
                      │                              │
                      │ 维度2: Worker自评置信度(0-0.3) │
                      │   output.confidence × 0.3     │
                      │                              │
                      │ 维度3: 精确匹配命中 (0-0.4)    │
                      │   命中req_ids/table_names/    │
                      │   code_refs → 0.4             │
                      │   否则 → 0.0                  │
                      └──────────────┬───────────────┘
                                     │
                                     ▼
                      ┌──────────────────────────────┐
                      │ 按 query_type 查权重矩阵       │
                      │ 加权求和 → 每个Worker的最终分  │
                      └──────────────┬───────────────┘
                                     │
                                     ▼
                      ┌──────────────────────────────┐
                      │ 所有Worker评分 ≥ 0.6?          │
                      └──────┬───────────┬───────────┘
                             │ YES       │ NO
                             ▼           ▼
                      ┌──────────┐ ┌─────────────────┐
                      │ 收敛 ✓    │ │ 重调度次数 < 2?   │
                      │ → Synth  │ └────┬────────┬───┘
                      └──────────┘      │ YES    │ NO
                                        ▼        ▼
                                   ┌────────┐ ┌──────────┐
                                   │调整参数 │ │强制收敛   │
                                   │重新派发 │ │→ Synth   │
                                   └────────┘ └──────────┘
```

### 三维评分细则

**维度1 — 结果数量（0-0.3 分）：** 纯计数打分。0 条结果 → 0.0 分；不足 3 条 → 0.1 分；不足 10 条 → 0.2 分；10 条及以上 → 满分 0.3。过少的结果量意味着检索覆盖不足。

**维度2 — Worker 自评置信度（0-0.3 分）：** Worker 返回的 `confidence` 字段线性映射——Worker 对自己的检索结果有多大把握。这个值是 Worker 内部完备度判断的输出（确定性收敛 → confidence≥0.85；LLM 判断充足 → 0.6-0.85；LLM 判断不足但被 max_rounds 截断 → <0.6）。

**维度3 — 精确匹配命中（0-0.4 分）：** 这是权重最高的维度，也是区分"模糊语义搜索"和"确定性精确检索"的关键信号。如果 Worker 命中了精确实体（Doc Agent 命中 req_ids 精确过滤、SQL Agent 命中 table_names 精确 DDL 查询、Code Agent 命中 code_refs 精确 grep），表明走的是确定性检索路径，质量天然高于纯语义搜索。

### query_type 权重矩阵

维度权重不是固定的——不同的查询类型对三个维度的倚重不同：

| query_type | count 权重 | confidence 权重 | exact_match 权重 | 设计原理 |
|-----------|-----------|----------------|-----------------|---------|
| **data_query** | 0.3 | 0.3 | 0.4 | 数据查询最看重表名精确匹配——选对表比返回多少行更重要 |
| **search** | 0.4 | 0.4 | 0.2 | 搜索类查询重视召回量和 Worker 自评——用户期望"尽量多找" |
| **trace** | 0.2 | 0.3 | 0.5 | 溯源查询（"这个需求影响了哪些代码"）以 req_ids 精确匹配为核心——需求 ID 是最强锚点 |

### 重调度决策逻辑

当任一 Worker 评分 < 0.6 且当前编排轮次 < 2（即尚未重调度过 2 次）时，触发重调度。所有 Worker ≥ 0.6 或已达 2 轮重调度 → 不再重调度，强制收敛。

### 参数调整策略（重调度时）

从成功的 Worker 结果中提取上下文实体（req_ids、table_names、module），注入失败 Worker 的检索参数中：
- **调整检索 query：** 将其他 Worker 找到的实体追加到失败 Worker 的原始 query 后，形成扩展检索词
- **调整检索范围：** 扩展/收缩时间窗口、放宽 doc_type 过滤条件
- **不调整：** Worker 类型选择——不新增或移除 Worker

---

## 四、Agent 交互协议

### Agent 间交互：DAG（有向无环图）

```
Supervisor ──Send API──▶ Doc Agent    ──┐
           ──Send API──▶ Code Agent   ──┼── fan-in ▶ Supervisor(收集)
           ──Send API──▶ SQL Agent    ──┘       │
                                                ▼
                                         Synthesis Agent
```

- Agent 间**不互调**。网状调用（Doc 发现线索→主动调 Code）推迟到 Phase 5+。
- Worker 内部各自循环，Supervisor 通过 Send API 并行派发、fan-in 收集。
- 每个 Worker Agent 作为独立的 LangGraph 子图。

### Worker 输出格式

所有 Worker Agent 返回给 Supervisor 的输出遵循统一的字段规范：

| 字段 | 类型 | 说明 |
|------|------|------|
| `worker_type` | `"doc"` / `"code"` / `"sql"` | 标识来源 Agent 类型 |
| `result_count` | int | 返回结果数量 |
| `results` | list[dict] | 检索/SQL 结果列表 |
| `citations` | list[Citation] | 每条结果的引用元数据 |
| `confidence` | float (0-1) | Worker 自评信心 |
| `has_exact_match` | bool | 是否命中精确匹配实体（req_ids / table_names / code_refs） |
| `rounds_used` | int | 内部消耗的检索轮数 |
| `original_query` | str | 原始检索 query |

**Citation 字段规范：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `source_type` | `"prd"` / `"code"` / `"sql"` | 来源数据源类型 |
| `source_id` | str | 来源标识：doc_id、`file_path:line` 或 `table.column` |
| `snippet` | str | 引用原文片段，≤200 字符 |

各 Agent 可在标准字段基础上追加 Agent 特有字段（如 SQL Agent 追加 `execution_sql` 供用户复制复现）。

### Synthesis Agent 输出格式

| 字段 | 类型 | 说明 |
|------|------|------|
| `answer` | str | 最终回答（Markdown 格式） |
| `citations_verified` | int | 已验证的引用数量 |
| `citations_unverified` | int | 无法验证的引用数量 |
| `contradictions` | list[str] | 跨源矛盾列表（如 "Doc 说 A，Code 说 B"） |
| `coverage_gaps` | list[str] | 用户问题中未被回答的部分 |
| `audit_trail` | str | 自检过程简述 |

---

## 五、状态管理

### Agent 状态数据模型

每个 Agent 在每一轮循环中维护以下状态字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `round` | int | 当前轮次编号（从 1 开始） |
| `query` | str | 本轮检索 query（可能经历改写） |
| `action` | str | 本轮执行的检索动作（`"bm25_vector_search"` / `"ripgrep"` / `"schema_rag"` 等） |
| `results` | list[dict] | 本轮检索结果摘要，每条含 `{id, source, snippet, score}` |
| `assessment` | str | 完备度判断结论（`"sufficient"` 或 `"insufficient: missing X"`） |
| `confidence` | float (0-1) | 本轮自评信心 |
| `llm_calls` | int | 本轮 LLM 调用次数 |
| `latency_ms` | int | 本轮耗时（毫秒） |

### 存储层级

| 层级 | 技术 | Phase | 语义 |
|------|------|-------|------|
| **进程内存** | Python dict | Phase 1 | SQL Agent 单进程内 5 轮循环。无外部依赖 |
| **Redis 热状态** | Redis, TTL=5min | Phase 2+ | Write-through，每次状态变更同步写入。Key: `agent:{user_id}:{session_id}:{query_id}:{agent_type}:state` |
| **Postgres 冷 trace** | PostgreSQL | Phase 3+ | Write-back（查询结束后异步写入），不阻塞 Agent 循环 |

### 降级路径

```
Redis可用 ──→ Agent多轮循环（正常）
Redis不可用 ──→ Agent降级为单轮pipeline模式（退化为当前非Agentic行为）
              → logger.warning("Redis unavailable, falling back to single-pass mode")
```

### Checkpointer 隔离

每个 Agent 子图使用独立 LangGraph checkpointer namespace，避免并发冲突。Supervisor 通过 Send API 派发任务时，在子图的 config 参数中注入 namespace，格式为 `{query_id}:{agent_type}`（如 `uuid-xxx:doc`、`uuid-xxx:code`、`uuid-xxx:sql`）。LangGraph Checkpointer 自动将不同 namespace 的状态写入独立的存储 key，同一 query 下不同 Agent 的子图状态互不干扰。

---

## 六、Agent 基础设施

采用"状态共享 + 循环独立"的抽象层次：

### 共享状态基类

所有 Agent 共享一个基础状态模型，包含 `round`（当前轮次）、`confidence`（自评信心）、`results`（结果列表）、`token_used`（已消耗 token）、`assessment_history`（完备度判断历史）五个字段。每个 Agent 在此基础上追加自己特有的字段（如 Code Agent 追加 `call_depth` 和 `new_files_this_round`）。

### 共享基础设施方法

三个跨 Agent 通用方法，通过 mixin 提供给每个 Agent：

| 方法 | 功能 | 调用时机 |
|------|------|---------|
| `check_convergence(state)` | 检查当前 Agent 是否满足收敛条件（确定性优先，LLM 兜底） | 每轮结束后 |
| `consume_budget(tokens)` | 从 Token 预算中扣减，返回是否有剩余额度 | 每次 LLM 调用前 |
| `save_checkpoint(state)` | 将 Agent 状态写入 LangGraph Checkpointer | 每轮状态变更后（由 LangGraph 自动管理） |

### Agent 循环图构建模式

每个 Agent 作为独立的 LangGraph StateGraph 构建，遵循统一的 **"搜索 → 评估 → 条件分支"** 模式：

```
                     ┌──────────────────────┐
                     │    search 节点        │
                     │  执行本轮检索动作       │
                     │  (bm25+向量/ripgrep/   │
                     │   schema_rag/...)     │
                     └───────────┬──────────┘
                                 │
                                 ▼
                     ┌──────────────────────┐
                     │   assess 节点         │
                     │  完备度判断            │
                     │  (确定性优先 → LLM兜底)│
                     └───────────┬──────────┘
                                 │
                    ┌────────────┼────────────┐
                    │ 不满足      │            │ 满足
                    ▼             │            ▼
           ┌──────────────┐      │     ┌──────────┐
           │ expand/retry  │      │     │   END    │
           │ (线索扩展/     │      │     │ 返回结果  │
           │  重生成/...)  │      │     └──────────┘
           └──────┬───────┘      │
                  │              │
                  └──────────────┘
                  回到 search 节点
```

**以 Doc Agent 为例的具体构建：**
- **search 节点** → 执行 BM25 + 向量混合检索，每轮可能使用不同的扩展线索
- **assess 节点** → 完备度判断：`结果≥5 AND req_ids命中` → 自动收敛；否则 Haiku 判断是否充足
- **条件边** → 不满足收敛条件时携带新线索回到 search（最多 3 轮）；满足则 END

**以 SQL Agent 为例的具体构建：**
- **generate 节点** → LLM 根据 Schema RAG 结果 + 业务元数据生成 SQL
- **guard 节点** → SQLGlot 语法校验 + DDL/DML 拦截 + 表/列存在性验证
- **条件边** → guard 失败则带错误信息回到 generate（最多 5 轮）
- **execute 节点** → 只读副本执行 SQL
- **verify 节点** → 语义验证：`执行成功 AND 行数正常` → 自动收敛；否则 Haiku 语义验证
- **条件边** → verify 不通过则带异常信息回到 generate；通过则 END

---

## 七、安全保障

### Agent Action Guard

每个 Agent 可调用的工具受白名单限制——这是一个纯确定性检查，不依赖 LLM。每次 Agent 尝试调用工具时，系统检查该 Agent 类型的白名单，不在白名单内的操作被拦截并记录 `BLOCKED` 日志：

| Agent | 允许的工具 |
|-------|-----------|
| **Doc Agent** | `bm25_search`, `vector_search`, `metadata_filter`, `completeness_check`, `expand_clues`, `return_results` |
| **Code Agent** | `ripgrep`, `read_file`, `glob`, `ast_expand`, `completeness_check`, `return_results` |
| **SQL Agent** | `schema_rag`, `generate_sql`, `validate_sql`, `execute_readonly`, `verify_results`, `semantic_verify`, `return_results` |
| **Supervisor** | `classify_intent`, `extract_entities`, `rewrite_query`, `send_to_worker`, `collect_results`, `evaluate_quality`, `reschedule`, `finalize` |
| **Synthesis** | `rrf_fusion`, `llm_generate`, `citation_check`, `cross_source_check`, `return_results` |

### LLM 并发与退避

LLM API 调用使用指数退避重试策略（tenacity 库）：

```
LLM 调用
    │
    ▼
┌─────────────────────────────────────┐
│ 尝试调用 Haiku/Sonnet API            │
└──────────────┬──────────────────────┘
               │
    ┌──────────┼──────────┐
    │ 成功     │ 429      │ 其他异常
    ▼          ▼          ▼
  返回结果  自动重试   降级到本地 Qwen3-8B
            (最多3次,      (非限流错误不重试)
             指数退避:
             multiplier=0.5,
             max_wait=2s)
```

- **退避参数：** multiplier=0.5s, max_wait=2s, 最多 3 次重试
- **触发条件：** 仅 `RateLimitError`（429）触发重试；非限流错误直接降级到本地模型
- **429 不计入 Agent 轮次计数** — 这是基础设施问题，不是搜索质量问题

---

## 八、Token 预算管理

| 查询类型 | 预算（LLM调用次数） |
|---------|-------------------|
| 单源简单 | 8 次 |
| 单源复杂 | 12 次 |
| 跨源 | 20 次 |
| 三源全查 | 25 次 |

- 完备度判断使用 **Haiku**（~$0.001/次），最终生成使用 **Sonnet**（~$0.005/次）
- Phase 1-2 使用单 Agent token 硬上限（硬截断）
- Phase 3（Supervisor 上线时）引入跨 Agent 预算分配

---

## 九、成本模型

| 场景 | 非Agent | Agent P50 | Agent P99 | 成本倍率 |
|------|---------|----------|----------|---------|
| 单源 SQL 查询 | 3次 (~$0.015) | 5次 (~$0.025) | 8次 (~$0.04) | ~2x |
| 单源 Doc 查询 | 2次 (~$0.01) | 4次 (~$0.015) | 6次 (~$0.02) | ~1.5x |
| 跨源查询 | 5次 (~$0.025) | 10次 (~$0.04) | 20次 (~$0.07) | ~2.5x |
| 三源全查 | 5次 (~$0.025) | 12次 (~$0.05) | 25次 (~$0.09) | ~3x |

---

## 十、延迟 SLO

| 查询类型 | P50 | P95 | P99 |
|---------|-----|-----|-----|
| 单源查询 | < 3s | < 6s | < 8s |
| 跨源查询 | < 6s | < 12s | < 15s |
| 整体硬上限 | — | — | 10s（强制中断）|

---

## 十一、可观测性

### Agent 专用指标

| 指标 | 告警阈值 |
|------|---------|
| `agent_rounds_p99` | p99 > max_rounds → 调整收敛参数 |
| `agent_false_confidence_rate` | > 15% → 完备度判断质量下降 |
| `agent_early_stop_rate` | > 30% → 收敛条件过严或搜索质量下降 |
| `agent_degradation_rate` | > 10% → 基础设施问题（Redis/LLM） |
| `agent_loop_efficiency` (第N轮新增 / 第N-1轮) | < 0.3 → 边际收益递减，考虑收紧max_rounds |

### Agent Dashboard

- 循环次数分布（histogram per agent）
- 虚假信心率趋势（line chart）
- Token 成本热力图（by query_type）
- 降级/早停率（stacked area）
- 边际效率（bar chart，per round）

---

## 十二、回滚机制

每个 Agent 有独立 feature flag，可秒级回退到 pipeline 模式：

```yaml
agents:
  sql_agentic: false       # false=当前3轮自修复, true=agentic语义验证
  doc_agentic: false       # false=BM25+向量单次, true=agentic完备度判断
  code_agentic: false      # false=ripgrep+AST单次, true=agentic多轮
  supervisor_agentic: false # false=单次分类+规则, true=多轮编排
  synth_agentic: false     # false=LLM一次生成, true=自检循环
```

**回滚触发：** 虚假信心率 > 15% OR P99 延迟恶化 > 30% OR Token 成本恶化 > 50%。

---

## 十三、技术选型

| 组件 | 选型 | 理由 |
|------|------|------|
| Agent 框架 | **LangGraph** | StateGraph + Send API + Checkpointer，最大社区 |
| LLM（完备度判断）| **Claude Haiku** | ~300ms, ~$0.001/次 |
| LLM（生成/分类）| **Claude Sonnet** | 高质量回答 |
| LLM（本地降级）| **Qwen3-8B (vLLM)** | API 不可用时的兜底 |
| 热状态存储 | **Redis**, TTL=5min | Write-through, Phase 2+ |
| 冷 trace 存储 | **PostgreSQL** | Write-back, Phase 3+ |
| 向量数据库 | **PGVector** | 已有设计，不变 |
| 重试/退避 | **tenacity** | exponential backoff |

---

## 十四、渐进式路线

| Phase | 引入 | 基础设施 | Gating |
|-------|------|---------|--------|
| **Phase 0** | **收敛判断 Spike** — 100条 query 验证 LLM 完备度判断精确率 | 标注工具 | 精确率 ≥ 80% 通关 |
| **Phase 1** | SQL Worker Agent | 进程内存状态 + 单 Agent token 上限 | Spike 通过 |
| **Phase 2** | Doc Agent + Synthesis Agent | Redis 热状态 | SQL Agent 稳定 |
| **Phase 3** | Supervisor Agent + Code Agent | Postgres 冷 trace + Agent Dashboard | Doc/Synth Agent 稳定 |
| **Phase 4** | 完整降级 L0-L4 + 熔断器 + K8s + 混沌工程 | — | 5 Agent 全部稳定 |
| **Phase 5+** | Cognitive Layer（主动感知 + 跨源推演 + 用户记忆）| 事件总线 | 降级体系完整 |

---

## 十五、前置验证：收敛判断 Spike

这是整个架构的 gating item——LLM 能否可靠判断"找够了"？

### 方法

1. 收集 100 条真实用户问题（从 Shadowing + 测试查询）
2. 为每条标注"正确的完备文档/代码/SQL 集合"（人工）
3. 对每条 query 跑 Agent 循环（确定性条件 + LLM 判断）
4. 计算：LLM 说"够了"时，真实 Recall 是否 ≥ 80%

### 通关标准

- **精确率 ≥ 80%**：LLM 说"够了"时，至少 80% 概率真的够了
- 召回率不做硬性要求（宁可多搜一轮也不少搜）

### Plan B（Spike 未通过）

放弃 LLM 完备度判断，改用**纯确定性收敛 + 轮次上限**。Agent 在 max_rounds 内反复搜索直到命中确定性条件。仍保留"多轮搜索"的检索增强价值，但去掉"自主判断"能力。

---

## 十六、测试策略

### 测试层次

| 层次 | 工具 | 覆盖 |
|------|------|------|
| **Unit** | pytest + MockLLM | 确定性收敛条件、Action Guard、Token 预算 |
| **Integration** | pytest-asyncio + LangGraph test | Send API 并行编排、Checkpointer 隔离、Agent 完整循环 |
| **E2E** | pytest + 真实 LLM | Agent Eval Dataset（50 条 × golden results） |
| **Chaos** | fault injection | Redis 不可用、LLM 超时、10s 硬上限、Token 预算耗尽 |

### LLM Mock 策略

Agent 循环测试使用 MockLLM 替代真实 LLM 调用。MockLLM 按预先编排的响应序列逐轮返回结果——每轮返回一个 JSON 对象，控制该轮的完备度判断结论（`sufficient`/`insufficient`）、缺失信息描述（`missing`）和置信度（`confidence`）。

测试覆盖三种典型收敛模式：

| 收敛模式 | MockLLM 行为 | 断言 |
|---------|-------------|------|
| **第 1 轮收敛** | 首轮返回 `sufficient=True, confidence=0.9` | `rounds_used == 1` — 验证单轮快速收敛路径 |
| **第 3 轮收敛** | 前两轮返回 `sufficient=False`，第三轮返回 `sufficient=True` | `rounds_used == 3` — 验证多轮扩展检索后收敛 |
| **永不收敛（强制停止）** | 每轮返回 `sufficient=False` | `rounds_used == max_rounds` — 验证达到上限后强制收敛并返回当前最佳结果 |

通过 pytest 参数化（`pytest.mark.parametrize`）对以上三种场景注入不同的 MockLLM fixture，断言 Agent 的实际收敛轮次。

### Agent Eval Dataset

```
tests/eval/agent_eval_dataset.json:

{
  "queries": [
    {
      "query": "用户登录模块的PRD改了哪些内容",
      "query_type": "cross_source",
      "golden_docs": ["doc_001:chunk_3", "doc_001:chunk_5"],
      "golden_code": ["src/auth/oauth.py", "src/auth/login.py"],
      "golden_sql_tables": ["users", "user_sessions"]
    }
  ]
}
```

评估脚本：每次 Agent 变更后自动跑 → 检测虚假信心率、收敛率、Recall@10 变化。

---

## 十七、关键风险

| 风险 | 等级 | 缓解 |
|------|------|------|
| 收敛判断 LLM 精确率不达标 | 🔴 最高 | Phase 0 Spike gating + Plan B |
| Agent 延迟不可预测 | 🟡 | 收敛契约 + 10s 硬上限 + P50 目标保障 |
| Token 成本爆炸 | 🟡 | 分级模型 + Token 上限 + 完备度判断用 Haiku |
| 虚假信心（Agent 说够了但不够）| 🟡 | Agent Eval Dataset + 虚假信心率指标 |
| 5 Agent 调试复杂度 | 🟡 | Agent Dashboard + 完整 trace + per-Agent 日志 |

---

## 十八、NOT in Scope

- **Cognitive Layer**（Phase 5+）：主动感知、跨源推演、用户记忆
- **Agent 间网状互调**（选项 B）：Doc 发现线索→主动调 Code
- **知识图谱混合方案**：维护成本极高
- **代码嵌入摘要层**：Phase 3 验证 BGE-M3 代码检索效果后决定

---

## 十九、错误与降级速查表

| 错误 | 处置 | 用户感知 |
|------|------|---------|
| `MaxRoundsExceeded` | 返回当前最佳结果+透明标注 | "搜索未穷尽，结果可能不完整" |
| `LLMTimeoutError` | 降级为 pipeline 模式 | 无感知（自动切换） |
| `LLMResponseMalformed` | 重试1次→规则兜底 | 无感知 |
| `LLMRefusalError` | 跳过该轮，标记收敛 | 该 Agent 结果为空 |
| `TokenBudgetExhausted` | 强制收敛，取当前最佳结果 | "为控制成本，搜索未完全穷尽" |
| `AgentStateError`（Redis不可用）| 降级为单轮 pipeline | 无感知 |
| `AllWorkersEmptyError` | 返回"未找到相关信息" + 建议 | "未找到相关信息" |
| `ResultCardinalityAnomaly` | 分析原因→调整 SQL→重生成 | 无感知（自动修正） |
| `CitationNotFoundError` | 标注该引用为"未验证" ⚠️ | 引用标记 ⚠️ |
| `CrossSourceContradiction` | 显式标注矛盾 | "⚠️ Doc 说 A，Code 说 B" |
