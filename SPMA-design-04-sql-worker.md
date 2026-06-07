# Design: SQL Agent 设计（Text-to-SQL 执行Agent）

> 所属项目：[SPMA 全局概览](SPMA-design-00-global-overview.md)
> 权威架构：[5独立Agent架构设计](SPMA-design-07-agent-architecture.md) — **如有冲突以此为准**
> 相关模块：[Supervisor Agent](SPMA-design-01-supervisor-agent.md) — 负责通过 Send API 下发检索参数给本 Agent
> 模块职责：作为**执行 Agent**，将自然语言查询转化为安全的 SQL、在只读副本上执行、通过多轮自主循环保障 SQL 语义正确性和数据正确性——Schema RAG → LLM SQL生成 → Guard → 执行 → 语义验证 → 不够 → 重生成

---

## Agent 收敛契约

| 参数 | 值 |
|------|-----|
| **Agent 类型** | 执行 Agent |
| **最大轮数** | ≤5 |
| **收敛条件** | SQL执行成功 AND 行数∈[1,10000] AND 通过语义验证 |
| **超时(含执行)** | 3s |
| **超时策略** | 返回最后成功执行的SQL结果 |
| **确定性收敛** | 执行成功 AND 行数正常 → 自动收敛（不调LLM） |
| **LLM 兜底** | 确定性条件不满足 → Haiku语义验证（~300ms） |

### Agent 循环图

```python
# SQL Agent 独立构建 LangGraph 子图
sql_graph = StateGraph(SQLAgentState)  # 继承 AgentState
sql_graph.add_node("generate", llm_sql_generate)
sql_graph.add_node("guard", sql_guard_check)
sql_graph.add_node("execute", execute_readonly)
sql_graph.add_node("verify", semantic_verify)
sql_graph.add_conditional_edges("verify", should_continue, {
    "regenerate": "generate",  # 语义验证不通过 → 重生成
    "done": END,               # 通过 → 返回
})
```

### Agent 状态数据模型

```python
class SQLAgentState(AgentState):
    """SQL Agent 专属状态"""
    round: int                    # 当前执行轮次
    generated_sql: str            # 本轮生成的SQL
    guard_result: GuardResult     # SQL Guard校验结果
    execution_result: QueryResult # 执行结果
    semantic_check: str           # 语义验证结果 ("passed" | "failed: reason")
    confidence: float             # Agent自评信心 0-1
    has_exact_match: bool         # 是否命中精确表名（table_names非空）
    llm_calls: int                # 本轮LLM调用次数
    latency_ms: int               # 本轮延迟
```

---

## 模块在架构中的位置

```
Supervisor Agent
    │
    │ Send API
    ▼
┌─────────────────────────────────────────┐
│           SQL Agent  ← 本文档范围         │
│  (执行Agent, ≤5轮, 3s超时)               │
│  ┌─────────────────────────────────┐    │
│  │ 多轮执行循环:                     │    │
│  │   Schema RAG                    │    │
│  │   → LLM SQL生成                 │    │
│  │   → SQL Guard 校验              │    │
│  │   → 只读副本执行                 │    │
│  │   → 语义验证                    │    │
│  │   → 不够 → 重生成（最多5轮）      │    │
│  └─────────────────────────────────┘    │
└─────────────────────────────────────────┘
    │
    ├── Doc Agent
    └── Code Agent
```

---

## 一、SQL Guard 层设计（非协商项）

```
用户自然语言问题
      │
      ▼
┌──────────────────┐
│ Schema RAG 检索   │  ← 只检索相关表的 DDL + few-shot 示例
└──────┬───────────┘
       │
       ▼
┌──────────────────┐
│ LLM SQL 生成      │
└──────┬───────────┘
       │
       ▼
┌──────────────────┐
│ SQL Guard 校验    │
│ ✓ 语法校验(SQLGlot)│
│ ✓ DDL/DML 拦截     │  ← 阻止 DELETE/UPDATE/DROP/INSERT/TRUNCATE/ALTER
│ ✓ 表/列存在性验证   │  ← 生成的 SQL 只能引用真实 schema 对象
│ ✓ 性能保护          │  ← 检测缺失 WHERE、笛卡尔积、缺失 LIMIT
└──────┬───────────┘
       │ 失败 → 错误信息反馈 LLM → 重新生成（最多5轮，Agent循环）
       │ 通过
       ▼
┌──────────────────┐
│ 只读副本执行       │  ← 永远不在主库上执行
│ 连接池 + 超时控制  │
└──────┬───────────┘
       │
       ▼
  结果返回 + 格式化
```

---

## 二、SQL 正确性保障：从"语法对"到"语义对"

SQL Guard 只解决了第一层——**语法正确性**。但语法正确的 SQL 可能语义完全错误——查了不该查的表、用错了列、算错了指标。这需要更深的保护层。

### 2.1 两个维度的正确性

```
┌─────────────────────────────────────────────────────────────┐
│  维度1: SQL 执行正确性（"SQL 本身对不对"）                      │
│  ├─ 语法正确（SQL Guard 已覆盖）                               │
│  ├─ 语义正确（查的是用户想查的东西吗？）  ← 当前设计覆盖不足       │
│  └─ 业务正确（符合业务规则和定义吗？）                          │
│                                                             │
│  维度2: 数据正确性（"查出来的数能信吗"）                         │
│  ├─ 数据源正确（查的是正确的表/正确的副本吗？）                   │
│  ├─ 数据本身正确（数据有没有质量问题？）                         │
│  ├─ 结果可解释（用户能看懂这个数字是怎么算出来的吗？）             │
│  └─ 用户理解正确（用户对结果的解读没有偏差吗？）                  │
└─────────────────────────────────────────────────────────────┘
```

---

## 三、维度1 补充：语义正确性——SQL Guard 之上的保护层

### 3.1 保护层 A：业务元数据注入（Schema RAG 增强）

当前 Schema RAG 只检索 DDL。这不够——DDL 告诉你列名是 `status`、类型是 `varchar`，但不告诉你业务含义。需要在检索结果中额外注入三类业务元数据：

```sql
-- 从数据库元数据中提取，存入 PGVector 的 Schema 索引中：
{
  "table": "orders",
  "column": "status",
  "type": "varchar(20)",
  
  -- 以下来自列注释/数据字典（由DBA维护或自动从代码中提取）
  "business_meaning": "订单状态",
  "enum_values": {
    "pending": "待支付",
    "paid": "已支付", 
    "cancelled": "已取消",
    "refunded": "已退款"
  },
  "business_rules": "只有 paid 状态的订单才计入营收",
  
  -- 以下来自外键关系
  "related_tables": ["order_items", "payments"],
  "common_queries": [
    "SELECT status, COUNT(*) FROM orders WHERE created_at >= ? GROUP BY status"
  ]
}
```

**这条信息如何改变 LLM 的 SQL 生成质量：**

```
没有业务元数据:
  用户: "各状态的订单数"
  LLM: SELECT status, COUNT(*) FROM orders GROUP BY status
  问题: status 可能是逻辑删除标记，用户想要的是 order_state
  
有业务元数据:
  用户: "各状态的订单数"  
  LLM 看到: status 的业务含义是"订单状态"，enum_values 是 {待支付,已支付,已取消,已退款}
  LLM 生成: SELECT status, COUNT(*) FROM orders GROUP BY status
  LLM 补充说明: "订单状态包括待支付/已支付/已取消/已退款四种"
  → 用户确认"对，就是这个" ← 即使枚举值映射错误，用户能立即发现
```

**业务元数据的来源：**
- 数据库列注释（`COMMENT ON COLUMN orders.status IS '订单状态: pending/paid/cancelled/refunded'`）——已有，直接提取
- 代码中的 enum 定义（`class OrderStatus(Enum): PENDING = 'pending'`）——用 AST 从代码中提取
- 数据字典/PRD 文档——实体 `column_names` 命中后在 Doc Worker 中搜索相关定义

### 3.2 保护层 B：Few-Shot 示例的质量管理

`pg_stat_statements` 的采样查询被用作 few-shot 示例。但有一个隐蔽问题：

```sql
-- pg_stat_statements 里采样到的查询:
SELECT status, COUNT(*) FROM orders WHERE created_at > '2026-01-01' AND status != 'deleted' GROUP BY status

-- LLM 学到了什么?
-- "查订单状态时加 status != 'deleted'" ← 这是一个隐含的业务规则
-- 如果 LLM 没看到这条示例 → 生成的 SQL 少了这个过滤 → 数据不对
```

**对策：** few-shot 示例需要人工 curator 审核，审核标准：
1. 查询是否加了必要的业务过滤条件（软删除、租户隔离、时间范围）
2. 聚合逻辑是否正确（去重、排除测试数据）
3. 标注"这条查询体现了什么业务规则"

Phase 1 人工 curator 选 20-30 条高频查询作为黄金示例集，之后的审核由 LLM 辅助（标记可疑示例）+ 人工确认。

### 3.3 保护层 C：Agent 循环的语义验证增强

Agent 循环（≤5轮）中每轮不仅反馈语法错误，还把**执行结果的统计特征**也反馈进下一轮：

```python
def enhanced_self_healing(sql: str, error: Exception, result: QueryResult) -> str:
    """增强自修复：不只反馈错误，还反馈结果的统计异常"""
    
    feedback = []
    
    # 原有: 语法/执行错误
    if error:
        feedback.append(f"SQL执行错误: {error}")
    
    # 新增: 结果统计异常检测
    if result:
        if result.row_count == 0:
            feedback.append("查询返回0行。可能原因: 过滤条件过严、时间范围不对、表为空")
        if result.row_count > 100000:
            feedback.append(f"查询返回{result.row_count}行，可能缺少聚合或过滤条件")
        if result.has_null_columns:
            feedback.append(f"以下列存在大量NULL值: {result.null_columns}")
        if result.has_unexpected_distribution:
            feedback.append(f"分组分布异常: {result.distribution_summary}")
    
    return "\n".join(feedback)
```

### 3.4 保护层 D：用户确认闸门（高风险查询）

不是所有 SQL 都需要用户确认。但以下情况需要在执行前展示 SQL 等用户确认：

```python
def needs_user_confirmation(sql: str, entities: ExtractedEntities) -> bool:
    """判断是否需要用户确认"""
    HIGH_RISK_PATTERNS = [
        # 涉及金额/财务指标
        (r'\b(sum|avg|count)\b.*\b(amount|price|revenue|salary|工资|金额|营收)\b', "涉及财务指标"),
        # 全表聚合（可能很慢）
        (r'COUNT\(\*\)|SUM\(.*\).*FROM.*WHERE\s*$', "全表扫描/聚合"),
        # 跨多表JOIN
        (r'JOIN.*JOIN.*JOIN', "三个以上表关联"),
        # 时间范围异常（查了10年数据）
        (r"INTERVAL\s+'?\s*(\d+)\s*(year|month)", "时间范围异常"),
    ]
    
    for pattern, reason in HIGH_RISK_PATTERNS:
        if re.search(pattern, sql, re.IGNORECASE):
            return True
    return False
```

确认时展示：
```
即将执行以下SQL:
  SELECT SUM(amount) FROM orders WHERE status = 'paid' AND created_at >= '2026-01-01'

涉及表: orders (订单表)
时间范围: 2026-01-01 至今
预计返回: 1 行
风险提示: 涉及财务指标聚合

[确认执行] [修改查询]
```

---

## 四、维度2：数据正确性——查出来的数能信吗

即使 SQL 完全正确，查出来的数据也可能有问题。

### 4.1 数据同步延迟

```
用户: "今天的订单数"
SQL:  SELECT COUNT(*) FROM orders WHERE created_at >= '2026-06-05'
执行在: 只读副本（可能有 2-10 秒复制延迟）
结果: 比实际少了最近几秒的订单
```

**对策：** 在结果中标注数据新鲜度：
```
查询结果: 1,247 笔订单
数据来源: orders 表（只读副本，延迟 ~3秒）
数据截止: 2026-06-05 14:32:18
```

从 `pg_last_xact_replay_timestamp()` 获取。

### 4.2 数据质量问题

即使主库数据也未必干净——测试数据混入生产、NULL 值的默认处理不一致、软删除标记不统一。

**对策：** 执行后结果质量检测：

```python
def result_quality_check(result: QueryResult, sql: str) -> QualityReport:
    """对查询结果做基本的质量扫描"""
    issues = []
    
    # 检查1: 空结果
    if result.row_count == 0:
        issues.append("查询返回0行。如果这是非预期的，可能原因: (1)过滤条件过严 (2)时间范围不对 (3)表名选错")
    
    # 检查2: NULL 比例异常
    for col in result.columns:
        null_ratio = result.null_count(col) / result.row_count
        if null_ratio > 0.5:
            issues.append(f"列 {col} 的 NULL 值占比 {null_ratio:.0%}，可能影响聚合结果")
    
    # 检查3: 数值列异常值（简单统计）
    for col in result.numeric_columns:
        stats = result.basic_stats(col)
        if stats.max > stats.p99 * 10:  # 最大值是P99的10倍
            issues.append(f"列 {col} 存在极端值: max={stats.max}, p99={stats.p99}")
    
    # 检查4: 时间范围是否合理
    if "created_at" in sql.lower() or "date" in sql.lower():
        time_span = result.time_span()
        if time_span and time_span.days > 365:
            issues.append(f"查询覆盖了 {time_span.days} 天的数据，可能超出预期范围")
    
    return QualityReport(issues=issues, confidence=1.0 - len(issues)*0.2)
```

质量问题不阻塞返回——结果照样展示，但在结果下方附加质量标注，让用户自行判断。

### 4.3 不可复现性

同一个问题在不同时间问可能得到不同答案（因为数据在变化）。

**对策：** 
- 查询结果带时间戳和快照标识
- 返回的 SQL 可以直接复制到数据库客户端复现（透明性）
- 高频查询结果缓存时标注"该结果基于 {timestamp} 的数据快照"

### 4.4 用户误解结果

系统通过 **over-communication** 把不确定性暴露出来：

```
查询结果: ¥847,230.00

⚠️ 数据质量提示:
- 此查询基于 orders 表，包含所有 status='paid' 的订单
- 未排除可能存在的测试订单（user_id < 100 的订单）
- 未进行汇率换算
- 如需更精确的财务数据，请确认: (1)是否排除内部测试订单 (2)是否需要汇率换算
```

---

## 五、端到端保障全景图（Agent 循环版）

```
用户自然语言（Supervisor Send API 触发 Agent）
      │
      ▼
┌─────────────────────────────────────────────────────────┐
│              SQL Agent 循环（≤5轮, 3s超时）               │
│                                                         │
│  Round N:                                               │
│    实体抽取 (table_names, metrics, time_range, group_by) │
│          │                                               │
│          ▼                                               │
│    Schema RAG 检索 ─── 增强注入: DDL + 业务元数据         │
│          │                                               │
│          ▼                                               │
│    LLM SQL 生成 ─── 输入含: 列的业务含义、枚举值映射       │
│          │                                               │
│          ▼                                               │
│    SQL Guard 校验 ─── 语法、安全性、表/列真实性、性能      │
│          │                                               │
│          ├─ 失败 → 反馈LLM重生成（Agent循环下一轮）         │
│          │                                               │
│          ▼ 通过                                           │
│    高风险? ─── 是 → 用户确认闸门（展示SQL + 风险提示）      │
│          │                                               │
│          │ 否/已确认                                      │
│          ▼                                               │
│    只读副本执行 ─── 记录: 执行时间、副本延迟、数据快照标识   │
│          │                                               │
│          ▼                                               │
│    语义验证:                                              │
│    ├─ 行数∈[1,10000] AND 无统计异常 → 收敛 ✓              │
│    └─ 异常 → 反馈LLM重生成（Agent循环下一轮，最多5轮）      │
│                                                         │
└─────────────────────────────────────────────────────────┘
      │ 收敛
      ▼
结果返回 ─── 附带: SQL原文、数据新鲜度、质量标注、业务局限提示
```

---

## 六、实体驱动的检索策略

### 实体用法详表

| 实体 | 用法 | 优先级 | 示例 |
|------|------|-------|------|
| `table_names` | 精确 Schema 检索——跳过语义搜索，直接查 DDL 和列注释 | **最高** | `SELECT column_name, data_type FROM information_schema.columns WHERE table_name IN ('users','orders')` |
| `column_names` | 列级检索——在 Schema RAG 中精确定位列的语义描述 | 高 | 搜 `status` 列的业务含义（可能有枚举值映射） |
| `metrics` | 聚合函数构造——决定 SELECT 子句 | 高 | `"订单数"` → `COUNT(order_id)`, `"订单总额"` → `SUM(amount)` |
| `time_range` | WHERE 条件——自然语言时间转 SQL 日期范围 | 高 | `"过去7天"` → `WHERE created_at >= NOW() - INTERVAL '7 days'` |
| `group_by` | GROUP BY 构造——自然语言分组转 SQL | 中 | `"按状态"` → `GROUP BY status` |
| `module` | Schema RAG 语义搜索——当 `table_names` 为空时用 module 语义检索相关表 | 中 | `"用户登录"` → 检索 `users`, `user_sessions`, `oauth_states` 表 |

### 有实体 vs 无实体的流程对比

```
有实体: "过去7天各状态的订单数"
  → table_names=["orders"] → SELECT column_name FROM information_schema WHERE table_name='orders'
  → 拿到全部列名和注释 → metrics="订单数" → LLM 生成: SELECT status, COUNT(*) FROM orders WHERE created_at >= NOW()-7d GROUP BY status
  → 路径: 确定性查找 → 精确SQL生成
         [延迟 ~800ms, 准确率 ~85%]

无实体: "过去7天各状态的订单数"
  → 整句 embedding → Schema RAG 搜索 → 可能返回: ["orders", "order_items", "order_logs", "user_orders_view"]
  → LLM 从四个候选表中选出正确的 → 生成 SQL → SQL Guard 校验 → 失败 → 自修复 → ...
  → 路径: 模糊检索 → 猜测表 → 可能选错表 → 自修复兜底
         [延迟 ~2500ms, 准确率 ~55%]
```

**核心差异不是延迟，而是"表名选错"这个失败模式。** Schema RAG 用"过去7天各状态的订单数"去搜，返回的前 5 个表里如果 `orders` 排第二而不是第一，LLM 可能选了 `order_items` 来生成 SQL。SQL Guard 能校验语法，但校验不了"你选错表了"。

---

## 七、反事实分析：去掉实体对 SQL Worker 的影响

> **量化估计：** 去掉实体后，SQL Worker 的端到端 Execution Accuracy 从 ~80% 下降到 ~50-55%。不是语义搜索不够好，而是数据库 schema 根本没提供足够的语义信号让搜索工作。

**注意陷阱：** 去掉实体后 Recall 可能不降——语义搜索总能返回 N 个相关的表，里面很可能包含正确的那个。但 LLM 从 N 个候选表中选对一个的准确率，跟只有 1 个确定表完全不同。Recall 看起来还行，用户拿到的答案却是错的。这是最危险的退化模式——指标看起来 OK，用户体验已经崩了。

---

## 九、Agent Action Guard & Worker 输出 & 回滚机制

### Agent Action Guard

SQL Agent 可调用的工具受白名单限制：

```python
ALLOWED_ACTIONS = {
    'sql': ['schema_rag', 'generate_sql', 'validate_sql', 'execute_readonly',
            'verify_results', 'semantic_verify', 'return_results'],
}
```

### WorkerOutput 格式

SQL Agent 返回给 Supervisor 的输出遵循标准 WorkerOutput 格式：

```python
class WorkerOutput:
    worker_type: str = "sql"
    result_count: int          # 返回的行数
    results: list[dict]        # SQL查询结果
    citations: list[Citation]  # 每条结果的引用元数据
    confidence: float          # Agent自评信心 (0-1)
    has_exact_match: bool      # 是否命中精确表名（table_names非空）
    rounds_used: int           # 使用的执行轮数
    original_query: str        # 原始检索query
    execution_sql: str         # 最终执行的SQL（供用户复制复现）
```

### 回滚机制

SQL Agent 有独立 feature flag，可秒级回退到当前3轮自修复模式：

```yaml
agents:
  sql_agentic: false  # false=当前3轮自修复, true=agentic语义验证循环（≤5轮）
```

**回滚触发：** 虚假信心率 > 15% OR P99 延迟恶化 > 30% OR Token 成本恶化 > 50%。

---

## 十、数据摄入（SQL Agent 视角）

```
SQL 数据库 (PostgreSQL/MySQL)
  → Schema 自省（表结构、列类型、外键关系、列注释作为业务语义描述）
  → DDL + pg_stat_statements 采样（Top 100 高频查询作为 few-shot 示例；需 DBA 配合开通 pg_stat_statements 扩展）
  → BGE-M3 嵌入 → PGVector
  → 触发方式：定时轮询 information_schema（10min 间隔）+ 手动触发刷新（DDL 变更后）
```

> 完整的数据摄入管道设计见 [数据摄入管道设计](SPMA-design-05-data-ingestion.md)。
