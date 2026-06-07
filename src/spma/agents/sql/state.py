"""SQL Agent 专属状态定义。"""

from typing import TypedDict

from spma.models.agent_state import AgentState
from spma.models.entities import WorkerEntities


class ColumnMeta(TypedDict):
    column_name: str
    data_type: str
    is_nullable: bool
    comment: str | None
    business_meaning: str | None
    enum_values: dict | None


class ForeignKeyMeta(TypedDict):
    column_name: str
    referenced_table: str
    referenced_column: str


class SchemaHit(TypedDict):
    """Schema RAG 检索命中"""
    table_name: str
    ddl: str
    columns: list[ColumnMeta]
    foreign_keys: list[ForeignKeyMeta]
    business_description: str
    few_shot_queries: list[str]
    relevance_score: float


class GuardResult(TypedDict):
    """SQL Guard 校验结果"""
    passed: bool
    syntax_errors: list[str]
    forbidden_operations: list[str]
    table_existence_errors: list[str]
    performance_warnings: list[str]
    risk_level: str               # "low" | "medium" | "high" | "blocked"
    requires_user_confirmation: bool


class QueryResult(TypedDict):
    """SQL 执行结果"""
    columns: list[str]
    rows: list[list]
    row_count: int
    execution_time_ms: int
    replica_lag_ms: int
    data_snapshot_at: str
    sql_executed: str


class QualityIssue(TypedDict):
    """质量问题"""
    type: str                    # "empty_result" | "null_anomaly" | "outlier" | "stale_data"
    severity: str                # "info" | "warning"
    column: str | None
    detail: str


class QualityReport(TypedDict):
    """结果质量报告"""
    issues: list[QualityIssue]
    issue_count: int
    confidence: float            # 1.0 - (issue_count * 0.2)，最低 0.0
    data_snapshot_at: str
    replica_lag_ms: int


class SQLAgentState(AgentState, total=False):
    """SQL Agent 专属状态字段。"""

    query: str
    original_query: str
    entities: WorkerEntities

    # Schema RAG
    schema_search_results: list[SchemaHit]
    business_metadata: dict

    # SQL 生成与校验
    generated_sql: str
    guard_result: GuardResult
    guard_passed: bool

    # 确认闸门
    confirmation_required: bool
    confirmation_token: str
    confirmation_status: str       # "pending" | "approved" | "modified"

    # 执行
    execution_result: QueryResult
    execution_success: bool
    row_count: int

    # 语义验证
    semantic_check: str            # "passed" | "failed: reason"

    # 质量
    quality_report: QualityReport

    # 循环控制
    sql_history: list[str]
    max_rounds: int
    timeout_ms: int
    current_round: int
    start_time: float
