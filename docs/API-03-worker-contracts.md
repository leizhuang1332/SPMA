# API 契约：Worker Agent 输入输出

> 所属项目：[SPMA 全局概览](designs/SPMA-design-00-global-overview.md)
> 契约边界：**Doc Agent / Code Agent / SQL Agent 的内部 I/O Schema**
> 版本：1.0

---

## 一、Agent 状态基类

所有 Worker Agent 共享基础状态模型，通过继承扩展各自特有字段：

```python
from typing import TypedDict, Optional

class AgentState(TypedDict, total=False):
    """所有 Agent 共享的基础状态字段"""
    round: int                                    # 当前轮次
    confidence: float                             # 自评信心 (0-1)
    results: list[dict]                           # 结果列表
    token_used: int                               # 已消耗 token
    assessment_history: list[str]                 # 完备度判断历史
    llm_calls: int                                # LLM 调用次数
    latency_ms: int                               # 累计延迟
```

---

## 二、Doc Agent 契约

### 2.1 收敛参数

| 参数 | 值 | 说明 |
|------|-----|------|
| `max_rounds` | ≤3 | 最大检索轮数 |
| `timeout_ms` | 2000 | 超时（含执行） |
| `convergence_deterministic` | `result_count >= 5 AND has_exact_match == True` | 确定性收敛 |
| `convergence_llm` | Haiku 判断"信息充足" | LLM 兜底 |

### 2.2 状态模型

```python
class DocAgentState(AgentState, total=False):
    """Doc Agent 专属状态"""
    # ── 检索参数 ──
    query: str                                    # 本轮检索 query（可能被改写）
    original_query: str                           # 用户原始问题
    entities: WorkerEntities                      # Supervisor 下发的实体
    
    # ── 检索动作 ──
    action: str                                   # "bm25_vector_search" | "metadata_filter" | "expand_clues"
    
    # ── 检索结果 ──
    bm25_candidates: list[BM25Hit]                # BM25 检索结果 (Top-20)
    vector_candidates: list[VectorHit]            # 向量检索结果 (Top-20)
    fused_results: list[FusedResult]              # RRF 融合后结果 (Top-10)
    
    # ── 检索配置 ──
    weight_mode: str                              # "precise" | "semantic" | "hybrid"（控制 BM25/向量权重）
    
    # ── 完备度 ──
    assessment: str                               # "sufficient" | "insufficient: missing X"
    has_exact_match: bool                         # 是否命中 req_ids 精确匹配
    convergence_reason: str                       # 收敛原因
    
    # ── 约束 ──
    max_rounds: int
    timeout_ms: int
    token_budget: int

class BM25Hit(TypedDict):
    doc_id: str
    chunk_id: int
    rank: int                                     # 在 BM25 结果中的排名
    score: float                                  # BM25 分数
    snippet: str
    metadata: dict

class VectorHit(TypedDict):
    doc_id: str
    chunk_id: int
    rank: int
    score: float                                  # 余弦相似度
    snippet: str
    metadata: dict

class FusedResult(TypedDict):
    doc_id: str
    chunk_id: int
    rrf_score: float
    bm25_rank: int
    vector_rank: int
    snippet: str
    metadata: dict
```

### 2.3 检索动作枚举

```python
from enum import StrEnum

class DocAgentAction(StrEnum):
    BM25_VECTOR_SEARCH = "bm25_vector_search"     # Round 1: 混合检索
    METADATA_FILTER = "metadata_filter"           # req_ids 命中：精确元数据过滤
    EXPAND_CLUES = "expand_clues"                 # Round 2+: 线索扩展重搜
    RETURN_RESULTS = "return_results"             # 返回最终结果
```

### 2.4 混合检索权重配置

```python
from typing import Literal

class DocWeightConfig(TypedDict):
    """按 query_type 分层的 BM25/向量权重"""
    precise: WeightPair    # req_ids 命中 → BM25 主导
    semantic: WeightPair   # 无实体 → 向量主导
    hybrid: WeightPair     # module 命中 → 等权

class WeightPair(TypedDict):
    bm25: float            # 0.0 ~ 1.0
    vector: float          # 0.0 ~ 1.0

# 默认配置（Phase 1 等权，Phase 2 基于数据调优）
DEFAULT_WEIGHTS: DocWeightConfig = {
    "precise":  {"bm25": 0.8, "vector": 0.2},
    "semantic": {"bm25": 0.2, "vector": 0.8},
    "hybrid":   {"bm25": 0.5, "vector": 0.5},
}
```

### 2.5 Doc Agent 的 WorkerOutput 扩展

```python
class DocWorkerOutput(WorkerOutput):
    """Doc Agent 在标准 WorkerOutput 基础上的扩展"""
    worker_type: str = "doc"
    
    # ── 检索方法 ──
    retrieval_method: str                         # "precise_metadata" | "hybrid_search" | "semantic_only"
    weight_mode_used: str                         # 实际使用的权重模式
    
    # ── 检索日志（可观测性） ──
    bm25_top20: list[BM25Hit]                     # BM25 Top-20 快照
    vector_top20: list[VectorHit]                 # 向量 Top-20 快照
    
    # ── 跨源桥接 ──
    discovered_req_ids: list[str]                 # 检索过程中发现的需求 ID
```

---

## 三、Code Agent 契约

### 3.1 收敛参数

| 参数 | 值 | 说明 |
|------|-----|------|
| `max_rounds` | ≤3 | 最大检索轮数 |
| `timeout_ms` | 2000 | 超时（含执行） |
| `convergence_deterministic` | `result_count >= 3 AND (call_depth <= 2 OR new_files_this_round == 0)` | 确定性收敛 |
| `convergence_llm` | Haiku 判断"信息充足" | LLM 兜底 |

### 3.2 状态模型

```python
class SearchTermSet(TypedDict):
    """搜索词集合——搜索词构造管线的产出"""
    exact_terms: list[str]                        # 精确搜索词 (weight ≥ 0.8)
    fuzzy_terms: list[str]                        # 模糊搜索词 (0.4 ≤ weight < 0.8)
    tag_terms: list[str]                          # 标签搜索词 (weight < 0.4)

class CodeAgentState(AgentState, total=False):
    """Code Agent 专属状态"""
    # ── 检索参数 ──
    query: str                                    # 本轮检索 query
    original_query: str                           # 用户原始问题
    entities: WorkerEntities                      # Supervisor 下发的实体
    search_terms: SearchTermSet                   # 搜索词集合
    
    # ── 路由 ──
    candidate_repos: list[str]                    # 文件路径路由后的候选仓库
    route_method: str                             # "file_path_cache" | "repo_registry" | "top_dir_scan"
    route_confidence: str                         # "HIGH" | "MEDIUM" | "LOW"
    
    # ── 检索结果 ──
    ripgrep_results: list[RipgrepHit]             # ripgrep 搜索结果
    expanded_context: list[ExpandedFile]           # AST 调用图扩展结果
    
    # ── 完备度 ──
    assessment: str
    has_exact_match: bool                         # 是否命中 code_refs
    call_depth: int                               # 当前调用链展开深度
    new_files_this_round: int                     # 本轮新增文件数
    convergence_reason: str
    
    # ── 回退 ──
    fallback_layer: int                           # 渐进式回退层数 (0=精确命中, 1-4=各回退层)
    fallback_method: str                          # "none" | "stem_split" | "expanded_repos" | "fuzzy_match" | "llm_retry"
    
    # ── 约束 ──
    max_rounds: int
    timeout_ms: int
    token_budget: int

class RipgrepHit(TypedDict):
    repo: str
    file_path: str
    line_number: int
    match_text: str
    match_type: str                               # "exact" | "stem" | "fuzzy" | "llm_suggested"
    confidence: float                             # 1.0 (精确) → 0.45 (LLM 兜底)

class ExpandedFile(TypedDict):
    repo: str
    file_path: str
    file_content: str                             # read_file 获取的完整文件内容
    imports: list[str]
    calls: list[str]
    called_by: list[str]
    relation_to_seed: str                         # "seed" | "caller" | "callee" | "imported"
    depth: int                                    # 距离种子文件的调用层级
```

### 3.3 检索动作枚举

```python
class CodeAgentAction(StrEnum):
    RIPGREP_SEARCH = "ripgrep"                    # Phase 1: ripgrep 搜索
    READ_FILE = "read_file"                       # Phase 2: 上下文补全
    GLOB_DISCOVERY = "glob"                       # Phase 2: 关联文件发现
    AST_EXPAND = "ast_expand"                     # Phase 2: AST 调用图扩展
    COMPLETENESS_CHECK = "completeness_check"     # 完备度判断
    RETURN_RESULTS = "return_results"
```

### 3.4 搜索词构造管线接口

```python
class CodeTermConstructionResult(TypedDict):
    """搜索词构造管线的输出"""
    search_terms: SearchTermSet
    query_type: str                               # "EXACT_REFS" | "MIXED_CN_EN" | "PURE_CN" | "REQ_TRACE" | "PERSON_TIME"
    translation_method: str                       # "synonym_map" | "module_symbols" | "llm_translate"
    llm_cache_hit: bool
    construction_latency_ms: int

# 搜索词构造入口函数签名
def build_search_terms(
    user_query: str,
    entities: WorkerEntities,
    repo_registry: dict[str, RepoMeta]
) -> CodeTermConstructionResult:
    """从用户问题 + 实体构造代码搜索词集合"""
    ...
```

### 3.5 Code Agent 的 WorkerOutput 扩展

```python
class CodeWorkerOutput(WorkerOutput):
    """Code Agent 在标准 WorkerOutput 基础上的扩展"""
    worker_type: str = "code"
    
    # ── 检索方法 ──
    retrieval_method: str                         # "ripgrep" | "fallback_stem_split" | "fallback_expanded_repos" | "fallback_fuzzy_match" | "fallback_llm_retry"
    route_method: str
    route_confidence: str
    
    # ── 结果类型 ──
    primary_hits: list[RipgrepHit]                # 主要检索命中
    expanded_files: list[ExpandedFile]            # 上下文扩展文件
    glob_discoveries: list[str]                   # glob 发现的关联文件
    
    # ── 跨源桥接 ──
    discovered_req_ids: list[str]                 # git log 中发现的需求 ID
    discovered_table_names: list[str]             # AST 分析发现的表引用
```

---

## 四、SQL Agent 契约

### 4.1 收敛参数

| 参数 | 值 | 说明 |
|------|-----|------|
| `max_rounds` | ≤5 | 最大执行轮数 |
| `timeout_ms` | 3000 | 超时（含执行） |
| `convergence_deterministic` | `execution_success == True AND row_count in [1, 10000]` | 确定性收敛 |
| `convergence_llm` | Haiku 语义验证 | LLM 兜底 |

### 4.2 状态模型

```python
class SQLAgentState(AgentState, total=False):
    """SQL Agent 专属状态"""
    # ── 检索参数 ──
    query: str                                    # 本轮检索 query
    original_query: str                           # 用户原始问题
    entities: WorkerEntities                      # Supervisor 下发的实体
    
    # ── Schema RAG ──
    schema_search_results: list[SchemaHit]         # Schema RAG 检索结果
    business_metadata: dict                        # 业务元数据注入
    
    # ── SQL 生成与校验 ──
    generated_sql: str                            # 本轮生成的 SQL
    guard_result: GuardResult                     # SQL Guard 校验结果
    guard_passed: bool
    
    # ── 执行 ──
    execution_result: QueryResult                 # 执行结果
    execution_success: bool
    row_count: int
    
    # ── 语义验证 ──
    semantic_check: str                           # "passed" | "failed: reason"
    quality_report: QualityReport                 # 结果质量报告
    
    # ── 完备度 ──
    assessment: str
    has_exact_match: bool                         # 是否命中 table_names 精确匹配
    convergence_reason: str
    
    # ── 历史 ──
    sql_history: list[str]                        # 历史 SQL（用于错误反馈）
    
    # ── 约束 ──
    max_rounds: int
    timeout_ms: int
    token_budget: int

class SchemaHit(TypedDict):
    table_name: str
    column_name: Optional[str]
    ddl_snippet: str
    column_comment: Optional[str]
    business_meaning: Optional[str]
    enum_values: Optional[dict]
    business_rules: Optional[str]
    relevance_score: float

class GuardResult(TypedDict):
    passed: bool
    syntax_errors: list[str]                      # SQLGlot 语法错误
    forbidden_operations: list[str]               # 被拦截的 DDL/DML 操作
    table_existence_errors: list[str]             # 不存在的表/列
    performance_warnings: list[str]               # 性能警告（缺失 WHERE、笛卡尔积）
    risk_level: str                               # "low" | "medium" | "high" | "blocked"
    requires_user_confirmation: bool              # 高风险查询需要用户确认

class QueryResult(TypedDict):
    columns: list[str]
    rows: list[list]
    row_count: int
    execution_time_ms: int
    replica_lag_ms: int                           # 只读副本延迟
    data_snapshot_at: str                         # 数据快照时间戳
    sql_executed: str                             # 实际执行的 SQL

class QualityReport(TypedDict):
    issues: list[QualityIssue]
    issue_count: int
    confidence: float                             # 1.0 - (issue_count × 0.2)

class QualityIssue(TypedDict):
    type: str                                     # "empty_result" | "null_ratio" | "outlier" | "time_range"
    column: Optional[str]
    description: str
    severity: str                                 # "info" | "warning" | "error"
```

### 4.3 执行动作枚举

```python
class SQLAgentAction(StrEnum):
    SCHEMA_RAG = "schema_rag"                     # Schema RAG 检索
    GENERATE_SQL = "generate_sql"                 # LLM SQL 生成
    VALIDATE_SQL = "validate_sql"                 # SQL Guard 校验
    EXECUTE_READONLY = "execute_readonly"         # 只读副本执行
    VERIFY_RESULTS = "verify_results"             # 确定性验证（行数范围）
    SEMANTIC_VERIFY = "semantic_verify"           # LLM 语义验证
    RETURN_RESULTS = "return_results"
```

### 4.4 SQL Guard 规则契约

```python
class SQLGuardRules(TypedDict):
    """SQL Guard 的校验规则配置"""
    
    # 语法校验
    syntax_check: bool                            # 默认 True
    sql_dialect: str                              # "postgresql" | "mysql"
    
    # 操作拦截
    forbidden_operations: list[str]               # ["DELETE", "UPDATE", "DROP", "INSERT", "TRUNCATE", "ALTER", "CREATE"]
    
    # 表/列存在性
    validate_table_existence: bool                # 默认 True
    validate_column_existence: bool               # 默认 True
    
    # 性能保护
    max_joins: int                                # 默认 5（超过 5 个 JOIN 警告）
    require_where: bool                           # 默认 True（全表扫描警告）
    require_limit: bool                           # 默认 False（警告但不拦截）
    max_execution_time_ms: int                    # 默认 3000
    
    # 用户确认闸门
    user_confirmation_thresholds: ConfirmationThresholds

class ConfirmationThresholds(TypedDict):
    financial_keywords: list[str]                 # ["金额", "营收", "工资", "费用", "SUM", "AVG"]
    min_joins_for_confirm: int                    # 默认 3
    large_time_range_patterns: list[str]          # ["INTERVAL.*year", "INTERVAL.*month"]
```

### 4.5 SQL Agent 的 WorkerOutput 扩展

```python
class SQLWorkerOutput(WorkerOutput):
    """SQL Agent 在标准 WorkerOutput 基础上的扩展"""
    worker_type: str = "sql"
    
    # ── SQL 信息 ──
    execution_sql: str                            # 最终执行的 SQL（供用户复制复现）
    guard_risk_level: str                         # 风险等级
    user_confirmation_required: bool              # 是否需要用户确认
    user_confirmed: bool                          # 用户是否已确认
    
    # ── 执行元数据 ──
    execution_time_ms: int
    row_count: int
    replica_lag_ms: int
    data_snapshot_at: str
    
    # ── 质量报告 ──
    quality_report: QualityReport
    
    # ── Schema 信息 ──
    tables_used: list[str]                        # 实际使用的表
    columns_used: list[str]                       # 实际使用的列
    
    # ── 数据局限说明 ──
    data_limitations: list[str]                   # ["未排除测试订单", "未进行汇率换算"]
    
    # ── 跨源桥接 ──
    discovered_req_ids: list[str]                 # 表注释中发现的需求 ID
```

---

## 五、Worker 检索日志契约（可观测性）

所有 Worker Agent 在检索过程中生成结构化日志，写入 Kafka/ClickHouse：

```python
class SearchLogEntry(TypedDict, total=False):
    """所有 Worker 共享的检索日志基类"""
    $schema: str                                  # "spma/search_log/1.0"
    log_id: str                                   # UUID
    timestamp: str                                # ISO 8601
    worker_type: str                              # "doc" | "code" | "sql"
    worker_version: str                           # 语义版本
    
    # ── 输入快照 ──
    query_id: str
    query_text: str
    query_type: str                               # "precise" | "semantic" | "hybrid" | "exact_refs" | ...
    trigger: str                                  # "supervisor_dispatch" | "supervisor_reschedule"
    entities: dict                                # 注入的实体
    
    # ── Agent 循环 ──
    agent_rounds: int
    convergence_reason: str
    
    # ── 延迟 ──
    latency_ms: int
    
    # ── 用户反馈（异步填充） ──
    feedback: Optional[dict]
```

Doc/Code/SQL Agent 在此基类上追加各自的检索特有字段（候选集快照、SQL 执行结果等）。详见各 Agent 设计文档中的日志结构定义。
