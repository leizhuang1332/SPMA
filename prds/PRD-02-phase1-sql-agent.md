# PRD: Phase 1 — SQL Agent（Text-to-SQL 执行Agent）

> **版本:** v1.0 | **日期:** 2026-06-07 | **状态:** PRD 完成
> **父文档:** [PRD-00 主概述](PRD-00-master-overview.md)
> **前置 Phase:** [Phase 0](PRD-01-phase0-convergence-spike.md)（必须通关）
> **工期:** 1.5-2 人·月 | **优先级:** 🔴 最高（第一个交付价值的 Phase）

---

## 一、阶段目标

**让产品经理和开发工程师能用自然语言查询数据库。** 交付一个完整的 Text-to-SQL Agent，具备 Schema RAG → LLM SQL生成 → SQL Guard 校验 → 只读执行 → 语义验证的完整循环能力。

### 用户故事

| 优先级 | 用户故事 | 验收标准 |
|--------|---------|---------|
| P0 | 作为 PM，我想用自然语言问"过去7天各状态的订单数"，得到正确的数据表格 | Execution Accuracy ≥ 80% |
| P0 | 作为开发，我想知道"users 表有哪些字段"，不需要找 DBA | Schema 查询 100% 准确 |
| P1 | 作为 PM，我查询"上月营收"时系统能提醒我数据可能包含测试数据 | 质量标注出现在结果下方 |
| P1 | 作为运维，我不用担心生成的 SQL 会修改数据 | 100% DDL/DML 操作被拦截 |
| P2 | 作为用户，当我问"各状态订单数"时系统能告诉我 status 的枚举值含义 | 业务元数据注入到回答中 |

---

## 二、输入与依赖

### 2.1 前置依赖

| 依赖项 | 来源 | 状态 | 说明 |
|--------|------|------|------|
| Phase 0 通关 | Phase 0 | ❌ 必须 | 收敛判断精确率 ≥ 80% |
| Claude Haiku API | 外部 | ❌ 需申请 | 语义验证 LLM |
| Claude Sonnet API | 外部 | ❌ 需申请 | SQL 生成 LLM |
| Qwen3-8B + vLLM 部署 | 内部 GPU | ❌ 需部署 | LLM 降级兜底 |
| BGE-M3 + vLLM 部署 | 内部 GPU | ❌ 需部署 | Schema RAG embedding |
| PostgreSQL + PGVector | 内部 | ❌ 需安装 | 向量存储 + Schema 元数据 |
| 数据库只读副本 | DBA | ❌ 需申请 | SQL 执行环境 |
| APScheduler | 内部 | ❌ 需配置 | Schema 定时轮询 |
| Presidio | OSS | ❌ 需部署 | LLM 调用前脱敏 |

### 2.2 数据依赖

| 数据 | 来源 | 获取方式 |
|------|------|---------|
| 数据库 Schema（表名、列名、类型、注释）| `information_schema` | 定时轮询（10min） |
| 列的业务含义/枚举值 | 列注释 + 代码 AST 提取 | 摄入管道 |
| Few-shot 示例 | `pg_stat_statements` Top 100 | 人工 curator 审核 20-30 条 |

### 2.3 人员

| 角色 | 人数 | 时间 |
|------|------|------|
| 后端工程师 | 1 人 | 1.5-2 月 |
| 算法/NLP 工程师 | 0.5 人 | 辅助 RAG 调优 |

---

## 三、任务拆解

### Task 1.1: 基础设施搭建（1 周）

**目标：** 搭建 Phase 1 需要的所有基础设施。

**子任务：**

| # | 子任务 | 产出 | 工时 |
|---|--------|------|------|
| 1.1.1 | PostgreSQL + PGVector 安装，创建 `schema_chunks` 表（HNSW 索引，1024维） | 数据库就绪 | 1天 |
| 1.1.2 | BGE-M3 embedding 服务部署（vLLM，本地 GPU） | embedding API 可用 | 1天 |
| 1.1.3 | Qwen3-8B 降级模型部署（vLLM，本地 GPU） | 降级 LLM 可用 | 1天 |
| 1.1.4 | Claude API 配额申请 + SDK 集成（Haiku + Sonnet） | LLM 调用通路 | 1天 |
| 1.1.5 | APScheduler + PG Queue 摄入调度配置 | 定时任务框架就绪 | 0.5天 |
| 1.1.6 | Presidio 脱敏层集成 | LLM 调用前自动脱敏 | 0.5天 |

**验收：**
- [ ] PGVector 可正常写入和查询（HNSW，1024维向量）
- [ ] BGE-M3 embedding API 返回 1024 维向量，P99 < 100ms
- [ ] Qwen3-8B 可正常生成文本，P99 < 500ms
- [ ] Claude Haiku/Sonnet API 可正常调用
- [ ] Presidio 脱敏规则覆盖手机号、邮箱、IP、金额

---

### Task 1.2: Schema 摄入管道（1 周）

**目标：** 实现数据库 Schema 的自动发现、嵌入和持续更新。

**子任务：**

| # | 子任务 | 产出 | 工时 |
|---|--------|------|------|
| 1.2.1 | Schema 自省脚本：从 `information_schema` 提取表名、列名、类型、注释、外键 | `schema_introspector.py` | 1天 |
| 1.2.2 | 业务元数据提取：列注释解析 + 枚举值映射 + 业务规则文本 | `business_metadata_extractor.py` | 1天 |
| 1.2.3 | Schema Chunk 构造：DDL + 列注释 + 业务元数据 → embedding chunk | `schema_chunk_builder.py` | 1天 |
| 1.2.4 | BGE-M3 嵌入 + PGVector 写入（批量，32条/批） | `schema_embedder.py` | 1天 |
| 1.2.5 | 定时轮询调度：每 10 分钟检查 `information_schema` 变更 → 增量更新 | APScheduler job | 1天 |
| 1.2.6 | 同义词映射表冷启动：从 `information_schema` 提取表名↔注释映射 | `synonym_map_seed.py` | 0.5天 |

**Schema Chunk 数据结构：**
```python
class SchemaEmbeddingChunk(TypedDict):
    table_name: str
    ddl: str                              # 完整 DDL
    columns: list[ColumnMeta]             # 列元数据列表
    foreign_keys: list[ForeignKeyMeta]    # 外键关系
    business_metadata: BusinessMetadata   # 业务元数据
    few_shot_queries: list[FewShotQuery]  # few-shot 示例

class ColumnMeta(TypedDict):
    column_name: str
    data_type: str
    is_nullable: bool
    comment: Optional[str]
    business_meaning: Optional[str]        # "订单状态"
    enum_values: Optional[dict]            # {"pending": "待支付", ...}
```

**验收：**
- [ ] 完整摄入一个测试数据库的所有 Schema（≥ 50 张表）
- [ ] PGVector 中每条表记录可被 BGE-M3 语义搜索命中
- [ ] 定时轮询检测到新增表后 < 10 分钟内可检索
- [ ] 同义词映射表冷启动 ≥ 60 条映射

---

### Task 1.3: SQL Guard 层（5 天）

**目标：** 实现五层 SQL 安全防护——这是非协商项。

**子任务：**

| # | 子任务 | 产出 | 工时 |
|---|--------|------|------|
| 1.3.1 | Layer 1 语法校验：SQLGlot 集成，解析 SQL → 检查 AST 完整性 | `guard_syntax.py` | 1天 |
| 1.3.2 | Layer 2 DDL/DML 拦截：检测 DELETE/UPDATE/DROP/INSERT/TRUNCATE/ALTER/CREATE/GRANT/EXECUTE | `guard_operation.py` | 1天 |
| 1.3.3 | Layer 3 表/列存在性验证：生成的 SQL 中每个表/列必须在 Schema 快照中存在 | `guard_existence.py` | 1天 |
| 1.3.4 | Layer 4 性能保护：检测缺失 WHERE、≥3 JOIN 笛卡尔积风险、全表扫描警告 | `guard_performance.py` | 1天 |
| 1.3.5 | 只读副本执行：连接池 + `statement_timeout=2s` + 结果返回 | `executor.py` | 1天 |

**GuardResult 数据结构：**
```python
class GuardResult(TypedDict):
    passed: bool
    syntax_errors: list[str]
    forbidden_operations: list[str]
    table_existence_errors: list[str]
    performance_warnings: list[str]
    risk_level: str                       # "low" | "medium" | "high" | "blocked"
    requires_user_confirmation: bool
```

**验收：**
- [ ] 语法错误的 SQL 100% 被拦截（含中文标点、缺失关键字等 20+ case）
- [ ] DELETE/UPDATE/DROP/INSERT 100% 被拦截
- [ ] 不存在的表名/列名 100% 被检测
- [ ] 无 WHERE 的 SELECT * 触发性能警告
- [ ] 只读副本上执行 `CREATE TABLE` 被数据库权限层拒绝（双重保险）

---

### Task 1.4: SQL Agent 核心循环（2 周）

**目标：** 实现 SQL Agent 的 generate → guard → execute → verify 四阶段循环。

**子任务：**

| # | 子任务 | 产出 | 工时 |
|---|--------|------|------|
| 1.4.1 | Schema RAG 检索：用户 query → BGE-M3 embedding → PGVector 语义搜索（或 table_names 精确命中时跳过语义搜索） | `schema_rag.py` | 2天 |
| 1.4.2 | LLM SQL 生成：Haiku/Sonnet + Schema RAG 结果 + 实体 → SQL | `sql_generator.py` | 2天 |
| 1.4.3 | Agent 循环编排：LangGraph StateGraph 实现 generate→guard→execute→verify 四阶段+条件边 | `sql_agent_graph.py` | 3天 |
| 1.4.4 | 确定性收敛：执行成功 AND 行数∈[1,10000] → 自动收敛 | `convergence.py` | 1天 |
| 1.4.5 | LLM 语义验证（Haiku）：确定性条件不满足时，Haiku 判断执行结果是否语义正确 | `semantic_verify.py` | 1天 |
| 1.4.6 | 进程内存状态管理（Python dict）：单 Agent 循环内状态 | `state_manager.py` | 0.5天 |
| 1.4.7 | 错误反馈构造：语法错误/执行异常/空结果/异常行数 → 构造反馈文本注入下一轮 | `error_feedback.py` | 0.5天 |

**Agent 循环图：**
```
generate（LLM SQL生成）
    │
    ▼
guard（SQL Guard 校验）
    │
    ├─ 失败 → 错误反馈 → 回到 generate
    │
    ▼
execute（只读副本执行）
    │
    ▼
verify（语义验证）
    │
    ├─ 不通过 → 异常反馈 → 回到 generate
    └─ 通过 → 返回结果（END）

max_rounds: 5, timeout: 3s
```

**验收（单元）：**
- [ ] MockLLM 下测试三种收敛模式：首轮收敛、第3轮收敛、永不收敛（强制停止）
- [ ] SQL Guard 失败后正确反馈错误信息到下一轮
- [ ] 超时（3s）后正确返回最后成功执行的 SQL 结果
- [ ] 5 轮后未收敛 → 强制返回

**验收（集成）：**
- [ ] 对 20 条测试 query 跑端到端 SQL Agent 循环，Execution Accuracy ≥ 80%

---

### Task 1.5: 高风险查询用户确认闸门（3 天）

**目标：** 对涉及财务、全表扫描、多表 JOIN 的高风险查询，执行前展示 SQL 等用户确认。

**子任务：**

| # | 子任务 | 产出 | 工时 |
|---|--------|------|------|
| 1.5.1 | 确认闸门规则引擎：匹配财务指标/全表扫描/≥3 JOIN/大时间范围模式 | `confirmation_gate.py` | 1天 |
| 1.5.2 | 确认 UI：展示 SQL 原文 + 涉及表/字段 + 时间范围 + 风险提示 | Streamlit 组件 | 1天 |
| 1.5.3 | 确认流程集成：暂停 Agent 循环 → 等用户确认 → 继续或修改查询 | Agent 流程改造 | 1天 |

**确认展示模板：**
```
即将执行以下SQL:
  SELECT SUM(amount) FROM orders WHERE status = 'paid' AND created_at >= '2026-01-01'

涉及表: orders (订单表)
时间范围: 2026-01-01 至今
预计返回: 1 行
风险提示: 涉及财务指标聚合

[确认执行] [修改查询]
```

**验收：**
- [ ] 含"营收"关键词的查询触发确认闸门
- [ ] 3 个以上 JOIN 的 SQL 触发确认闸门
- [ ] 确认后正确执行，取消后正确返回修改提示

---

### Task 1.6: 结果质量检测（3 天）

**目标：** 执行 SQL 后对结果做基本统计质量扫描，生成 QualityReport。

**子任务：**

| # | 子任务 | 产出 | 工时 |
|---|--------|------|------|
| 1.6.1 | 空结果检测：行数=0 → 分析可能原因（过滤过严/时间范围/表名选错） | `quality_empty.py` | 0.5天 |
| 1.6.2 | NULL 比例异常：每列 NULL 占比 > 50% → 标记 | `quality_null.py` | 0.5天 |
| 1.6.3 | 数值异常值检测：max > P99 × 10 → 标记极端值 | `quality_outlier.py` | 0.5天 |
| 1.6.4 | 数据新鲜度标注：`pg_last_xact_replay_timestamp()` 获取副本延迟 | `quality_freshness.py` | 0.5天 |
| 1.6.5 | QualityReport 生成 + 结果下方质量标注渲染 | `quality_report.py` | 1天 |

**验收：**
- [ ] 空结果的 SQL 查询附带原因分析标注
- [ ] NULL 比例 > 50% 的列在结果中被标记
- [ ] 副本延迟信息展示在结果下方

---

### Task 1.7: WorkerOutput 实现（2 天）

**目标：** 实现 SQL Agent 的标准 WorkerOutput 格式，为后续 Phase 的 Agent 间通信打基础。

**交付物：**
- `worker_output.py` — WorkerOutput + SQLWorkerOutput Pydantic 模型
- SQL Agent 特有字段：`execution_sql`, `guard_risk_level`, `quality_report`, `tables_used`, `columns_used`, `data_limitations`

---

### Task 1.8: 单元测试 + Agent Eval（1 周）

**目标：** 建立 SQL Agent 的测试体系。

**子任务：**

| # | 子任务 | 产出 | 工时 |
|---|--------|------|------|
| 1.8.1 | SQL Guard 单元测试（语法校验/DDL拦截/表列存在性/性能保护各 20+ case） | `test_sql_guard.py` | 1天 |
| 1.8.2 | Agent 循环 MockLLM 测试（首轮收敛/多轮收敛/永不收敛 3 种模式） | `test_sql_agent_loop.py` | 1天 |
| 1.8.3 | Schema RAG 检索测试（Recall@10 + 精确表名命中） | `test_schema_rag.py` | 1天 |
| 1.8.4 | Agent Eval Dataset 构建（50 条 × golden SQL） | `tests/eval/sql_eval_dataset.json` | 2天 |
| 1.8.5 | E2E 测试：真实 LLM 跑 50 条 → Execution Accuracy | `test_sql_e2e.py` | 1天 |

**Agent Eval 数据示例：**
```json
{
  "query": "过去7天各状态的订单数",
  "query_type": "data_query",
  "golden_sql": "SELECT status, COUNT(*) FROM orders WHERE created_at >= NOW() - INTERVAL '7 days' GROUP BY status",
  "golden_tables": ["orders"],
  "golden_columns": ["status", "created_at"],
  "expected_row_count_range": [1, 10]
}
```

**验收：**
- [ ] SQL Guard 单元测试覆盖率 ≥ 80%
- [ ] Agent 循环 MockLLM 测试通过 3 种收敛模式
- [ ] E2E Execution Accuracy ≥ 80%

---

## 四、阶段输出与交付物

| 交付物 | 路径 | 格式 |
|--------|------|------|
| SQL Agent 完整代码 | `src/agents/sql_agent/` | Python |
| SQL Guard 层 | `src/agents/sql_agent/guard/` | Python |
| Schema 摄入管道 | `src/ingestion/schema/` | Python |
| Agent Eval Dataset | `tests/eval/sql_eval_dataset.json` | JSON |
| 单元测试 | `tests/unit/sql_agent/` | Python |
| E2E 测试 | `tests/e2e/sql_agent/` | Python |
| Streamlit 测试 UI | `src/ui/streamlit_app.py` | Python |

---

## 五、验收标准

### 5.1 功能验收

- [ ] 自然语言查询 → 正确 SQL 生成 + 执行 + 结果返回（端到端）
- [ ] Execution Accuracy ≥ 80%（50 条测试集）
- [ ] DDL/DML 操作 100% 被 SQL Guard 拦截
- [ ] 语法错误 SQL 100% 被拦截 + 自动重生成
- [ ] Schema 变更后 < 10 分钟内可检索到新表
- [ ] 高风险查询（财务/全表扫描）触发用户确认闸门
- [ ] 查询结果附带质量标注（空结果原因/NULL比例/副本延迟）

### 5.2 性能验收

- [ ] 单源 SQL 查询 P50 < 3s, P95 < 6s
- [ ] Agent 最多 5 轮循环后强制返回（≤ 3s 超时）
- [ ] Schema RAG 检索 P99 < 100ms
- [ ] SQL Guard 校验 P99 < 50ms

### 5.3 质量验收

- [ ] SQL Guard 单元测试覆盖率 ≥ 80%
- [ ] Agent 循环 3 种收敛模式 MockLLM 测试通过
- [ ] 虚假信心率 < 15%

### 5.4 基础设施验收

- [ ] PGVector 正常运行，HNSW 索引已创建
- [ ] BGE-M3 embedding 服务可用（P99 < 100ms）
- [ ] Qwen3-8B 降级模型可用（Claude API 不可用时自动切换）
- [ ] Presidio 脱敏规则生效（手机号/邮箱/IP/金额被替换）
- [ ] APScheduler 定时轮询正常运行

---

## 六、风险与缓解

| 风险 | 概率 | 缓解 |
|------|------|------|
| Text-to-SQL Execution Accuracy 不达标（< 70%） | 中 | 增强 Schema RAG 的业务元数据注入；扩充 few-shot 黄金示例集；Phase 0 Plan B 降低 LLM 依赖 |
| SQL Guard 漏拦截（新型 DDL 语法绕过） | 低 | SQLGlot 持续更新；数据库只读权限作为兜底保险 |
| 只读副本延迟过高（> 30s） | 中 | 在结果中标注数据新鲜度；后续支持用户选择查询主库（需审批） |
| Schema 摄入管道遗漏变更 | 低 | 每日凌晨全量同步作为兜底 |
| BGE-M3 + Qwen3-8B GPU 资源不足 | 中 | 使用 AWQ INT4 量化降低 VRAM 需求；申请额外 GPU |
