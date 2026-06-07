# PRD: Phase 3 — Supervisor Agent + Code Agent

> **版本:** v1.0 | **日期:** 2026-06-07 | **状态:** PRD 完成
> **父文档:** [PRD-00 主概述](PRD-00-master-overview.md)
> **前置 Phase:** [Phase 2](PRD-03-phase2-doc-synthesis-agent.md)（Doc/Synthesis Agent 稳定）
> **工期:** +1.5-2 人·月（在 Phase 2 基础上增量）

---

## 一、阶段目标

**交付完整的三源全覆盖 + 跨源溯源能力。** 上线 Supervisor Agent（多轮编排循环）和 Code Agent（ripgrep 代码实时搜索），让开发工程师可以追溯需求→代码→数据库的影响链。同时引入 Postgres 冷 trace 存储和 Agent Dashboard。

### 用户故事

| 优先级 | 用户故事 | 验收标准 |
|--------|---------|---------|
| P0 | 作为开发，当我搜"oauth.py 里 token_refresh 函数的实现"时，系统能精确返回代码 | Code Agent Recall@10 ≥ 0.80 |
| P0 | 作为开发，当我问"REQ-187 改了哪些代码和表"时，三个源并行检索并融合 | 跨源三源查询 P50 < 6s |
| P0 | 作为 PM，当我问模糊问题"用户登录怎么做的"时，系统能自动判断需要查三个源 | 意图分类准确率 ≥ 95% |
| P1 | 作为开发，当我搜"张三上周改的代码"时，系统能通过 git log 定位到变更 | person+time_range 实体驱动检索 |
| P1 | 作为运维，我能看到每个 Agent 的收敛轮次分布和虚假信心率趋势 | Agent Dashboard 正常展示 |
| P2 | 作为用户，多轮对话中问"那它影响了哪些表"时系统能继承上一轮的上下文 | 上下文感知改写 |

---

## 二、输入与依赖

### 2.1 前置依赖

| 依赖项 | 来源 | 状态 | 说明 |
|--------|------|------|------|
| Phase 2 完成 | Phase 2 | ❌ 必须 | Doc Agent + Synthesis Agent 稳定 |
| 代码仓库访问权限 | Git 管理员 | ❌ 需获取 | Clone + Webhook 权限 |
| Elasticsearch 部署 | 内部 | ❌ 需部署 | Phase 3 BM25 专用引擎（替代 PG tsvector） |
| Postgres 冷 trace 表 | PostgreSQL | ❌ 需建表 | Agent 执行 trace 异步写入 |
| Langfuse 部署 | 内部/Cloud | ❌ 需部署 | Agent Dashboard 数据源 |

### 2.2 人员

| 角色 | 人数 | 时间 |
|------|------|------|
| 后端工程师 | 1-2 人 | 1.5-2 月 |

---

## 三、Supervisor Agent 任务拆解

### Task 3.1: 意图分类器（1 周）

**目标：** 实现"LLM 结构化分类 + 规则兜底"两层意图分类。

**子任务：**

| # | 子任务 | 产出 | 工时 |
|---|--------|------|------|
| 3.1.1 | LLM 分类器：Haiku + LangChain `with_structured_output` → `ClassificationResult` Pydantic 模型 | `classifier_llm.py` | 1天 |
| 3.1.2 | 规则兜底层：4 条硬规则（数据查询关键词/需求ID格式/代码引用痕迹/极短模糊查询） | `classifier_rules.py` | 1天 |
| 3.1.3 | 分类评估集构建：100 条标注 query（人工标注 golden sources） | `data/classification_eval.json` | 1天 |
| 3.1.4 | 分类准确率评估 + 分层分析（模糊查询/术语歧义/短查询/跨源漏判） | 评估脚本 + 报告 | 1天 |
| 3.1.5 | 降级路径：Haiku 不可用→Qwen3-8B；全部 LLM 不可用→纯规则分类 | `classifier_fallback.py` | 1天 |

**分类 Prompt 核心结构：**
```
你是一个查询路由器。分析用户问题，输出 JSON。

数据源定义:
- doc: PRD 文档、产品需求
- code: 代码实现、函数、类
- sql: 业务数据、统计查询

分类规则:
- 含需求ID [REQ-XXXXX] → 至少包含 doc
- 含表名/列名/数据/统计 → 至少包含 sql
- 含文件路径/函数名/代码/bug → 至少包含 code
- "X影响Y"、"X对应哪个Z" → 跨源查询
```

**验收：**
- [ ] 分类准确率 ≥ 95%（100 条标注测试集）
- [ ] 规则兜底覆盖 LLM 分类的 4 种已知失败模式
- [ ] 降级路径：Haiku→Qwen3-8B→纯规则 均可用

---

### Task 3.2: 实体抽取 + 查询改写（1 周）

**目标：** 从用户问题中抽取结构化检索参数，桥接用户用语和系统内部名。

**子任务：**

| # | 子任务 | 产出 | 工时 |
|---|--------|------|------|
| 3.2.1 | LLM 实体抽取器（与分类共享同一 Haiku 调用）：12 种实体类型 | `entity_extractor.py` | 1天 |
| 3.2.2 | 实体完备度评估（确定性代码，非 LLM）：3 级评分（rich/partial/bare） | `entity_completeness.py` | 1天 |
| 3.2.3 | 分级处置策略：rich→精确检索, partial→混合检索, bare→语义兜底/反问 | `entity_dispatcher.py` | 1天 |
| 3.2.4 | 查询标准化：同义词映射表（表名/术语/缩写→全称），~100 条冷启动 | `query_normalizer.py` | 1天 |
| 3.2.5 | 查询扩展：Haiku 生成 3-5 个相关搜索词，追加到原始 query（仅当 query ≤ 30 字） | `query_expander.py` | 1天 |
| 3.2.6 | 查询分解：跨源查询时按数据源分解为 2-4 个子查询（Haiku） | `query_decomposer.py` | 0.5天 |
| 3.2.7 | 纯规则兜底抽取器（LLM 不可用时）：正则 + 词典匹配 | `entity_extractor_rules.py` | 0.5天 |

**12 种实体类型（权重表示搜索空间缩减倍数）：**
| 权重 | 实体 | 作用 |
|------|------|------|
| **10** | req_ids | 精确定位到 3-5 个文档片段 |
| **10** | table_names | 跳过 Schema RAG，直接查 DDL |
| **10** | code_refs | grep 精确搜索文件名/函数名 |
| 5 | module | 缩小搜索到功能域 |
| 4 | metrics | 确定聚合方向 |
| 4 | column_names | 精确定位列 |
| 2 | time_range | 元数据过滤 |
| 2 | person | git log --author |
| 2 | version | 分支/tag 过滤 |
| 1 | group_by | 确定分组维度 |
| 1 | doc_types | 文档类型过滤 |

**验收：**
- [ ] 实体 correctness ≥ 90%，completeness ≥ 85%（100 条标注）
- [ ] 确定性完备度评估正确分级（rich/partial/bare 三级）
- [ ] 同义词映射表覆盖常见口语（≥ 80 条）
- [ ] 查询扩展后 Recall@10 不下降

---

### Task 3.3: Supervisor 编排循环（1.5 周）

**目标：** 实现 Supervisor Agent 的多轮编排循环——分类+抽取→Send API 并行派发→质量评估→重调度。

**子任务：**

| # | 子任务 | 产出 | 工时 |
|---|--------|------|------|
| 3.3.1 | Round 1: 分类+抽取+改写 → 构造 WorkerDispatch → LangGraph Send API 并行派发 | `supervisor_round1.py` | 2天 |
| 3.3.2 | Worker 结果收集：LangGraph reducer（`operator.add`）自动合并 WorkerOutput 列表 | `supervisor_collect.py` | 1天 |
| 3.3.3 | 质量评分函数：三维评分（结果数量 0-0.3 + Worker自评 0-0.3 + 精确匹配 0-0.4）× query_type 权重矩阵 | `quality_scorer.py` | 2天 |
| 3.3.4 | 重调度决策：任一 Worker < 0.6 AND round < 2 → 调整参数重派（从成功Worker提取实体注入失败Worker） | `rescheduler.py` | 1天 |
| 3.3.5 | 编排循环实现：LangGraph StateGraph + 条件边（≤5轮, 5s 超时） | `supervisor_graph.py` | 1天 |
| 3.3.6 | Supervisor 超时处理：5s 后强制收敛 → 取最佳结果 + 标注 | `supervisor_timeout.py` | 0.5天 |

**质量评分权重矩阵：**
| query_type | count 权重 | confidence 权重 | exact_match 权重 |
|-----------|-----------|----------------|-----------------|
| data_query | 0.3 | 0.3 | 0.4 |
| search | 0.4 | 0.4 | 0.2 |
| trace | 0.2 | 0.3 | 0.5 |

**验收：**
- [ ] 跨源查询：Supervisor 正确分类+抽取 → 并行派发 3 个 Worker → 收集质量评分
- [ ] 重调度正确触发（Worker 评分 < 0.6 时）
- [ ] 重调度 2 次后强制收敛
- [ ] 5s 超时后正确返回部分结果 + 标注
- [ ] LangGraph Checkpointer namespace 隔离正常

---

## 四、Code Agent 任务拆解

### Task 3.4: 代码摄入管道（1 周）

**目标：** 实现代码仓库的文件路径缓存 + 调用图元数据提取。

**子任务：**

| # | 子任务 | 产出 | 工时 |
|---|--------|------|------|
| 3.4.1 | Git 仓库 clone + Webhook 接收器：push 事件 → git pull | `git_manager.py` | 1天 |
| 3.4.2 | 文件路径缓存构建：`git ls-files` → `file_path_cache` 表（repo_name, file_path, file_type） | `file_path_cache.py` | 1天 |
| 3.4.3 | 仓库注册表：目录→模块名映射（`src/auth/` → 认证模块） | `repo_registry.py` | 0.5天 |
| 3.4.4 | AST 调用图提取：TreeSitter 解析变更文件 → `code_metadata` 表（calls/called_by/imports） | `ast_parser.py` | 1天 |
| 3.4.5 | git log 需求关联提取：commit message 中匹配 REQ-XXXXX 格式 → 关联文件列表 | `gitlog_req_extractor.py` | 0.5天 |
| 3.4.6 | 增量更新调度：Git Webhook → 10s 防抖 → 增量更新 file_path_cache + code_metadata | APScheduler job | 1天 |

**验收：**
- [ ] 文件路径缓存覆盖所有仓库（≥ 50,000 文件）
- [ ] Webhook 触发后 < 5 分钟文件变更可检索
- [ ] AST 调用图正确提取 Python/Java/Go 的函数调用关系

---

### Task 3.5: Code Agent 核心循环（2 周）

**目标：** 实现 Code Agent 的 ripgrep 实时搜索 → 完备度判断 → 调用链展开重搜循环。

**子任务：**

| # | 子任务 | 产出 | 工时 |
|---|--------|------|------|
| 3.5.1 | 搜索词构造管线：实体→搜索词集合（exact_terms/fuzzy_terms/tag_terms），含同义词映射+LLM辅助翻译 | `term_constructor.py` | 2天 |
| 3.5.2 | ripgrep 搜索执行器：按 priority 逐层执行（exact→stem→fuzzy→llm_suggested） | `ripgrep_searcher.py` | 1天 |
| 3.5.3 | 文件路径路由：file_path_cache 快速定位目标仓库（500→5 候选） | `repo_router.py` | 1天 |
| 3.5.4 | AST 调用图扩展：read_file + TreeSitter 解析 → calls/called_by/imports → 发现关联文件 | `ast_expander.py` | 1天 |
| 3.5.5 | Agent 循环编排：ripgrep→完备度判断→(不够→AST扩展→ripgrep) 循环 | `code_agent_graph.py` | 2天 |
| 3.5.6 | 确定性收敛：结果≥3 AND (调用链深度≤2 OR 本轮无新增文件) | 代码规则 | 0.5天 |
| 3.5.7 | LLM 完备度判断（Haiku）：确定性条件不满足时兜底 | `code_completeness.py` | 0.5天 |
| 3.5.8 | 渐进式回退：exact→stem_split→expanded_repos→fuzzy_match→llm_retry 四层回退 | `code_fallback.py` | 1天 |
| 3.5.9 | Code Agent WorkerOutput 实现（含 primary_hits, expanded_files, glob_discoveries） | `code_worker_output.py` | 0.5天 |

**搜索词构造管线：**
```
用户 query + entities
    │
    ▼
┌──────────────────────────────────────┐
│ Phase 0: 文件路径路由                  │
│ file_path_cache 快速定位目标仓库        │
└──────────────────┬───────────────────┘
                   ▼
┌──────────────────────────────────────┐
│ Phase 1: 搜索词构造                    │
│ ├─ code_refs 非空 → exact_terms       │
│ ├─ req_ids 非空 → git log --grep     │
│ └─ module 非空 → 中文→英文翻译         │
│     ├─ 同义词映射表 命中 → exact_terms │
│     └─ LLM 辅助翻译 → exact_terms     │
└──────────────────┬───────────────────┘
                   ▼
┌──────────────────────────────────────┐
│ Phase 2: ripgrep 分层搜索              │
│ exact_terms(weight≥0.8) → stem_split  │
│ → fuzzy(0.4≤w<0.8) → llm_retry(w<0.4)│
└──────────────────────────────────────┘
```

**验收（单元）：**
- [ ] 搜索词构造管线：code_refs → exact_terms；module → 中文翻译 → exact_terms
- [ ] ripgrep 搜索返回正确的文件路径、行号、匹配片段
- [ ] 渐进式回退 4 层均可用

**验收（集成）：**
- [ ] Code Agent Recall@10 ≥ 0.80（50 条 code 查询测试集）
- [ ] code_refs 精确命中时直接返回匹配文件（不走 fuzzy）

---

## 五、基础设施增量任务

### Task 3.6: Postgres 冷 trace 存储（3 天）

**目标：** 查询结束后异步写入 Agent 执行 trace 到 PostgreSQL。

**存储内容：**
- 每个 Agent 的每轮状态快照（round, action, results, assessment, confidence）
- WorkerOutput 完整 JSON
- 最终 SynthesisOutput
- 延迟分解（by agent + by round）
- Token 消耗分解

**写入策略：** Write-back（查询结束后异步写入，不阻塞 Agent 循环）

**验收：**
- [ ] 查询结束后 < 5s 内 trace 写入 Postgres
- [ ] Agent trace 包含每轮的 action/results/assessment/confidence
- [ ] 可通过 query_id 检索完整 trace

---

### Task 3.7: Agent Dashboard（1 周）

**目标：** 基于 Langfuse + Grafana 构建 Agent 专用监控面板。

**面板内容：**

| 面板 | 可视化类型 | 数据来源 |
|------|-----------|---------|
| 循环次数分布 | Histogram per agent | Langfuse |
| 虚假信心率趋势 | Line chart | Langfuse |
| Token 成本热力图 | Heatmap by query_type | Langfuse |
| 降级/早停率 | Stacked area | Langfuse |
| 边际效率（第N轮新增/第N-1轮） | Bar chart per round | Langfuse |
| Supervisor 重调度率 | Gauge | Langfuse |
| 意图分类准确率 | Time series | Langfuse |
| Agent 超时率 | Gauge by agent | Langfuse |
| P50/P95/P99 延迟 | Time series by query_type | OpenTelemetry → Grafana |

**验收：**
- [ ] 所有面板正常渲染
- [ ] 数据刷新延迟 < 1 分钟
- [ ] 告警规则配置完成（虚假信心率>15%, 重调度率>30%, 超时率>5%）

---

### Task 3.8: Elasticsearch 迁移（可选，3 天）

**目标：** 如果 Phase 2 的 PG tsvector BM25 不满足性能或召回要求，迁移到 Elasticsearch。

**迁移内容：**
- ES 集群部署（2 节点）
- IK Analyzer 中文分词插件安装
- 文档 re-index（从 PGVector 到 ES）
- Doc Agent BM25 检索从 PG tsquery 切换到 ES

---

### Task 3.9: 跨 Agent Token 预算管理（2 天）

**目标：** 从 Phase 1-2 的单 Agent Token 硬上限，升级为跨 Agent 预算分配。

| 查询类型 | 预算（LLM调用次数） | 分配策略 |
|---------|-------------------|---------|
| 单源简单 | 8 次 | Supervisor 2 + Worker 4 + Synthesis 2 |
| 单源复杂 | 12 次 | Supervisor 3 + Worker 6 + Synthesis 3 |
| 跨源 | 20 次 | Supervisor 4 + Workers 12 + Synthesis 4 |
| 三源全查 | 25 次 | Supervisor 5 + Workers 15 + Synthesis 5 |

---

### Task 3.10: 测试 + E2E（1 周）

**目标：** 建立完整的三源全链路测试体系。

**子任务：**

| # | 子任务 | 工时 |
|---|--------|------|
| 3.10.1 | Supervisor 分类+抽取评估（100 条标注） | 1天 |
| 3.10.2 | Supervisor 编排循环 MockLLM 测试（单轮收敛/重调度/强制收敛） | 1天 |
| 3.10.3 | Code Agent 循环 MockLLM 测试（3 种收敛模式） | 1天 |
| 3.10.4 | Send API 并行派发集成测试（3 Worker 并行 + fan-in） | 1天 |
| 3.10.5 | E2E 测试：Supervisor→Workers→Synthesis 全链路（20 条跨源 query） | 1天 |
| 3.10.6 | Checkpointer 隔离测试（namespace 不互相干扰） | 0.5天 |

---

## 六、阶段输出与交付物

| 交付物 | 路径 | 格式 |
|--------|------|------|
| Supervisor Agent 完整代码 | `src/agents/supervisor_agent/` | Python |
| Code Agent 完整代码 | `src/agents/code_agent/` | Python |
| 代码摄入管道 | `src/ingestion/code/` | Python |
| Agent Dashboard 配置 | `config/dashboards/` | JSON/YAML |
| 告警规则 | `config/alerts.yaml` | YAML |
| 分类评估集 | `data/classification_eval.json` | JSON |
| E2E 测试 | `tests/e2e/phase3/` | Python |

---

## 七、验收标准

### 7.1 功能验收

- [ ] Supervisor: 意图分类准确率 ≥ 95%
- [ ] Supervisor: 实体 correctness ≥ 90%, completeness ≥ 85%
- [ ] Supervisor: 跨源查询时正确并行派发 3 个 Worker
- [ ] Supervisor: 重调度逻辑正确（评分 < 0.6 → 重派，≥ 2 次 → 强制收敛）
- [ ] Code Agent: Recall@10 ≥ 0.80
- [ ] Code Agent: code_refs 精确命中时直接返回（不走 fuzzy）
- [ ] 三源跨源全链路：Supervisor→Doc+Code+SQL→Synthesis 正常工作
- [ ] 上下文感知改写：多轮对话中继承上一轮实体

### 7.2 性能验收

- [ ] 跨源查询 P50 < 6s, P95 < 12s
- [ ] Supervisor 5s 超时后正确返回部分结果
- [ ] Code Agent 2s 超时后正确返回当前结果
- [ ] Send API 并行派发延迟 < 50ms（框架开销）

### 7.3 可观测性验收

- [ ] Agent Dashboard 所有面板正常展示
- [ ] Postgres 冷 trace 完整记录每个 Agent 的执行轨迹
- [ ] 告警规则配置完成
- [ ] Langfuse Agent 循环追踪正常

---

## 八、风险与缓解

| 风险 | 概率 | 缓解 |
|------|------|------|
| 5 Agent 全链路调试复杂度高 | 高 | Agent Dashboard + 冷 trace + per-Agent 日志；LangSmith 本地调试 |
| Supervisor 重调度次数过多（P99 > 2） | 中 | 收敛条件收紧；降低 Worker 评分阈值；增加确定性收敛路径 |
| Code Agent 中文→英文代码标识符映射质量差 | 中 | 同义词映射表人工种子 + LLM 辅助翻译 + 渐进式回退兜底 |
| 跨源查询延迟超标（P95 > 12s） | 中 | 并行 Worker 执行；10s 硬上限；按 query_type 动态调整 Worker 超时 |
| LangGraph Send API 并发限制 | 低 | LangGraph 原生支持并行派发；配置 Worker 副本数满足并发需求 |
| AST 调用图解析覆盖不足（非主流语言） | 低 | 优先支持 Python/Java/Go；其他语言降级为纯 grep 搜索 |
