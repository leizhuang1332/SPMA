# Design: Supervisor Agent 设计（编排Agent）

> 所属项目：[SPMA 全局概览](SPMA-design-00-global-overview.md)
> 权威架构：[5独立Agent架构设计](SPMA-design-07-agent-architecture.md) — **如有冲突以此为准**
> 模块职责：理解用户意图、抽取关键实体、改写查询、**多轮编排循环**（分发→收集→质量评估→重调度）、将结构化的检索参数通过 Send API 并行派发给各 Worker Agent

---

## 模块在架构中的位置

```
                                    ┌─────────────────────────┐
                                    │     API Gateway / LB     │
                                    └───────────┬─────────────┘
                                                │
                        ┌───────────────────────▼───────────────────────┐
                        │              Supervisor Agent                  │  ← 本文档范围
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
              └──────────────┘  └────────────┘  └────────────────────┘
```

---

## 一、编排循环总览

Supervisor Agent 是整个系统的编排中枢。与旧版"查询理解层"不同，新版 Supervisor 是一个**具备多轮自主推理能力的编排 Agent**：

- **Round 1：** LLM 意图分类 + 实体抽取 → Send API 并行派发给 Doc/Code/SQL Agent
- **Round 2+：** 收集 Worker Agent 返回结果 → 质量评估 → 评分 ≥ 0.6 收敛 / 评分 < 0.6 + 重调度 < 2 次 → 调整参数重新派发
- **收敛条件：** 所有 Worker 评分 ≥ 0.6 OR 已重调度 2 次无改善
- **超时：** 5s（含 Worker 等待），超时后取最佳结果 + 标注"结果可能不完整"

### 编排循环流程图

```
用户输入
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ Round 1: Query Understanding (~500ms)                            │
│  ├─ 意图分类（确定需要哪些 Worker Agent）                          │
│  ├─ 实体抽取（确定在每个 Worker 里查什么）                          │
│  ├─ 查询改写（标准化 + 扩展 + 条件性分解/HyDE）                    │
│  └─ Send API 并行派发 → Doc Agent, Code Agent, SQL Agent         │
└────────────────────────────┬────────────────────────────────────┘
                             │ 等待 Worker 返回（并行）
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ 收集 Worker 结果 + 质量评估 (~200ms)                               │
│  ├─ evaluate_worker_quality() 对每个 Worker 输出打分 (0-1)        │
│  ├─ 评分维度: 结果数量 + Worker自评置信度 + 精确匹配命中             │
│  └─ 权重按 query_type 动态调整                                    │
└────────────────────────────┬────────────────────────────────────┘
                             │
                    ┌────────▼────────┐
                    │ 所有Worker≥0.6?  │
                    └────────┬────────┘
                             │
              ┌──────────────┼──────────────┐
              │ YES          │ NO           │
              ▼              ▼              │
         收敛 → Synthesis   round ≥ 2?      │
                            ┌──────┴──────┐
                            │ YES  │ NO   │
                            ▼      ▼       │
                         强制收敛  调整参数 │
                          → Synth  重派发 ─┘
```

### Agent 状态数据模型

```python
class SupervisorState(AgentState):
    """Supervisor Agent 专属状态"""
    round: int                    # 当前编排轮次
    original_query: str           # 用户原始问题
    classification: ClassificationResult  # 意图分类结果
    entities: ExtractedEntities   # 抽取的实体
    rewritten_queries: dict       # 改写后的查询（by worker type）
    worker_outputs: list[WorkerOutput]  # 本轮 Worker 返回结果
    quality_scores: dict          # 每个 Worker 的质量评分
    reschedule_count: int         # 已重调度次数
    final_results: list[dict]     # 最终收集的最佳结果
```

---

## 二、Supervisor 质量函数

Supervisor 的收敛依赖 Worker 评分——这是编排循环的核心决策逻辑。

### 质量评分逻辑（自然语言描述）

**evaluate_worker_quality 评分维度（0-1 分）：**

Supervisor 对每个 Worker 返回结果从三个维度打分，最终得分 = 各维度分数 × 维度权重的加权和。维度权重按 `query_type` 动态调整：

| 维度 | 分数范围 | 评分规则 |
|------|---------|---------|
| 结果数量 | 0-0.3 | result_count=0 → 0.0 分；<3 → 0.1 分；<10 → 0.2 分；≥10 → 0.3 分 |
| Worker 自评置信度 | 0-0.3 | `output.confidence × 0.3`，即 Worker 的自我信心线性映射 |
| 精确匹配命中 | 0-0.4 | 命中 req_ids/table_names/code_refs 中任一 → 0.4 分（确定性检索路径）；否则 0.0 分 |

**query_type 权重矩阵：**

| query_type | count 权重 | confidence 权重 | exact_match 权重 |
|-----------|-----------|----------------|-----------------|
| data_query | 0.3 | 0.3 | 0.4 |
| search | 0.4 | 0.4 | 0.2 |
| trace | 0.2 | 0.3 | 0.5 |

**should_reschedule 判断逻辑：** 扫描所有 Worker 的质量评分，如果有任一 Worker 评分 < 0.6 且当前轮次 < 2，则触发重调度。所有 Worker ≥ 0.6 或已达 2 轮重调度 → 不重调度。

**adjust_params 调整策略：** 从成功的 Worker 结果中提取上下文实体（req_ids、table_names、module），注入到失败 Worker 的检索参数中。具体调整：
- 调整检索 query：将其他 Worker 找到的实体追加到失败 Worker 的原始 query 后
- 调整检索范围：扩展/收缩时间窗口、放宽 doc_type 过滤
- 不调整：Worker 类型选择（不新增/移除 Worker）

---

## 三、Agent 交互协议

### Agent 间交互：DAG（有向无环图）

```
Supervisor ──Send API──▶ Doc Agent    ──┐
           ──Send API──▶ Code Agent   ──┼── fan-in ▶ Supervisor(收集)
           ──Send API──▶ SQL Agent    ──┘       │
                                                ▼
                                         Synthesis Agent
```

- Agent 间**不互调**。网状调用推迟到 Phase 5+。
- Worker Agent 内部各自循环，Supervisor 通过 Send API 并行派发、fan-in 收集。
- 每个 Worker Agent 作为独立的 LangGraph 子图。

### Checkpointer 隔离

每个 Agent 子图使用独立 LangGraph checkpointer namespace，避免并发冲突。Supervisor 通过 Send API 派发时，在子图 config 中注入 namespace，格式为 `{query_id}:{agent_type}`（如 `uuid-xxx:doc`、`uuid-xxx:code`、`uuid-xxx:sql`）。同一 query 下不同 Agent 的子图状态互不干扰。

### Worker 输出格式

```python
class WorkerOutput:
    worker_type: str           # "doc" | "code" | "sql"
    result_count: int          # 返回结果数
    results: list[dict]        # 检索/SQL结果
    citations: list[Citation]  # 每条结果的引用元数据
    confidence: float          # Worker自评信心 (0-1)
    has_exact_match: bool      # 是否命中精确匹配实体
    rounds_used: int           # 使用的轮数
    original_query: str        # 原始检索query

class Citation:
    source_type: str           # "prd" | "code" | "sql"
    source_id: str             # doc_id, file_path:line, table.column
    snippet: str               # 引用原文片段（≤200 chars）
```

---

## 四、Agent Action Guard

Supervisor 可调用的工具受白名单限制：

```python
ALLOWED_ACTIONS = {
    'supervisor': [
        'classify_intent',      # LLM意图分类
        'extract_entities',     # LLM实体抽取
        'rewrite_query',        # 查询改写
        'send_to_worker',       # Send API 派发Worker
        'collect_results',      # fan-in 收集Worker结果
        'evaluate_quality',     # 质量评估
        'reschedule',           # 重调度
        'finalize',             # 收敛 → 交给Synthesis
    ],
}
```

---

## 五、意图分类器设计（Round 1）

这是 Supervisor Round 1 的第一步——分类错了，后面的 Worker 调度全错。不需要训练专用模型，用 **LLM 结构化分类 + 规则兜底** 两层架构。

### 5.1 第一层：LLM 结构化分类

使用轻量模型（Claude Haiku 或本地 Qwen3-8B，延迟 < 500ms），通过 LangChain `with_structured_output` / JSON mode 输出固定 schema：

```python
from pydantic import BaseModel
from typing import Literal, Optional

class ClassificationResult(BaseModel):
    """Supervisor 意图分类输出"""
    sources: list[Literal["doc", "code", "sql"]]
    is_cross_source: bool
    entities: Optional[dict]  # {"module": "用户登录", "req_ids": ["REQ-187"], ...}
    query_type: Literal["trace", "search", "data_query", "explain"]
```

分类 Prompt：

```
你是一个查询路由器。分析用户问题，输出 JSON。

数据源定义：
- doc: PRD 文档、产品需求、功能规格、需求变更
- code: 代码实现、函数、类、文件路径、bug、架构
- sql: 业务数据、统计、报表、指标查询

分类规则：
- 含需求ID [REQ-XXXXX] → 至少包含 doc
- 含表名/列名/数据/统计/多少 → 至少包含 sql
- 含文件路径/函数名/代码/类名/bug → 至少包含 code
- "X影响Y"、"X对应哪个Z" → 跨源查询，标记 is_cross_source=true
- 模糊查询无法判断 → 默认三源全查，标记 query_type="search"

示例：
"上周用户登录改了什么需求？" → {"sources": ["doc"], "is_cross_source": false, ...}
"REQ-187 改了哪些代码和表？" → {"sources": ["doc","code","sql"], "is_cross_source": true, ...}
"过去7天新增了多少用户？" → {"sources": ["sql"], "is_cross_source": false, ...}
"oauth.py 的 token_refresh 是谁写的？" → {"sources": ["code"], "is_cross_source": false, ...}
"用户登录怎么做的？" → {"sources": ["doc","code","sql"], "is_cross_source": true, "query_type": "search", ...}
```

### 5.2 第二层：规则兜底

LLM 分类结果之后加硬规则——LLM 做语义理解，规则做确定性补刀：

**规则兜底的修正逻辑（自然语言描述）：**

LLM 分类结果之后依次执行四条硬规则——规则做确定性补刀，LLM 做语义理解：

| 规则 | 触发条件（正则匹配） | 动作 |
|------|-------------------|------|
| 规则1：数据查询关键词 | 用户输入包含"表/字段/列/数据/统计/多少/汇总/趋势/报表/指标" | 强制将 `sql` 加入 sources |
| 规则2：需求 ID 格式 | 用户输入包含 `REQ-数字`、`PROJ-数字` 或 `PRD-数字` | 强制将 `doc` 加入 sources |
| 规则3：代码引用痕迹 | 用户输入包含 `.py`/`.java`/`.go` 等扩展名，或"函数/方法/类"等关键词 | 强制将 `code` 加入 sources |
| 规则4：极短模糊查询 | 用户输入 < 5 个字且 LLM 未抽到任何实体 | 保守策略：三源全查，query_type 设为 "search" |

每条规则触发后同步更新 `is_cross_source`（当 sources 数量 > 1 时设为 true）。规则按优先级从上到下依次执行，后一条规则不会撤销前一条规则的修正。

### 5.3 分类质量保障

**评估集：** 从 Shadowing 观察和用户测试中收集 100 条真实查询 → 人工标注正确的 `sources` → 跑分类器计算准确率。目标：≥ 95%。

分类准确率在这个场景下不是高难度问题——三类数据源的语义边界足够清晰：

| 失败模式 | 例子 | 发生率估计 | 对策 |
|---------|------|-----------|------|
| 模糊查询 | "用户登录怎么做的？" | ~10% | 默认三源全查，答案多样性本身就是价值 |
| 术语歧义 | "表的索引" | ~2% | 规则优先——含"表"字就走 SQL Agent |
| 无上下文短查询 | "那个功能" | ~5% | 多轮对话带上前一轮分类结果；单轮直接反问 |
| 跨源漏判 | "REQ-187 的实现"（漏了 code） | ~3% | 规则2补上；同时一旦识别需求ID，默认同时查 doc+code |

**持续改进：** 每次分类结果记录日志（用户问题 + 分类输出 + 实际路由 + 用户反馈）。每周 review 20 条，往规则层加 pattern。不依赖一次性调参。

### 5.4 降级路径

- **Haiku API 不可用** → 切到本地 Qwen3-8B（分类质量下降 2-3%，规则层兜底）
- **全部 LLM 不可用** → 纯规则分类（正则匹配 + 关键词），准确率约 85%，但系统仍可用
- **分类置信度极低**（无实体、无关键词）→ 保守策略：三源全查

---

## 六、实体抽取设计（Round 1）

意图分类回答"查哪些源"，实体抽取回答"在每个源里查什么"。分类和抽取共享同一个 LLM 调用——一次请求同时输出分类结果和实体列表，避免串行延迟。

### 6.1 抽取哪些实体

```python
class ExtractedEntities(BaseModel):
    """从用户问题中抽取的结构化实体"""
    
    # ── 跨源通用实体 ──
    module: Optional[str]          # 功能模块："用户登录"、"支付网关"、"审批流"
    req_ids: list[str]             # 需求ID：["REQ-2024-0187", "PROJ-1234"]
    time_range: Optional[str]      # 时间范围："上周"、"过去7天"、"2026年3月"
    version: Optional[str]         # 版本/分支："v2.3"、"release/2026Q1"、"main"
    
    # ── SQL 相关实体 ──
    table_names: list[str]         # 表名：["users", "orders"]
    column_names: list[str]        # 列名：["status", "amount", "created_at"]
    metrics: list[str]             # 指标/聚合："新增用户数"、"订单总额"、"日活"
    group_by: Optional[str]        # 分组维度："按天"、"按部门"、"按状态"
    
    # ── 代码相关实体 ──
    code_refs: list[str]           # 文件/类/函数引用：["oauth.py", "TokenService", "login_oauth"]
    person: Optional[str]          # 代码作者/提交人："张三"、"@leizhuang1332"
    
    # ── 文档相关实体 ──
    doc_types: list[str]           # 文档类型：["PRD", "技术方案", "接口文档", "会议纪要"]
```

**抽取 Prompt（与意图分类同一请求）：**

```
从用户问题中抽取以下实体。找不到的字段设为 null 或空列表。

实体说明：
- module: 问题涉及的功能模块或业务领域
- req_ids: 需求编号（格式 REQ-XXXXX 或 PROJ-XXXXX）
- time_range: 时间范围表述。保留原文，如"上周"、"过去7天"、"2026年Q1"
- version: 版本号或分支名
- table_names: 提到的数据库表名（英文或中文表名）
- column_names: 提到的列名/字段名
- metrics: 要查询的指标，如"新增用户数"、"订单总额"
- group_by: 分组维度，"按天"→daily，"按部门"→department
- code_refs: 提到的文件名、类名、函数名
- person: 提到的人名（代码作者/提交人）
- doc_types: 提到的文档类型，如"PRD"、"技术方案"、"接口文档"

示例：
"过去7天订单表里各种状态的订单数是多少？"
→ {"module":"订单","time_range":"过去7天","table_names":["orders"],"column_names":["status"],"metrics":["订单数"],"group_by":"按状态"}

"张三上周改的 oauth.py 里 TokenService 这个类，关联的需求是哪个？"
→ {"req_ids":[],"code_refs":["oauth.py","TokenService"],"person":"张三","time_range":"上周","module":"认证"}
```

### 6.2 抽取后怎么用——实体到 Worker Agent 的分发

实体是下发给各 Worker Agent 的检索参数。不同的实体到达不同的 Agent，各自用最擅长的方式消费：

```
                        ┌─────────────────────────────┐
                        │     ExtractedEntities        │
                        └─────────────┬───────────────┘
                                      │
            ┌─────────────────────────┼─────────────────────────┐
            │                         │                         │
            ▼                         ▼                         ▼
    ┌───────────────┐       ┌───────────────┐       ┌───────────────┐
    │   Doc Agent    │       │  Code Agent    │       │   SQL Agent    │
    └───────────────┘       └───────────────┘       └───────────────┘
```

**Doc Agent 实体用法：**

| 实体 | 用法 | 示例 |
|------|------|------|
| `req_ids` | 精确匹配——元数据过滤 `WHERE req_id IN (...)`，这是 Doc Agent 最高优先级的检索信号 | `req_id = "REQ-2024-0187"` → 直接返回该需求的所有 PRD 片段 |
| `module` | 语义搜索+关键词——向量检索的 query text | `"用户登录 需求规格 PRD"` |
| `time_range` | 元数据过滤——限定文档更新时间范围 | `WHERE updated_at > '2026-05-29'` |
| `version` | 版本过滤——Confluence 版本历史或 Git tag 对应的文档快照 | `WHERE version = 'v2.3'` |
| `doc_types` | 文档类型过滤 | `WHERE doc_type IN ('PRD', '技术方案')` |

**Code Agent 实体用法：**

| 实体 | 用法 | 示例 |
|------|------|------|
| `code_refs` | 精确搜索——文件名精确匹配 + 函数名/类名 grep。最高优先级信号 | 搜 `oauth.py` 文件 + grep `TokenService` 定义 |
| `req_ids` | 关联搜索——grep commit log 和代码注释中引用该需求 ID 的代码 | `git log --grep="REQ-2024-0187"` 找变更文件 |
| `module` | 搜索词构造——中文术语翻译为英文代码标识符 | "用户登录"→搜索词 `["login","auth","oauth"]` |
| `person` | 作者过滤——`git log --author="张三"` 限定变更范围 | 结合 `time_range` 精确定位 |
| `time_range` | 时间过滤——限制 git log 的 `--since` / `--until` | `git log --since="2026-05-29"` |
| `version` | 分支/tag 过滤 | `git log release/2026Q1` |

**SQL Agent 实体用法：**

| 实体 | 用法 | 示例 |
|------|------|------|
| `table_names` | 精确 Schema 检索——跳过语义搜索，直接查这些表的 DDL 和列注释 | `SELECT column_name, data_type FROM information_schema.columns WHERE table_name IN ('users','orders')` |
| `column_names` | 列级检索——在 Schema RAG 中精确定位列的语义描述 | 搜 `status` 列的业务含义（可能有枚举值映射："待支付/已支付/已取消"） |
| `metrics` | 聚合函数构造——决定 SELECT 子句 | `"订单数"` → `COUNT(order_id)`, `"订单总额"` → `SUM(amount)` |
| `time_range` | WHERE 条件——自然语言时间转 SQL 日期范围 | `"过去7天"` → `WHERE created_at >= NOW() - INTERVAL '7 days'` |
| `group_by` | GROUP BY 构造——自然语言分组转 SQL | `"按状态"` → `GROUP BY status`；`"按天"` → `GROUP BY DATE(created_at)` |
| `module` | Schema RAG 语义搜索——当 `table_names` 为空时，用 module 语义检索相关表 | `"用户登录"` → 检索 `users`, `user_sessions`, `oauth_states` 表 |

### 6.3 跨源关联的桥梁实体

三类数据源之间靠两个实体互相"穿针引线"：

**`req_ids` — 最强跨源桥梁：**

```
用户问题: "REQ-187 改了哪些代码和表？"
         │
         ├─ Doc Agent: req_id="REQ-187" → 返回 PRD 变更内容
         │
         ├─ Code Agent: req_id="REQ-187" → git log --grep → 找到变更的代码文件
         │                                 → 提取代码中引用的表名 → 反馈给 SQL Agent
         │
         └─ SQL Agent: req_id="REQ-187" → 在表注释/数据字典中搜索
                       + 从 Code Agent 获得表名 → 精确查表结构
```

**`table_names` — SQL ↔ Doc 桥梁：**

```
用户问题: "users 表加了 oauth_provider 字段是哪个需求要求的？"
         │
         ├─ SQL Agent: table_name="users", column_name="oauth_provider"
         │             → 查 DDL 变更历史、列注释
         │
         └─ Doc Agent: module + 从 SQL Agent 获得的变更时间
                       → 搜索对应时间段的 PRD 文档 → 定位需求
```

### 6.4 抽取质量保障

- **评估集：** 与意图分类共享 100 条标注数据，额外标注实体 correctness（抽取的实体是否正确）和 completeness（是否遗漏关键实体）
- **目标：** 实体 correctness ≥ 90%，completeness ≥ 85%
- **关键失败模式：** 用户用同义词指代表名（"用户表" vs `users`），用简称指代模块（"登录" vs "用户登录认证"）。对策：在规则层维护一个同义词映射表（"用户表"→`users`、"订单表"→`orders`），不依赖 LLM 猜测

---

## 七、实体完备度评估（Round 1）

实体抽取不是每次都成功的。用户说"那个功能上线了吗"，既没有需求 ID 也没有表名——但系统不能就此放弃。三层递进式补救：

```
抽取结果 → 实体完备度评估 → 分层处置
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
        实体充足         部分缺失          几乎为空
        (>2个关键)     (有module无细节)   (模糊指代/闲聊)
```

### 7.1 第一层：实体完备度评估（确定性代码，非 LLM）

**谁打分：** 一段纯 Python 函数，不是 LLM。不调用任何模型，不引入不确定性。LLM 已经做完了它那部分（实体抽取），完备度评估只做一件事——**数一下抽到了几个高价值实体**。

**打分标准——单一原则：信息增益（Information Gain）**

一个实体的权重 = 它能把搜索空间缩小多少倍：

| 权重 | 实体 | 搜索空间缩减 | 原理 |
|------|------|-------------|------|
| **10** | `req_ids` | ~10,000× | 一个需求 ID 直接定位到 3-5 个文档片段，从百万级分块中精准捞出目标 |
| **10** | `table_names` | ~10,000× | 跳过整个 Schema RAG 语义搜索环节，`WHERE table_name='users'` 直接返回 DDL |
| **10** | `code_refs` | ~10,000× | `grep "TokenService"` + 文件名 `oauth.py`，毫秒级精确定位，不走向量检索 |
| **5** | `module` | ~100× | 把搜索从全量语料库缩小到一个功能域（如从 100 万块缩小到 1 万块） |
| **4** | `metrics` | ~100× | 确定了聚合方向，SQL Agent 不必猜测 SELECT 子句 |
| **4** | `column_names` | ~100× | 在 Schema RAG 中精确定位列，而非泛泛搜索整个表的列 |
| **2** | `time_range` | ~10× | 元数据过滤，缩小时间窗口内的候选集 |
| **2** | `person` | ~10× | `git log --author` 限定变更范围 |
| **2** | `version` | ~10× | 限定分支/tag，过滤无关版本的代码和文档 |
| **1** | `group_by` | ~2× | 确定分组维度，但对检索本身帮助有限 |
| **1** | `doc_types` | ~2× | 过滤文档类型，但大多数查询不指定类型 |

**为什么权重不是连续的？** 10、5、2 三档不是拍脑袋的——对应三种截然不同的检索行为：

- **权重 10（精确匹配级）：** 有了它，对应的 Agent 可以**跳过语义搜索**，直接用元数据查询或 grep。延迟从秒级降到毫秒级。
- **权重 5（语义锚点级）：** 不能跳过语义搜索，但把搜索限定在一个功能域内，检索精度大幅提升。
- **权重 1-2（辅助过滤级）：** 只在元数据层面做减法，对检索策略本身没有影响。语义搜索仍然需要跑。

**阈值逻辑：**

**完备度评估逻辑（自然语言描述）：**

纯确定性函数，不调用 LLM。核心原理：每个实体的权重 = 它能把搜索空间缩小的倍数（取对数）。

实体权重表：

| 权重 | 实体 | 搜索空间缩减 | 原理 |
|------|------|-------------|------|
| 10 | req_ids, table_names, code_refs | ~10,000× | 精确匹配级——可跳过语义搜索 |
| 5 | module | ~100× | 强语义锚点级——把搜索限定在一个功能域 |
| 4 | metrics, column_names | ~100× | 强锚点——确定聚合方向或精确定位列 |
| 2 | time_range, person, version | ~10× | 辅助过滤级——元数据减法 |
| 1 | group_by, doc_types | ~2× | 辅助过滤级 |

**计算方式：** 遍历所有实体字段，非空（非 None、非空列表、非空字符串）则累加权重。总分与阈值比较：

- **≥ 10（rich）：** 至少有一个精确匹配级实体，对应的 Agent 可以跳过语义搜索，直接元数据查询或 grep
- **5-9（partial）：** 有功能锚点但无精确匹配 → 混合检索（元数据过滤 + 语义搜索）
- **< 5（bare）：** 无任何有效检索锚点 → 纯语义搜索兜底或反问用户

**阈值为什么设在这里：**

- **rich ≥ 10：** 恰好等于一个精确匹配级实体的权重。意思是"至少有一个 `req_ids`、`table_names` 或 `code_refs` 被抽到了"。
- **partial ≥ 5：** 恰好等于一个 `module` 的权重。意思是"即使没有精确 ID，至少知道功能域"。
- **bare < 5：** 连功能域都不知道。检索没有锚点，纯语义搜索是唯一选择。

**为什么不用 LLM 打分：** LLM 评判自己的输出质量是一个经典的循环依赖问题——LLM 抽取了实体，然后问同一个 LLM"你觉得你抽得怎么样？"，它大概率会说"抽得很好"。反过来，如果换一个 LLM 来打分，又引入了新的延迟、成本和不确定性。而完备度评估本身不涉及语义判断——"有没有抽到需求 ID"是纯规则问题，用代码就够了。

### 7.2 第二层：分级处置

**Level: rich（实体充足，约占 40% 查询）**

实体驱动精确检索。`req_ids` 非空→Doc Agent 直接用元数据过滤而非向量检索。`table_names` 非空→SQL Agent 跳过 Schema RAG 直接查 DDL。这条路延迟最低、准确率最高。

```
例: "REQ-187 改了 oauth.py 和 users 表的哪些字段？"
→ req_ids=["REQ-187"], code_refs=["oauth.py"], table_names=["users"], column_names=[]
→ Doc: WHERE req_id='REQ-187' （精确）
→ Code: grep "REQ-187" + 文件名匹配 oauth.py （精确）
→ SQL: WHERE table_name='users' （精确）
→ column_names 为空 → 返回 users 表全部字段供用户确认
```

**Level: partial（有方向缺细节，约占 45% 查询）**

实体提供检索锚点但不够精确。各 Agent 用实体做**混合检索**——实体用于元数据过滤缩小范围，原始用户问题用于语义搜索补全。

```
例: "用户登录模块上周的改动"
→ module="用户登录", time_range="上周", req_ids=[], code_refs=[], table_names=[]
→ Doc: 元数据过滤 time_range + 语义搜索 "用户登录 需求变更"
→ Code: 元数据过滤 time_range + git log --since + 搜索词 "authentication login"
→ SQL: 无表名 → Schema RAG 语义搜索 "用户 登录 认证" 找相关表
→ 返回时标注"以下结果基于'用户登录'+'上周'检索，如需精确匹配请提供需求ID或表名"
```

**Level: bare（几乎为空，约占 15% 查询）**

没有实体锚点时，两种选择：

| 判断条件 | 动作 | 原因 |
|---------|------|------|
| 原始问题 > 15 字 | 三源全查，纯语义搜索，用原始问题直接做向量检索 | 有足够语义信息，检索质量不会太差 |
| 原始问题 ≤ 15 字 且 有对话历史 | 从上一轮分类和实体中继承上下文，合并后再检索 | "那个功能"→ 上一轮聊的是"用户登录"→ 用"用户登录"补全 |
| 原始问题 ≤ 15 字 且 无对话历史 | **反问用户**，不猜 | "你提到'那个功能'，请问是指哪个功能模块？方便给个需求ID或文件名吗？" |

**bare 级别的三条处置路径（自然语言描述）：**

| 判断条件 | 处置策略 | 具体动作 |
|---------|---------|---------|
| 原始问题 > 15 字 | **路径A：纯语义搜索兜底** | 原始问题直接作为检索 query，按分类结果的 sources 做向量检索，标注"未识别到关键实体，使用全文语义搜索" |
| 原始问题 ≤ 15 字 且 有对话历史 | **路径B：上下文继承** | 从上一轮对话中提取 module 实体，与当前用户问题拼接为新 query（如 `"{上轮module} {当前问题}"`），标注继承来源 |
| 原始问题 ≤ 15 字 且 无对话历史 | **路径C：反问用户** | 不猜测，返回澄清提示："你指的是哪个功能模块？如果知道需求ID（如 REQ-XXXXX）或相关的文件名，告诉我会更精准。" |

每条路径返回一个包含 `strategy`（策略名）、`query`（实际使用的检索词）、`sources`（目标数据源）和 `note`（透明度标注）的调度指令。

### 7.3 第三层：纯规则兜底（LLM 不可用时）

当 LLM 分类和抽取全部不可用时，降级到纯规则抽取——不需要模型，正则直接上：

**纯规则抽取逻辑（零 LLM 依赖，准确率约 70-80%）：**

当 LLM 分类和抽取全部不可用时，降级到纯正则 + 词典匹配：

| 实体字段 | 抽取方式 | 示例 |
|---------|---------|------|
| `req_ids` | 正则匹配 `REQ/PROJ/PRD-XXXXX` 格式 | `REQ-2024-0187` |
| `table_names` | 从预定义的常见表名词典中匹配（如"用户表"→users） | 用户输入含"订单表"→提取 `orders` |
| `code_refs` | 正则匹配 `.py`/`.java`/`.go` 等文件扩展名引用 | `oauth.py`、`TokenService.java` |
| `time_range` | 正则匹配时间表述："上周"、"过去N天"、"N月N日" | "过去7天"→时间区间 |
| `module` | 从预定义的模块名词典匹配 | "登录"→"用户登录认证" |
| `person` | 正则匹配"XX写的"、"@username" 模式 | "张三写的"→person="张三" |

**同义词映射表（冷启动数据）：**

| 用户用语 | 系统内部名 |
|---------|-----------|
| 用户表 | users |
| 订单表 | orders |
| 商品表 | products |
| 登录 | 用户登录认证 |
| 支付 | 支付网关 |
| 审批 | 审批流程 |

映射表随线上日志持续扩充，维护机制见查询改写章节。

### 7.4 整体容错链路（端到端）

```
用户输入
    │
    ▼
LLM 意图分类 + 实体抽取 (Haiku, ~500ms)
    │
    ├─ 成功 ──→ 实体完备度评估 ──→ rich/partial/bare 分级处置
    │                                        │
    └─ 失败/超时 ──→ 纯规则分类+抽取 (~5ms)   │
                                              │
                                    ┌─────────┴─────────┐
                                    │ Send API 派发      │
                                    │ (使用实体 or       │
                                    │  原始query兜底)     │
                                    └────────────────────┘
```

**不静默失败的原则：** 当系统用了降级策略（语义搜索兜底、纯规则抽取、上下文继承）时，必须在返回给用户的回答末尾附加一个透明标注：

> ℹ️ 本次查询未识别到具体需求ID或表名，结果基于语义搜索，可能不够精确。[怎样提高搜索精度？](link-to-tips)

### 7.5 实体覆盖面：已知盲区和持续迭代

12 种实体类型是从三类数据源的检索需求中推导出来的。但这不意味着它们覆盖了所有查询。Schema-based 抽取的天然局限是：**你只能抽到预定义过的类型**。

#### 已知不覆盖的查询类型

| 查询类型 | 示例 | 为什么当前实体不覆盖 | 当前兜底策略 |
|---------|------|--------------------|------------|
| **归因/根因分析** | "为什么用户登录突然变慢了？" | 12 种实体里没有"异常现象"或"根因"的载体 | module="用户登录" + 纯语义搜索"登录 变慢 原因"；三源全查 |
| **对比分析** | "v2.3 和 v2.4 的支付流程有什么差异？" | `version` 只存单值，无法表达"两个版本的差异" | version 字段存两个值会被 LLM 误判为单个；当前只能取一个版本，另一个靠用户追问 |
| **假设性/预演分析** | "如果要加 OAuth 登录，需要改哪些地方？" | 实体设计围绕"已存在的东西"，不涉及"计划中的变更" | module="用户登录" + code_refs=["oauth"]；Code Agent 是唯一的有效源 |
| **状态/进度查询** | "REQ-187 的开发进度到哪了？" | 实体不表达任务状态（Jira 状态、CI 状态等），这些数据不在三个源里 | req_ids=["REQ-187"] → Doc Agent 能找到需求内容，但无法回答"进度" |
| **依赖/影响链分析** | "改了 users 表的 email 字段会影响哪些服务？" | 实体抽取能拿到 `table_names` 和 `column_names`，但缺少"下游依赖"的载体 | SQL Agent 返回表结构 + Code Agent 搜索 `users.email` 引用 → 跨源关联有一定效果 |
| **操作指引** | "怎么给订单表建一个索引？" | 既不是查文档也不是查代码，是操作性知识 | 三源全查——Doc Agent 可能搜到 DBA 规范文档，Code Agent 搜到 migration 示例 |
| **概念解释** | "什么是 OAuth2.0 authorization code flow？" | 这甚至不一定是三种数据源里有的内容——可能是外部知识 | 三源全查；如果 PRD 里有相关背景说明就能回答，否则需要承认知识库不覆盖 |
| **多模块耦合分析** | "订单模块和支付模块的耦合点在哪？" | 单个 `module` 字段只能承载一个值 | module 取"订单+支付"；各 Agent 独立搜两个模块的交叉引用 |

#### 怎么评估覆盖面

**上线前（Shadowing 阶段）：** 从 Shadowing 中收集 50-100 条真实用户问题 → 逐条标注 → 统计覆盖率。目标：覆盖率 ≥ 80%。

**上线后（持续监控）：** 每周 review `completeness == "bare"` 的查询 → 人工归因 → 如果同一类"实体覆盖不到"的查询反复出现（一周 ≥ 10 次）→ 新增实体类型。

#### 实体不覆盖 ≠ 系统失败

这是最关键的设计原则：**实体是检索加速器，不是检索闸门。** 即使一条查询匹配零个实体，系统仍然可以工作：

```
实体抽取失败 → bare 处置 → 原始 query 直接做向量检索 → 返回结果
                                                     │
                                               标注"结果基于语义搜索"
```

用户感知到的是"结果不够精准"，而不是"系统拒绝回答"。

### 7.6 反事实推演：去掉实体抽取会怎样

逐 Agent 分析去掉实体抽取的影响：

| Agent | 当前 Recall@10 | 去实体后 | 下降 | 主要损失来源 |
|--------|--------------|---------|------|------------|
| Doc | ~0.88 | ~0.75 | -13pp | req_ids 精确匹配失效 |
| Code | ~0.80 | ~0.55 | -25pp | code_refs 精确grep失效 + module→repo路由失效（全量搜索噪声大） |
| SQL | ~0.85 | ~0.85（Recall） / ~0.50（Accuracy） | Recall不降但准确率暴跌 | 语义搜索能"找到"表但找错表；Recall 虚高 |

**注意 SQL Agent 的陷阱：** 去掉实体后 Recall 可能不降——语义搜索总能返回 N 个相关的表，里面很可能包含正确的那个。但 LLM 从 N 个候选表中选对一个的准确率，跟只有 1 个确定表完全不同。这是最危险的退化模式——指标看起来 OK，用户体验已经崩了。

**结论：** 三个"权重 10"的精确匹配级实体——`req_ids`、`table_names`、`code_refs`——构成了系统的**确定性检索路径**。如果工程资源极度紧张被迫砍功能，保留三个"权重 10"实体 + `module`，其余的可以不做。这四个字段覆盖了 ~70% 的查询场景。

---

## 八、查询改写设计（Round 1）

这是 Query Understanding 的第三个子模块。用户说的话跟系统里存的东西之间存在**词汇鸿沟**——用户说"登录不了"，PRD 里写的是"认证失败"；用户说"用户表"，数据库里是 `users`。查询改写的作用是桥接这个鸿沟。

六种改写方案，解决的问题不同，代价也不同。不是全要——按 Phase 分阶段引入。

### 8.1 方案总览

```
用户原始 query
      │
      ▼
┌──────────────────────────────────────────────────────┐
│               查询改写流水线（可插拔）                   │
│                                                      │
│  [1] 标准化 ──→ [2] 扩展 ──→ [3] 分解 ──→ [4] HyDE  │
│   (~1ms)        (~5ms)       (~800ms)     (~1500ms)  │
│                                                      │
│  从左到右：代价递增，收益也递增                           │
│  标准化和扩展始终开启；分解和 HyDE 按条件触发              │
└──────────────────────────────────────────────────────┘
      │
      ▼
  改写后的 query → 下发给各 Worker Agent
```

### 8.2 方案 1：标准化（Normalization）— 始终开启

**解决的问题：** 用户用语和系统内部名不一致。

**实现：** 一个同义词映射表，不调用 LLM。毫秒级。

**同义词映射表（标准化用）：**

| 类别 | 用户用语 | 标准化表达 |
|------|---------|-----------|
| 表名映射 | 用户表 / 订单表 / 商品表 | users / orders / products |
| 术语映射 | 登录不了 / 挂了 / 慢 / 改需求 | 登录失败 OR 认证失败 OR 认证异常 / 服务不可用 OR 系统异常 OR 宕机 / 性能下降 OR 响应延迟 OR 超时 / 需求变更 OR PRD更新 |
| 模块简称→全称 | 登录 / 支付 / 审批 | 用户登录认证 / 支付网关 / 审批流程 |
| 缩写→全称（SQL） | uid / crt_tm | user_id / created_at |

**标准化处理流程：** 纯规则遍历映射表，对原始 query 中匹配到的用户用语替换为 `(原词 OR 标准化表达)`，不调用 LLM。例如 "用户表挂了" → "(用户表 OR users) (挂了 OR 服务不可用 OR 系统异常 OR 宕机)"。延迟 ~1ms。

#### 映射表维护：人工种子 → 自动发现 → 人工审核闭环

**阶段 1 — 冷启动（上线前）：** 从数据源盘点中一次性提取——数据库 `information_schema` 的表名/列注释、PRD 文档标题/目录、Git 仓库目录结构、人工补充常见口语/缩写。预计 80-110 条映射。

**阶段 2 — 自动发现（上线后）：** 三条管道持续挖掘：
- **管道 A：用户自修正挖掘** — 同一会话内两次查询间隔 < 60s，第一次无交互、第二次有正向交互，语义相似度 > 0.6 → 提取新旧关键词的映射对
- **管道 B：检索落差分析** — 同类查询 Recall@10 差异 > 0.3，高 Recall 查询中存在低 Recall 查询中不存在的关键词
- **管道 C：LLM 批量挖掘（月度离线）** — 扫描低交互查询，让 LLM 建议术语替换

**阶段 3 — 人工审核闭环：**
```
候选映射（置信度 0.7-0.9）──→ 人工审核队列（Web UI）
                               ├─ ✓ 批准 → 立即生效
                               ├─ ✗ 拒绝 → 丢弃
                               └─ ↻ 修改 → 调整后批准

候选映射（置信度 ≥ 0.9）  ──→ 自动生效 + 每周汇总邮件通知
```

#### 防止映射表腐化

每条映射定期做"衰变检查"：近 30 天触发过吗？映射目标还存在吗？用户还在用原词吗？每月自动运行 → 建议清理列表 → 人工确认后删除。

**为什么不全自动化：** 误报会把用户带到错误方向；映射表是代码仓库外的热配置，变更零风险但也意味着没有测试保护——人工审核是唯一的门禁；审核本身就是信号，反复被拒绝的映射可能意味着需要从数据源头解决。

**适用 Agent：** 全部。成本接近于零，始终开启。

### 8.3 方案 2：扩展（Query Expansion）— 始终开启

**解决的问题：** 用户查询太短，缺少上下文词。BM25 和向量检索在短查询上表现都差。

**实现：** LLM 生成 3-5 个相关词/短语，追加到原始查询。

```python
EXPANSION_PROMPT = """
为以下用户查询生成 3-5 个相关的搜索关键词或术语（仅输出关键词列表，用逗号分隔）。
不要改变查询的本意，只是为了提高搜索召回率而补充同义词和相关术语。

查询: {query}

关键词:"""

# 输出: "OAuth2.0, 第三方登录, SSO, token刷新, 认证授权"
```

**代价：** 一次轻量 LLM 调用（Haiku，~300ms），可选缓存（相同 query 24h 内复用扩展结果）。

**适用 Agent：** Doc Agent 和 Code Agent 收益最大；SQL Agent 有标准化就够，扩展容易引入不相关术语干扰 Schema 检索。

**风险：** 扩展过度会引入噪声。对策：限制扩展词数量 ≤ 5，且只在原始 query ≤ 30 字时触发。

### 8.4 方案 3：分解（Multi-Query Decomposition）— 条件触发

**解决的问题：** 一个查询包含多个独立子问题，或跨源查询时不同源需要不同的检索词。

**触发条件：** `is_cross_source == true` 或用户查询中包含"和"、"以及"、"还有"等多问题连接词。

```python
DECOMPOSE_PROMPT = """
将以下复杂查询分解为 2-4 个独立的子查询，每个子查询针对一个特定的数据源或子问题。

数据源:
- doc: PRD文档、产品需求
- code: 代码实现  
- sql: 数据库查询/数据统计

查询: {query}

以 JSON 输出: [{"query": "子查询1", "target": "doc"}, ...]

示例:
"REQ-187改了哪些功能，对应的代码在哪，影响了哪些表？"
→ [
  {"query": "REQ-187 PRD需求变更内容 改了什么功能", "target": "doc"},
  {"query": "REQ-187 相关代码文件 代码变更", "target": "code"},
  {"query": "数据库表 受REQ-187影响 相关数据表", "target": "sql"}
]
"""
```

**代价：** 一次 LLM 调用（Sonnet/Haiku，~500ms），只在跨源查询时触发（预计 ~30% 流量）。

### 8.5 方案 4：HyDE（Hypothetical Document Embeddings）— 条件触发

**解决的问题：** 向量检索的"冷启动"问题——用户的短 query（8 个字）和系统中长文档分块（500 tokens）的嵌入向量不在同一个语义空间里。

**HyDE 的思路：** 让 LLM 先根据用户 query 生成一段假设性答案（hypothetical document），然后用这段假设性答案的 embedding 去检索。假设性答案的风格和长度更接近真实文档，embedding 匹配度更高。

```
传统方式:  "用户登录怎么做的" ──embedding──→ [0.12, -0.34, ...] ──相似度──→ 文档分块
                                                      ↑
                                                 信息量差距大，相似度低

HyDE:      "用户登录怎么做的" ──LLM──→ "用户登录功能通过OAuth2.0协议实现，
                                       用户点击登录按钮后跳转至授权页面..." 
                                              │
                                              ──embedding──→ [0.15, -0.41, ...] ──相似度──→ 文档分块
                                                                    ↑
                                                              风格和长度接近文档，相似度更高
```

**触发条件（三者同时满足）：**
1. 原始 query ≤ 30 字
2. `completeness` 评估为 "partial" 或 "bare"
3. 目标 Worker 是 Doc Agent（Code Agent 和 SQL Agent 不需要——Code Agent 用 grep 不依赖向量检索，SQL Agent 的 Schema RAG 不需要 HyDE）

**并行策略：** HyDE 生成的同时原始 query 的检索也先跑，HyDE 的结果作为补充检索而非替代。两路检索（原始 query + HyDE 假设文档）并行执行，结果通过 RRF 融合合并。

**风险：** HyDE 生成的假设性文档可能"脑补"错误信息，引导检索走向错误方向。对策：始终同时保留原始 query 的检索结果，HyDE 只作为补充信号，不替代原始检索。

### 8.6 方案 5：退一步改写（Step-Back Prompting）— Phase 3+

**解决的问题：** 用户问了一个高度具体的问题，但这个具体问题需要更广泛的背景知识才能正确回答。

```
用户: "oauth.py第42行为什么用refresh_token而不是access_token？"

直接搜: "oauth.py第42行 refresh_token access_token" → 找不到解释"为什么"
退一步: "OAuth token刷新机制的设计原理" → 找到背景知识
         再回答: "因为refresh_token有效期更长，存在服务端更安全..."
```

**触发条件：** 查询包含"为什么"、"原因"、"设计思路"等归因词，且 code_refs 指向了具体文件/行号。

**代价：** 两次 LLM 调用，延迟增加 ~2000ms。Phase 3 再引入。

### 8.7 方案 6：上下文感知改写（Context-Aware Rewriting）— Phase 3+

**解决的问题：** 多轮对话中，用户的追问省略了上下文。

**触发条件：** 查询包含指代词（"它"、"这个"、"那个"）且对话历史非空。

**改写示例：** 上一轮用户问"用户登录的实现"（entities.module="用户登录"，req_ids=["REQ-187"]），本轮追问"它依赖哪些数据库表？" → 改写后："用户登录功能依赖哪些数据库表？"——将指代词替换为上轮上下文中的实体。

**代价：** 本身不增加 LLM 调用，但需要维护会话状态。Phase 3+ 实现。

### 8.8 改写流水线的执行顺序和 Phase 规划

```
                    标准化              扩展               分解              HyDE
用户query ──→  [始终开启]  ──→  [始终开启]  ──→  [跨源时触发]  ──→  [短query+无实体]
               ~1ms            ~300ms           ~500ms           ~1500ms
                 │                │                │                │
                 └────────────────┴────────────────┴────────────────┘
                                    │
                                    ▼
                            改写后的 query 集合
                            (1-N 个，取决于是否分解)
                                    │
                            ┌───────┼───────┐
                            ▼       ▼       ▼
                         Doc A   Code A   SQL A
```

| Phase | 引入方案 | 理由 |
|-------|---------|------|
| Phase 1（SQL Agent） | 标准化 + 扩展 | 标准化解决"用户表→users"映射；扩展补充查询词汇。Text-to-SQL 准确率的基础保障 |
| Phase 2（Doc Agent） | HyDE | PRD 文档长而 query 短，HyDE 的收益最大。延迟问题通过并行检索解决 |
| Phase 2 | 分解 | 跨源查询开始出现（Doc + SQL），分解为两源生成各自的检索词 |
| Phase 3 | 退一步改写 | 用户开始问"为什么"类归因问题，需要背景知识检索 |
| Phase 3 | 上下文感知改写 | 多轮对话场景成熟，用户习惯追问 |

**每个 Phase 的 A/B 对比指标：** Recall@10 变化 + P99 延迟变化。如果新增改写方案延迟增加 > 50% 但 Recall 提升 < 5pp，回滚该方案。

---

## 九、Token 预算管理

Supervisor 作为编排中心，管理跨 Agent 的 Token 预算分配：

| 查询类型 | 预算（LLM调用次数） |
|---------|-------------------|
| 单源简单 | 8 次 |
| 单源复杂 | 12 次 |
| 跨源 | 20 次 |
| 三源全查 | 25 次 |

- Round 1 分类+抽取使用 **Haiku**（~$0.001/次）
- 完备度判断使用 **Haiku**（~$0.001/次）
- 最终回答生成使用 **Sonnet**（~$0.005/次）
- Phase 1-2 使用单 Agent token 硬上限（硬截断）
- Phase 3（Supervisor 上线时）引入跨 Agent 预算分配

---

## 十、收敛判断 Spillover：确定性优先 → LLM 兜底

Supervisor 编排循环中的完备度评估遵循与 Worker Agent 一致的原则：

**确定性收敛优先（代码规则）→ LLM 判断兜底（仅在确定性条件不满足时触发）：**

- 所有 Worker 评分 ≥ 0.6 → 自动收敛（不调 LLM）
- 重调度 ≥ 2 次 → 强制收敛（不调 LLM）
- 确定性条件不满足 → Supervisor 内部 LLM 评估是否调整参数重派（~200ms, Haiku）

---

## 十一、Supervisor 状态存储

| 层级 | 技术 | Phase | 语义 |
|------|------|-------|------|
| **进程内存** | Python dict | Phase 1 | SQL Agent 单进程内循环。无外部依赖 |
| **Redis 热状态** | Redis, TTL=5min | Phase 3+ | Write-through，每次编排状态变更同步写入。Key: `agent:{user_id}:{session_id}:{query_id}:supervisor:state` |
| **Postgres 冷 trace** | PostgreSQL | Phase 3+ | Write-back（查询结束后异步写入），不阻塞编排循环 |

### 降级路径

```
Redis可用 ──→ Agent多轮编排循环（正常）
Redis不可用 ──→ Agent降级为单轮pipeline模式（退化为非Agentic行为）
              → logger.warning("Redis unavailable, falling back to single-pass mode")
```

---

## 十二、Supervisor 专用可观测性指标

| 指标 | 告警阈值 |
|------|---------|
| `supervisor_reschedule_rate` | > 30% → 分类/实体抽取质量或Worker检索质量下降 |
| `supervisor_rounds_p99` | > 3 → 收敛条件过严或Worker返回质量不稳定 |
| `supervisor_quality_score_distribution` | P50 < 0.5 → Worker整体质量不足 |
| `supervisor_timeout_rate` | > 5% → 5s超时设置过紧或Worker延迟恶化 |
| `supervisor_degradation_rate` | > 10% → Redis/LLM基础设施问题 |

---

## 十三、回滚机制

Supervisor 有独立 feature flag，可秒级回退到单次分类+规则模式：

```yaml
agents:
  supervisor_agentic: false  # false=单次分类+规则, true=多轮编排
```

**回滚触发：** 虚假信心率 > 15% OR P99 延迟恶化 > 30% OR Token 成本恶化 > 50%。
