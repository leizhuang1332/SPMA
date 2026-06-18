"""摄入管理 Schema: IngestionRequest, PipelineStatus 等。

设计依据: API-05 §2-10
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, Optional, TypedDict

from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════════════════════
# 枚举
# ═══════════════════════════════════════════════════════════════════════════════

class DocIngestionSource(StrEnum):
    CONFLUENCE = "confluence"
    MARKDOWN_DIR = "markdown_dir"
    WIKI_API = "wiki_api"


# ═══════════════════════════════════════════════════════════════════════════════
# 文档摄入 (API-05 §3)
# ═══════════════════════════════════════════════════════════════════════════════

class DocIngestionFilters(BaseModel):
    spaces: list[str] = Field(default_factory=list)
    updated_since: Optional[str] = None  # ISO 8601
    doc_types: list[str] = Field(default_factory=list)
    max_pages: int = Field(default=500, ge=1, le=5000)


class DocIngestionOptions(BaseModel):
    force_full_reindex: bool = False
    re_embed: bool = False  # 重新生成 embedding
    dry_run: bool = False


class DocIngestionRequest(BaseModel):
    source: DocIngestionSource = DocIngestionSource.CONFLUENCE
    mode: Literal["incremental", "full"] = "incremental"
    filters: DocIngestionFilters = Field(default_factory=DocIngestionFilters)
    options: DocIngestionOptions = Field(default_factory=DocIngestionOptions)


class DocChunkSpec(BaseModel):
    """PRD 文档分块规格"""
    chunk_size_tokens: int = Field(default=500, ge=100, le=2000)
    overlap_tokens: int = Field(default=50, ge=0, le=200)
    separators: list[str] = Field(
        default=["\n## ", "\n### ", "\n\n", "\n", "。"],
        description="分隔符优先级（从高到低）",
    )
    tokenizer: str = "tiktoken:cl100k_base"
    min_chunk_size_tokens: int = Field(default=100, ge=50)
    preserve_metadata: bool = True  # 保留标题层级、表格结构


# ═══════════════════════════════════════════════════════════════════════════════
# 代码摄入 (API-05 §4)
# ═══════════════════════════════════════════════════════════════════════════════

class CodeIngestionOptions(BaseModel):
    update_file_path_cache: bool = True  # 刷新 file_path_cache 表
    update_code_metadata: bool = True  # 刷新 code_metadata 表
    re_parse_ast: bool = False  # 全量重新 AST 解析
    force_full_reclone: bool = False  # 强制重新 clone
    max_repos_parallel: int = Field(default=5, ge=1, le=20)


class CodeIngestionRequest(BaseModel):
    repos: list[str] = Field(default_factory=list, description="空=全部仓库")
    mode: Literal["incremental", "full"] = "incremental"
    options: CodeIngestionOptions = Field(default_factory=CodeIngestionOptions)


class FilePathEntry(TypedDict):
    repo_name: str
    file_path: str
    file_type: str  # "py", "java", "ts", "yaml", "sql"
    updated_at: str


class CodeMetadataEntry(TypedDict):
    repo: str
    file_path: str
    function_name: Optional[str]
    class_name: Optional[str]
    line_start: int
    line_end: int
    calls: list[str]
    called_by: list[str]
    imports: list[str]
    req_ids: list[str]  # git log 中关联的需求 ID
    commit_hash: str
    updated_at: str


class CodeIngestionOutput(BaseModel):
    """Code Agent 摄入管道的输出结构"""

    # 产物 1: 文件路径缓存
    file_path_cache: list[FilePathEntry]

    # 产物 2: 代码元数据（调用图）
    code_metadata: list[CodeMetadataEntry]

    # 产物 3: Git 工作副本（即时更新）
    working_copies_updated: list[str]


# ═══════════════════════════════════════════════════════════════════════════════
# SQL Schema 摄入 (API-05 §5)
# ═══════════════════════════════════════════════════════════════════════════════

class SchemaIngestionOptions(BaseModel):
    include_table_data_samples: bool = False  # 是否采样表数据（影响性能）
    refresh_few_shot_examples: bool = False  # 刷新 pg_stat_statements 采样
    refresh_enum_definitions: bool = True  # 从代码 AST 刷新枚举值映射
    force_full_introspection: bool = False


class SchemaIngestionRequest(BaseModel):
    databases: list[str] = Field(default_factory=list, description="空=全部数据库")
    mode: Literal["incremental", "full"] = "incremental"
    options: SchemaIngestionOptions = Field(default_factory=SchemaIngestionOptions)


class ColumnMeta(TypedDict):
    column_name: str
    data_type: str
    is_nullable: bool
    column_default: Optional[str]
    comment: Optional[str]  # 列注释
    business_meaning: Optional[str]  # 业务含义（从注释/代码提取）
    enum_values: Optional[dict[str, str]]  # 枚举值映射 {"pending": "待支付", ...}
    business_rules: Optional[str]  # 业务规则描述


class ForeignKeyMeta(TypedDict):
    column_name: str
    referenced_table: str
    referenced_column: str


class BusinessMetadata(TypedDict):
    table_comment: Optional[str]  # 表注释
    business_domain: Optional[str]  # 业务域
    related_tables: list[str]  # 业务上相关的表
    common_queries: list[str]  # 常见查询描述
    data_classification: Optional[str]  # 数据分级（公开/内部/敏感）


class FewShotQuery(TypedDict):
    natural_language: str  # 自然语言问题
    sql: str  # 对应的 SQL
    business_rules_encoded: list[str]  # 这条查询隐含的业务规则
    curated_by: Optional[str]  # 审核人
    curated_at: Optional[str]  # 审核时间


class SchemaEmbeddingChunk(BaseModel):
    """存入 PGVector 的 Schema 嵌入分块结构"""
    table_name: str
    ddl: str  # 完整 DDL（含列定义、约束、索引）
    columns: list[ColumnMeta]  # 列元数据列表
    foreign_keys: list[ForeignKeyMeta]  # 外键关系
    business_metadata: BusinessMetadata  # 业务元数据
    few_shot_queries: list[FewShotQuery]  # few-shot 示例查询


# ═══════════════════════════════════════════════════════════════════════════════
# 摄入状态查询 (API-05 §6)
# ═══════════════════════════════════════════════════════════════════════════════

class PipelineStatus(BaseModel):
    """单条管道状态。

    不同管道类型有各自的可选字段：
    - doc: last_run_pages_processed, schedule, next_scheduled_full_sync
    - code: repos_indexed, file_path_cache_size_mb
    - sql: tables_indexed, next_scheduled_run
    """
    status: str
    last_run_at: str
    last_run_status: str
    # doc 特有
    last_run_pages_processed: Optional[int] = None
    schedule: Optional[str] = None
    next_scheduled_full_sync: Optional[str] = None
    # code 特有
    repos_indexed: Optional[int] = None
    file_path_cache_size_mb: Optional[float] = None
    # sql 特有
    tables_indexed: Optional[int] = None
    next_scheduled_run: Optional[str] = None


class IngestionError(BaseModel):
    page_id: str
    page_title: str
    error: str
    severity: str


class PipelineRunStatus(BaseModel):
    """特定摄入运行状态。"""
    pipeline_run_id: str
    pipeline_type: str
    status: str
    started_at: str
    completed_at: Optional[str] = None
    duration_seconds: Optional[int] = None
    stats: dict = Field(default_factory=dict)
    errors: list[IngestionError] = Field(default_factory=list)


class FreshnessComponent(BaseModel):
    most_recent_update: Optional[str] = None
    oldest_unindexed_change: Optional[str] = None
    within_slo: bool = False
    slo_minutes: int = 0
    # code 特有
    repos_with_pending_changes: Optional[int] = None
    # sql 特有
    most_recent_refresh: Optional[str] = None
    pending_schema_changes: Optional[int] = None
    # synonym_map 特有
    total_entries: Optional[int] = None
    last_updated: Optional[str] = None
    pending_review: Optional[int] = None


class FreshnessResponse(BaseModel):
    """知识新鲜度查询响应。"""
    freshness: dict[str, dict]


# ═══════════════════════════════════════════════════════════════════════════════
# 摄入调度配置 (API-05 §9)
# ═══════════════════════════════════════════════════════════════════════════════

class IngestionSchedule(BaseModel):
    """摄入调度配置"""

    # ── PRD 文档 ──
    doc_webhook_enabled: bool = True
    doc_full_sync_schedule: str = "0 2 * * *"  # 每日凌晨 2:00 UTC
    doc_incremental_delay_seconds: int = 30  # Webhook 后延迟处理（防抖）

    # ── 代码仓库 ──
    code_webhook_enabled: bool = True
    code_incremental_delay_seconds: int = 10  # Webhook 后延迟处理（防抖）

    # ── SQL Schema ──
    sql_polling_interval_seconds: int = 600  # 10 分钟轮询
    sql_polling_enabled: bool = True

    # ── 全局 ──
    max_concurrent_ingestions: int = 3
    embedding_batch_size: int = 32
    embedding_rate_limit_per_minute: int = 1000


# ═══════════════════════════════════════════════════════════════════════════════
# 同义词映射表管理 (API-05 §10)
# ═══════════════════════════════════════════════════════════════════════════════

class SynonymMapRefreshRequest(BaseModel):
    """同义词映射表刷新请求。"""
    sources: list[str] = Field(
        default=["information_schema", "prd_titles", "git_dirs"]
    )
    auto_apply_high_confidence: bool = True
    confidence_threshold: float = Field(default=0.9, ge=0.0, le=1.0)


class SynonymMapEntry(BaseModel):
    id: int
    user_term: str
    canonical_term: str
    category: str
    source: str
    confidence: float
    status: str
    hits_30d: int
    last_triggered_at: str
    created_at: str


class SynonymMapResponse(BaseModel):
    total: int
    entries: list[SynonymMapEntry]
