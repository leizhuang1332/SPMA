"""测试 ingestion schemas —— 覆盖 API-05 全部 Pydantic 模型。

测试范围: §3-10 所有请求/响应/配置/产物模型
"""

import pytest
from pydantic import ValidationError

from spma.api.schemas.ingestion import (
    # ── 枚举 ──
    DocIngestionSource,
    # ── 文档摄入 ──
    DocIngestionRequest,
    DocIngestionFilters,
    DocIngestionOptions,
    DocChunkSpec,
    # ── 代码摄入 ──
    CodeIngestionRequest,
    CodeIngestionOptions,
    CodeIngestionOutput,
    FilePathEntry,
    CodeMetadataEntry,
    # ── SQL Schema 摄入 ──
    SchemaIngestionRequest,
    SchemaIngestionOptions,
    SchemaEmbeddingChunk,
    ColumnMeta,
    ForeignKeyMeta,
    BusinessMetadata,
    FewShotQuery,
    # ── 状态查询 ──
    PipelineStatus,
    PipelineRunStatus,
    FreshnessResponse,
    # ── 调度配置 ──
    IngestionSchedule,
    # ── 同义词映射 ──
    SynonymMapRefreshRequest,
    SynonymMapEntry,
    SynonymMapResponse,
)


# ═══════════════════════════════════════════════════════════════════
# 文档摄入 (§3)
# ═══════════════════════════════════════════════════════════════════

class TestDocIngestionRequest:
    def test_defaults_confluence_incremental(self):
        req = DocIngestionRequest()
        assert req.source == DocIngestionSource.CONFLUENCE
        assert req.mode == "incremental"
        assert isinstance(req.filters, DocIngestionFilters)
        assert isinstance(req.options, DocIngestionOptions)

    def test_full_mode(self):
        req = DocIngestionRequest(source="markdown_dir", mode="full")
        assert req.source == DocIngestionSource.MARKDOWN_DIR
        assert req.mode == "full"

    def test_filters_spaces(self):
        req = DocIngestionRequest(
            filters={"spaces": ["PRODUCT", "TECH"]},
        )
        assert req.filters.spaces == ["PRODUCT", "TECH"]

    def test_options_force_reindex(self):
        req = DocIngestionRequest(
            options={"force_full_reindex": True},
        )
        assert req.options.force_full_reindex is True
        assert req.options.re_embed is False

    def test_mode_invalid(self):
        with pytest.raises(ValidationError):
            DocIngestionRequest(mode="invalid_mode")


class TestDocIngestionFilters:
    def test_defaults(self):
        f = DocIngestionFilters()
        assert f.spaces == []
        assert f.updated_since is None
        assert f.doc_types == []
        assert f.max_pages == 500

    def test_max_pages_bounds(self):
        DocIngestionFilters(max_pages=1)
        DocIngestionFilters(max_pages=5000)
        with pytest.raises(ValidationError):
            DocIngestionFilters(max_pages=0)
        with pytest.raises(ValidationError):
            DocIngestionFilters(max_pages=5001)

    def test_iso_date(self):
        f = DocIngestionFilters(updated_since="2026-06-01T00:00:00Z")
        assert f.updated_since == "2026-06-01T00:00:00Z"


class TestDocIngestionOptions:
    def test_defaults(self):
        o = DocIngestionOptions()
        assert o.force_full_reindex is False
        assert o.re_embed is False
        assert o.dry_run is False


class TestDocChunkSpec:
    def test_defaults(self):
        spec = DocChunkSpec()
        assert spec.chunk_size_tokens == 500
        assert spec.overlap_tokens == 50
        assert spec.tokenizer == "tiktoken:cl100k_base"
        assert spec.min_chunk_size_tokens == 100
        assert spec.preserve_metadata is True

    def test_chunk_size_bounds(self):
        DocChunkSpec(chunk_size_tokens=100)
        DocChunkSpec(chunk_size_tokens=2000)
        with pytest.raises(ValidationError):
            DocChunkSpec(chunk_size_tokens=99)
        with pytest.raises(ValidationError):
            DocChunkSpec(chunk_size_tokens=2001)

    def test_custom_separators(self):
        spec = DocChunkSpec(separators=["\n\n", "\n"])
        assert spec.separators == ["\n\n", "\n"]


# ═══════════════════════════════════════════════════════════════════
# 代码摄入 (§4)
# ═══════════════════════════════════════════════════════════════════

class TestCodeIngestionRequest:
    def test_defaults(self):
        req = CodeIngestionRequest()
        assert req.repos == []
        assert req.mode == "incremental"
        assert isinstance(req.options, CodeIngestionOptions)

    def test_specific_repos(self):
        req = CodeIngestionRequest(repos=["auth-service", "payment-service"])
        assert req.repos == ["auth-service", "payment-service"]

    def test_mode_invalid(self):
        with pytest.raises(ValidationError):
            CodeIngestionRequest(mode="invalid")


class TestCodeIngestionOptions:
    def test_defaults(self):
        o = CodeIngestionOptions()
        assert o.update_file_path_cache is True
        assert o.update_code_metadata is True
        assert o.re_parse_ast is False
        assert o.force_full_reclone is False
        assert o.max_repos_parallel == 5

    def test_max_repos_parallel_bounds(self):
        CodeIngestionOptions(max_repos_parallel=1)
        CodeIngestionOptions(max_repos_parallel=20)
        with pytest.raises(ValidationError):
            CodeIngestionOptions(max_repos_parallel=0)
        with pytest.raises(ValidationError):
            CodeIngestionOptions(max_repos_parallel=21)


class TestCodeIngestionOutput:
    def test_empty_output(self):
        o = CodeIngestionOutput(
            file_path_cache=[],
            code_metadata=[],
            working_copies_updated=[],
        )
        assert o.file_path_cache == []
        assert o.code_metadata == []
        assert o.working_copies_updated == []

    def test_with_entries(self):
        o = CodeIngestionOutput(
            file_path_cache=[
                {
                    "repo_name": "auth",
                    "file_path": "src/auth/login.py",
                    "file_type": "py",
                    "updated_at": "2026-06-07T10:00:00Z",
                }
            ],
            code_metadata=[
                {
                    "repo": "auth",
                    "file_path": "src/auth/login.py",
                    "function_name": "login",
                    "class_name": None,
                    "line_start": 10,
                    "line_end": 25,
                    "calls": ["check_password", "create_session"],
                    "called_by": ["handle_request"],
                    "imports": ["hashlib", "jwt"],
                    "req_ids": ["REQ-2024-0187"],
                    "commit_hash": "a1b2c3d4",
                    "updated_at": "2026-06-07T10:00:00Z",
                }
            ],
            working_copies_updated=["auth-service"],
        )
        assert len(o.file_path_cache) == 1
        assert o.file_path_cache[0]["repo_name"] == "auth"
        assert len(o.code_metadata) == 1
        assert o.code_metadata[0]["calls"] == ["check_password", "create_session"]


# ═══════════════════════════════════════════════════════════════════
# SQL Schema 摄入 (§5)
# ═══════════════════════════════════════════════════════════════════

class TestSchemaIngestionRequest:
    def test_defaults(self):
        req = SchemaIngestionRequest()
        assert req.databases == []
        assert req.mode == "incremental"
        assert isinstance(req.options, SchemaIngestionOptions)

    def test_specific_databases(self):
        req = SchemaIngestionRequest(databases=["production_readonly"])
        assert req.databases == ["production_readonly"]

    def test_mode_invalid(self):
        with pytest.raises(ValidationError):
            SchemaIngestionRequest(mode="bad")


class TestSchemaIngestionOptions:
    def test_defaults(self):
        o = SchemaIngestionOptions()
        assert o.include_table_data_samples is False
        assert o.refresh_few_shot_examples is False
        assert o.refresh_enum_definitions is True
        assert o.force_full_introspection is False


class TestSchemaEmbeddingChunk:
    def test_valid_chunk(self):
        chunk = SchemaEmbeddingChunk(
            table_name="users",
            ddl="CREATE TABLE users (id INT PRIMARY KEY);",
            columns=[
                {
                    "column_name": "id",
                    "data_type": "integer",
                    "is_nullable": False,
                    "column_default": None,
                    "comment": "主键",
                    "business_meaning": "用户ID",
                    "enum_values": None,
                    "business_rules": None,
                }
            ],
            foreign_keys=[],
            business_metadata={
                "table_comment": "用户表",
                "business_domain": "用户中心",
                "related_tables": ["orders"],
                "common_queries": ["根据id查询用户"],
                "data_classification": "内部",
            },
            few_shot_queries=[
                {
                    "natural_language": "查询所有活跃用户",
                    "sql": "SELECT * FROM users WHERE active = true;",
                    "business_rules_encoded": ["active = true 表示活跃"],
                    "curated_by": "dba",
                    "curated_at": "2026-06-07T10:00:00Z",
                }
            ],
        )
        assert chunk.table_name == "users"
        assert len(chunk.columns) == 1
        assert chunk.columns[0]["column_name"] == "id"
        assert chunk.business_metadata["business_domain"] == "用户中心"


# ═══════════════════════════════════════════════════════════════════
# 状态查询 (§6)
# ═══════════════════════════════════════════════════════════════════

class TestPipelineStatus:
    def test_valid_pipeline(self):
        ps = PipelineStatus(
            status="healthy",
            last_run_at="2026-06-07T09:55:00Z",
            last_run_status="success",
        )
        assert ps.status == "healthy"

    def test_pipeline_type_specific_fields(self):
        # Doc pipeline 额外字段: last_run_pages_processed, schedule, next_scheduled_full_sync
        ps = PipelineStatus(
            status="healthy",
            last_run_at="2026-06-07T09:55:00Z",
            last_run_status="success",
            last_run_pages_processed=12,
            schedule="webhook + daily 02:00 UTC",
            next_scheduled_full_sync="2026-06-08T02:00:00Z",
        )
        assert ps.last_run_pages_processed == 12

        # Code pipeline 额外字段: repos_indexed, file_path_cache_size_mb
        ps2 = PipelineStatus(
            status="healthy",
            last_run_at="2026-06-07T09:58:00Z",
            last_run_status="success",
            repos_indexed=500,
            file_path_cache_size_mb=38.5,
        )
        assert ps2.repos_indexed == 500

        # SQL pipeline 额外字段: tables_indexed, next_scheduled_run
        ps3 = PipelineStatus(
            status="healthy",
            last_run_at="2026-06-07T09:50:00Z",
            last_run_status="success",
            tables_indexed=245,
            next_scheduled_run="2026-06-07T10:00:00Z",
        )
        assert ps3.tables_indexed == 245


class TestPipelineRunStatus:
    def test_completed_run(self):
        run = PipelineRunStatus(
            pipeline_run_id="ingest-doc-20260607-102345",
            pipeline_type="doc",
            status="completed",
            started_at="2026-06-07T10:23:45Z",
            completed_at="2026-06-07T10:27:30Z",
            duration_seconds=225,
            stats={
                "pages_processed": 85,
                "chunks_generated": 340,
                "embeddings_generated": 340,
                "errors": 2,
                "skipped": 3,
            },
            errors=[
                {
                    "page_id": "12345",
                    "page_title": "已归档: v1.0 PRD",
                    "error": "Page archived -- skipped",
                    "severity": "info",
                }
            ],
        )
        assert run.status == "completed"
        assert run.duration_seconds == 225
        assert len(run.errors) == 1


class TestFreshnessResponse:
    def test_full_freshness(self):
        fr = FreshnessResponse(
            freshness={
                "documents": {
                    "most_recent_update": "2026-06-07T09:55:00Z",
                    "oldest_unindexed_change": None,
                    "within_slo": True,
                    "slo_minutes": 5,
                },
                "code": {
                    "most_recent_update": "2026-06-07T09:58:00Z",
                    "repos_with_pending_changes": 0,
                    "within_slo": True,
                    "slo_minutes": 5,
                },
                "sql_schema": {
                    "most_recent_refresh": "2026-06-07T09:50:00Z",
                    "pending_schema_changes": 0,
                    "within_slo": True,
                    "slo_minutes": 10,
                },
                "synonym_map": {
                    "total_entries": 118,
                    "last_updated": "2026-06-06T18:00:00Z",
                    "pending_review": 5,
                },
            }
        )
        assert fr.freshness["documents"]["within_slo"] is True


# ═══════════════════════════════════════════════════════════════════
# 调度配置 (§9)
# ═══════════════════════════════════════════════════════════════════

class TestIngestionSchedule:
    def test_defaults(self):
        s = IngestionSchedule()
        assert s.doc_webhook_enabled is True
        assert s.doc_full_sync_schedule == "0 2 * * *"
        assert s.max_concurrent_ingestions == 3
        assert s.embedding_batch_size == 32
        assert s.embedding_rate_limit_per_minute == 1000


# ═══════════════════════════════════════════════════════════════════
# 同义词映射表 (§10)
# ═══════════════════════════════════════════════════════════════════

class TestSynonymMapRefreshRequest:
    def test_defaults(self):
        req = SynonymMapRefreshRequest()
        assert req.sources == ["information_schema", "prd_titles", "git_dirs"]
        assert req.auto_apply_high_confidence is True
        assert req.confidence_threshold == 0.9

    def test_custom_sources(self):
        req = SynonymMapRefreshRequest(
            sources=["information_schema"],
            auto_apply_high_confidence=False,
            confidence_threshold=0.8,
        )
        assert req.sources == ["information_schema"]
        assert req.auto_apply_high_confidence is False
        assert req.confidence_threshold == 0.8

    def test_confidence_bounds(self):
        SynonymMapRefreshRequest(confidence_threshold=0.0)
        SynonymMapRefreshRequest(confidence_threshold=1.0)
        with pytest.raises(ValidationError):
            SynonymMapRefreshRequest(confidence_threshold=-0.1)
        with pytest.raises(ValidationError):
            SynonymMapRefreshRequest(confidence_threshold=1.1)


class TestSynonymMapEntry:
    def test_valid_entry(self):
        entry = SynonymMapEntry(
            id=42,
            user_term="用户表",
            canonical_term="users",
            category="table_name",
            source="information_schema",
            confidence=0.95,
            status="active",
            hits_30d=234,
            last_triggered_at="2026-06-07T10:20:00Z",
            created_at="2026-05-15T00:00:00Z",
        )
        assert entry.user_term == "用户表"
        assert entry.confidence == 0.95


class TestSynonymMapResponse:
    def test_with_entries(self):
        resp = SynonymMapResponse(
            total=1,
            entries=[
                {
                    "id": 42,
                    "user_term": "用户表",
                    "canonical_term": "users",
                    "category": "table_name",
                    "source": "information_schema",
                    "confidence": 0.95,
                    "status": "active",
                    "hits_30d": 234,
                    "last_triggered_at": "2026-06-07T10:20:00Z",
                    "created_at": "2026-05-15T00:00:00Z",
                }
            ],
        )
        assert resp.total == 1
        assert resp.entries[0].user_term == "用户表"
