# API 契约：数据摄入管道

> 所属项目：[SPMA 全局概览](designs/SPMA-design-00-global-overview.md)
> 相关设计：[数据摄入管道设计](designs/SPMA-design-05-data-ingestion.md)
> 契约边界：**外部触发器 ↔ 摄入管道 ↔ PGVector/Redis/PostgreSQL**
> 版本：1.0

---

## 一、摄入管道架构

```
┌─────────────────────────────────────────────────────────────────┐
│                     数据摄入管道契约边界                           │
│                                                                 │
│  外部触发器                   摄入管道                 存储层      │
│  ┌──────────┐    Webhook    ┌──────────────┐    write   ┌──────┐ │
│  │Confluence│ ────────────► │              │ ─────────► │PGVect│ │
│  └──────────┘               │              │            └──────┘ │
│  ┌──────────┐    Webhook    │ Doc Pipeline │                     │
│  │   Git    │ ────────────► │              │            ┌──────┐ │
│  └──────────┘               │ Code Pipeline│ ─────────► │Redis │ │
│  ┌──────────┐    轮询       │              │            └──────┘ │
│  │PostgreSQL│ ◄───────────  │ SQL Pipeline │                     │
│  │  /MySQL  │               │              │            ┌──────┐ │
│  └──────────┘               └──────┬───────┘ ─────────► │Postgr│ │
│                                    │                     └──────┘ │
│                           ┌───────▼────────┐                      │
│                           │ 管理 API / UI  │                      │
│                           │ (手动触发)     │                      │
│                           └────────────────┘                      │
└─────────────────────────────────────────────────────────────────┘
```

---

## 二、摄入管理 REST API

### 2.1 端点总览

| 方法 | 路径 | 说明 | 认证 |
|------|------|------|------|
| `POST` | `/api/v1/ingest/documents` | 手动触发 PRD 文档摄入 | Admin |
| `POST` | `/api/v1/ingest/code` | 手动触发代码仓库摄入 | Admin |
| `POST` | `/api/v1/ingest/schema` | 手动触发 SQL Schema 摄入 | Admin |
| `GET` | `/api/v1/ingest/status` | 查询摄入管道状态 | Admin |
| `GET` | `/api/v1/ingest/status/{pipeline_run_id}` | 查询特定摄入运行状态 | Admin |
| `GET` | `/api/v1/ingest/freshness` | 查询知识新鲜度 | Authenticated |
| `POST` | `/api/v1/ingest/synonym-map/refresh` | 刷新同义词映射表 | Admin |

---

## 三、文档摄入 API

### 3.1 手动触发摄入

```
POST /api/v1/ingest/documents
Content-Type: application/json
Authorization: Bearer <JWT_ADMIN>
```

```json
{
  "source": "confluence",
  "mode": "incremental",
  "filters": {
    "spaces": ["PRODUCT", "TECH"],
    "updated_since": "2026-06-01T00:00:00Z",
    "doc_types": ["PRD", "技术方案"],
    "max_pages": 500
  },
  "options": {
    "force_full_reindex": false,
    "re_embed": false,
    "dry_run": false
  }
}
```

**Pydantic 模型：**

```python
from pydantic import BaseModel, Field
from typing import Literal, Optional
from enum import StrEnum

class DocIngestionSource(StrEnum):
    CONFLUENCE = "confluence"
    MARKDOWN_DIR = "markdown_dir"
    WIKI_API = "wiki_api"

class DocIngestionRequest(BaseModel):
    source: DocIngestionSource = DocIngestionSource.CONFLUENCE
    mode: Literal["incremental", "full"] = "incremental"
    filters: DocIngestionFilters = Field(default_factory=DocIngestionFilters)
    options: DocIngestionOptions = Field(default_factory=DocIngestionOptions)

class DocIngestionFilters(BaseModel):
    spaces: list[str] = Field(default_factory=list)
    updated_since: Optional[str] = None  # ISO 8601
    doc_types: list[str] = Field(default_factory=list)
    max_pages: int = Field(default=500, ge=1, le=5000)

class DocIngestionOptions(BaseModel):
    force_full_reindex: bool = False
    re_embed: bool = False       # 重新生成 embedding
    dry_run: bool = False
```

### 3.2 响应

```json
{
  "pipeline_run_id": "ingest-doc-20260607-102345",
  "source": "confluence",
  "mode": "incremental",
  "status": "running",
  "started_at": "2026-06-07T10:23:45Z",
  "estimated_completion": "2026-06-07T10:28:00Z",
  "stats": {
    "pages_found": 1250,
    "pages_to_process": 85,
    "pages_skipped": 1165
  }
}
```

### 3.3 文档分块契约

```python
class DocChunkSpec(BaseModel):
    """PRD 文档分块规格"""
    chunk_size_tokens: int = Field(default=500, ge=100, le=2000)
    overlap_tokens: int = Field(default=50, ge=0, le=200)
    separators: list[str] = Field(
        default=["\n## ", "\n### ", "\n\n", "\n", "。"],
        description="分隔符优先级（从高到低）"
    )
    tokenizer: str = "tiktoken:cl100k_base"
    min_chunk_size_tokens: int = Field(default=100, ge=50)
    preserve_metadata: bool = True  # 保留标题层级、表格结构
```

---

## 四、代码摄入 API

### 4.1 手动触发摄入

```
POST /api/v1/ingest/code
Content-Type: application/json
Authorization: Bearer <JWT_ADMIN>
```

```json
{
  "repos": ["auth-service", "payment-service"],
  "mode": "incremental",
  "options": {
    "update_file_path_cache": true,
    "update_code_metadata": true,
    "re_parse_ast": false,
    "force_full_reclone": false,
    "max_repos_parallel": 5
  }
}
```

```python
class CodeIngestionRequest(BaseModel):
    repos: list[str] = Field(default_factory=list, description="空=全部仓库")
    mode: Literal["incremental", "full"] = "incremental"
    options: CodeIngestionOptions = Field(default_factory=CodeIngestionOptions)

class CodeIngestionOptions(BaseModel):
    update_file_path_cache: bool = True   # 刷新 file_path_cache 表
    update_code_metadata: bool = True     # 刷新 code_metadata 表
    re_parse_ast: bool = False            # 全量重新 AST 解析
    force_full_reclone: bool = False      # 强制重新 clone
    max_repos_parallel: int = Field(default=5, ge=1, le=20)
```

### 4.2 响应

```json
{
  "pipeline_run_id": "ingest-code-20260607-102345",
  "mode": "incremental",
  "status": "running",
  "repos_processed": 2,
  "stats": {
    "total_files_indexed": 45230,
    "new_files": 125,
    "updated_files": 38,
    "deleted_files": 5,
    "ast_functions_parsed": 152300,
    "file_path_cache_size_mb": 38.5
  }
}
```

### 4.3 代码摄入产物契约

```python
class CodeIngestionOutput(BaseModel):
    """Code Agent 摄入管道的输出结构"""
    
    # 产物 1: 文件路径缓存
    file_path_cache: list[FilePathEntry]
    
    # 产物 2: 代码元数据（调用图）
    code_metadata: list[CodeMetadataEntry]
    
    # 产物 3: Git 工作副本（即时更新）
    working_copies_updated: list[str]

class FilePathEntry(TypedDict):
    repo_name: str
    file_path: str
    file_type: str                               # "py", "java", "ts", "yaml", "sql"
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
    req_ids: list[str]                            # git log 中关联的需求 ID
    commit_hash: str
    updated_at: str
```

---

## 五、SQL Schema 摄入 API

### 5.1 手动触发摄入

```
POST /api/v1/ingest/schema
Content-Type: application/json
Authorization: Bearer <JWT_ADMIN>
```

```json
{
  "databases": ["production_readonly"],
  "mode": "incremental",
  "options": {
    "include_table_data_samples": false,
    "refresh_few_shot_examples": false,
    "refresh_enum_definitions": true,
    "force_full_introspection": false
  }
}
```

```python
class SchemaIngestionRequest(BaseModel):
    databases: list[str] = Field(default_factory=list, description="空=全部数据库")
    mode: Literal["incremental", "full"] = "incremental"
    options: SchemaIngestionOptions = Field(default_factory=SchemaIngestionOptions)

class SchemaIngestionOptions(BaseModel):
    include_table_data_samples: bool = False  # 是否采样表数据（影响性能）
    refresh_few_shot_examples: bool = False   # 刷新 pg_stat_statements 采样
    refresh_enum_definitions: bool = True     # 从代码 AST 刷新枚举值映射
    force_full_introspection: bool = False
```

### 5.2 响应

```json
{
  "pipeline_run_id": "ingest-schema-20260607-102345",
  "mode": "incremental",
  "status": "running",
  "stats": {
    "databases_scanned": 1,
    "tables_total": 245,
    "tables_new": 3,
    "tables_modified": 12,
    "columns_total": 3120,
    "enum_definitions_updated": 28,
    "few_shot_examples_count": 100
  }
}
```

### 5.3 Schema RAG 嵌入内容契约

```python
class SchemaEmbeddingChunk(TypedDict):
    """存入 PGVector 的 Schema 嵌入分块结构"""
    table_name: str
    ddl: str                                      # 完整 DDL（含列定义、约束、索引）
    columns: list[ColumnMeta]                     # 列元数据列表
    foreign_keys: list[ForeignKeyMeta]             # 外键关系
    business_metadata: BusinessMetadata           # 业务元数据
    few_shot_queries: list[FewShotQuery]           # few-shot 示例查询

class ColumnMeta(TypedDict):
    column_name: str
    data_type: str
    is_nullable: bool
    column_default: Optional[str]
    comment: Optional[str]                         # 列注释
    business_meaning: Optional[str]                # 业务含义（从注释/代码提取）
    enum_values: Optional[dict[str, str]]          # 枚举值映射 {"pending": "待支付", ...}
    business_rules: Optional[str]                  # 业务规则描述

class ForeignKeyMeta(TypedDict):
    column_name: str
    referenced_table: str
    referenced_column: str

class BusinessMetadata(TypedDict):
    table_comment: Optional[str]                   # 表注释
    business_domain: Optional[str]                 # 业务域
    related_tables: list[str]                      # 业务上相关的表
    common_queries: list[str]                      # 常见查询描述
    data_classification: Optional[str]             # 数据分级（公开/内部/敏感）

class FewShotQuery(TypedDict):
    natural_language: str                          # 自然语言问题
    sql: str                                       # 对应的 SQL
    business_rules_encoded: list[str]              # 这条查询隐含的业务规则
    curated_by: Optional[str]                      # 审核人
    curated_at: Optional[str]                      # 审核时间
```

---

## 六、摄入状态查询 API

### 6.1 全局摄入状态

```
GET /api/v1/ingest/status
Authorization: Bearer <JWT_ADMIN>
```

```json
{
  "pipelines": {
    "doc": {
      "status": "healthy",
      "last_run_at": "2026-06-07T09:55:00Z",
      "last_run_status": "success",
      "last_run_pages_processed": 12,
      "schedule": "webhook + daily 02:00 UTC",
      "next_scheduled_full_sync": "2026-06-08T02:00:00Z"
    },
    "code": {
      "status": "healthy",
      "last_run_at": "2026-06-07T09:58:00Z",
      "last_run_status": "success",
      "repos_indexed": 500,
      "schedule": "git webhook (push event)",
      "file_path_cache_size_mb": 38.5
    },
    "sql": {
      "status": "healthy",
      "last_run_at": "2026-06-07T09:50:00Z",
      "last_run_status": "success",
      "tables_indexed": 245,
      "schedule": "every 10min + manual trigger",
      "next_scheduled_run": "2026-06-07T10:00:00Z"
    }
  },
  "freshness": {
    "doc_oldest_chunk": "2026-06-05T10:00:00Z",
    "code_oldest_cache": "2026-06-07T09:58:00Z",
    "sql_schema_last_refresh": "2026-06-07T09:50:00Z"
  }
}
```

### 6.2 特定运行状态

```
GET /api/v1/ingest/status/ingest-doc-20260607-102345
```

```json
{
  "pipeline_run_id": "ingest-doc-20260607-102345",
  "pipeline_type": "doc",
  "status": "completed",
  "started_at": "2026-06-07T10:23:45Z",
  "completed_at": "2026-06-07T10:27:30Z",
  "duration_seconds": 225,
  "stats": {
    "pages_processed": 85,
    "chunks_generated": 340,
    "embeddings_generated": 340,
    "errors": 2,
    "skipped": 3
  },
  "errors": [
    {
      "page_id": "12345",
      "page_title": "已归档: v1.0 PRD",
      "error": "Page archived — skipped",
      "severity": "info"
    }
  ]
}
```

---

## 七、知识新鲜度查询 API

```
GET /api/v1/ingest/freshness
Authorization: Bearer <JWT>
```

```json
{
  "freshness": {
    "documents": {
      "most_recent_update": "2026-06-07T09:55:00Z",
      "oldest_unindexed_change": null,
      "within_slo": true,
      "slo_minutes": 5
    },
    "code": {
      "most_recent_update": "2026-06-07T09:58:00Z",
      "repos_with_pending_changes": 0,
      "within_slo": true,
      "slo_minutes": 5
    },
    "sql_schema": {
      "most_recent_refresh": "2026-06-07T09:50:00Z",
      "pending_schema_changes": 0,
      "within_slo": true,
      "slo_minutes": 10
    },
    "synonym_map": {
      "total_entries": 118,
      "last_updated": "2026-06-06T18:00:00Z",
      "pending_review": 5
    }
  }
}
```

---

## 八、Webhook 契约（外部触发器）

### 8.1 Confluence Webhook

```
POST /api/v1/webhooks/confluence
Content-Type: application/json
X-Confluence-Webhook-Token: <SHARED_SECRET>
```

```json
{
  "event": "page_updated",
  "page_id": "12345678",
  "space_key": "PRODUCT",
  "title": "用户登录模块 PRD v2.4",
  "version": 12,
  "updated_by": "leizhuang1332",
  "updated_at": "2026-06-07T10:23:45Z",
  "url": "https://confluence.internal.company.com/display/PRODUCT/User+Login+PRD"
}
```

### 8.2 Git Webhook（GitHub/GitLab/Gitea）

```
POST /api/v1/webhooks/git
Content-Type: application/json
X-Hub-Signature-256: sha256=<HMAC>
X-GitHub-Event: push
```

```json
{
  "repository": {
    "name": "auth-service",
    "clone_url": "git@git.internal.company.com:backend/auth-service.git"
  },
  "ref": "refs/heads/main",
  "commits": [
    {
      "id": "a1b2c3d4e5f6",
      "message": "feat: add oauth_provider column\n\nREQ-2024-0187",
      "added": ["src/auth/oauth.py"],
      "modified": ["src/auth/login.py"],
      "removed": []
    }
  ],
  "pusher": {"name": "leizhuang1332"}
}
```

**Git Webhook 处理逻辑：**

接收到 push 事件后：`git pull` → `git ls-files` → 增量更新 `file_path_cache` 表 → TreeSitter 解析变更文件的调用图 → 增量更新 `code_metadata` 表。不触发重新 clone。

---

## 九、摄入调度配置契约

```python
class IngestionSchedule(BaseModel):
    """摄入调度配置"""
    
    # ── PRD 文档 ──
    doc_webhook_enabled: bool = True
    doc_full_sync_schedule: str = "0 2 * * *"   # 每日凌晨 2:00 UTC
    doc_incremental_delay_seconds: int = 30      # Webhook 后延迟处理（防抖）
    
    # ── 代码仓库 ──
    code_webhook_enabled: bool = True
    code_incremental_delay_seconds: int = 10     # Webhook 后延迟处理（防抖）
    
    # ── SQL Schema ──
    sql_polling_interval_seconds: int = 600      # 10 分钟轮询
    sql_polling_enabled: bool = True
    
    # ── 全局 ──
    max_concurrent_ingestions: int = 3
    embedding_batch_size: int = 32
    embedding_rate_limit_per_minute: int = 1000
```

---

## 十、同义词映射表管理 API

### 10.1 刷新映射表

```
POST /api/v1/ingest/synonym-map/refresh
Authorization: Bearer <JWT_ADMIN>
```

```json
{
  "sources": ["information_schema", "prd_titles", "git_dirs"],
  "auto_apply_high_confidence": true,
  "confidence_threshold": 0.9
}
```

### 10.2 映射表查询

```
GET /api/v1/ingest/synonym-map?status=all&limit=100
Authorization: Bearer <JWT_ADMIN>
```

```json
{
  "total": 118,
  "entries": [
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
      "created_at": "2026-05-15T00:00:00Z"
    }
  ]
}
```
