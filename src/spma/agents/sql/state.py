"""SQL Agent 专属状态定义。"""

from spma.models.agent_state import AgentState
from spma.models.entities import WorkerEntities


class SchemaHit(dict):
    """Schema RAG 检索命中: table_name, ddl_snippet, column_comment, business_meaning, enum_values, business_rules, relevance_score"""
    pass


class GuardResult(dict):
    """SQL Guard 校验结果: passed, syntax_errors, forbidden_operations, table_existence_errors, performance_warnings, risk_level, requires_user_confirmation"""
    pass


class QueryResult(dict):
    """SQL 执行结果: columns, rows, row_count, execution_time_ms, replica_lag_ms, data_snapshot_at, sql_executed"""
    pass


class QualityReport(dict):
    """结果质量报告: issues(list), issue_count, confidence"""
    pass


class QualityIssue(dict):
    """质量问题: type, column, description, severity"""
    pass


class SQLAgentState(AgentState, total=False):
    """SQL Agent 专属状态字段。"""

    query: str
    original_query: str
    entities: WorkerEntities
    schema_search_results: list[SchemaHit]
    business_metadata: dict
    generated_sql: str
    guard_result: GuardResult
    guard_passed: bool
    execution_result: QueryResult
    execution_success: bool
    row_count: int
    semantic_check: str
    quality_report: QualityReport
    assessment: str
    sql_history: list[str]
    max_rounds: int
    timeout_ms: int
    token_budget: int
