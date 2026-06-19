# Markdown Directory Ingestion — Design Spec

**Date**: 2026-06-19
**Status**: Approved
**Scope**: 完善 `/api/v1/ingest/documents` 端点 `markdown_dir` 源的功能实现

---

## 1. Motivation

`DocIngestionSource.MARKDOWN_DIR` 枚举值已定义，但对应的数据获取和摄入逻辑完全缺失。`DocIngestionPipeline` 缺少 `run()` 方法，Controller 调用时会报错。本次补全整个链路。

## 2. Design Overview

### 2.1 Architecture: Source Handler Pattern

引入 `SourceHandler` 协议，将"获取文档"与"加工文档"解耦：

```
SourceHandler (protocol)                # 本次新建
  └── MarkdownDirSourceHandler          # 本次实现

(Future: ConfluenceSourceHandler, WikiApiSourceHandler)

POST /api/v1/ingest/documents
  → IngestionController.ingest_documents(request)
    → DocIngestionPipeline.run(request)
      → handler.fetch_documents(request)  → AsyncIterator[SourceDocument]
      → for each doc: ingest_document() / update_document()
      → return IngestionResult
```

### 2.2 Key Design Decisions

| 决策 | 选择 |
|------|------|
| 路径来源 | 请求参数 `path` 优先，fallback 到 `config/ingestion.yaml` 的 `markdown_dir` |
| 扫描粒度 | 支持单文件路径、目录递归扫描、Glob 模式（如 `docs/**/*.md`） |
| 增量策略 | 基于文件 mtime，对比上次成功摄入时间，跳过未变文件 |
| source_id | 文件绝对路径的 SHA256 哈希 |
| Markdown 处理 | 保留原始 Markdown 格式直接分块，不渲染 |
| 文件容错 | 单文件失败不影响其他文件，记录错误后继续 |

---

## 3. Schema Changes

### 3.1 `DocIngestionRequest` — 新增 `path` 字段

```python
# src/spma/api/schemas/ingestion.py

class DocIngestionRequest(BaseModel):
    source: DocIngestionSource = DocIngestionSource.CONFLUENCE
    mode: Literal["incremental", "full"] = "incremental"
    path: str | None = None  # ← 新增：markdown 路径/glob，对其他 source 无效
    filters: DocIngestionFilters = Field(default_factory=DocIngestionFilters)
    options: DocIngestionOptions = Field(default_factory=DocIngestionOptions)
```

### 3.2 `config/ingestion.yaml` — 新增 `markdown_dir` 默认值

```yaml
ingestion:
  doc:
    webhook_enabled: true
    webhook_debounce_seconds: 30
    full_sync_schedule: "0 2 * * *"
    parser: "docling"
    chunk_size_tokens: 500
    overlap_tokens: 50
    embedding_model: "BAAI/bge-m3"
    embedding_batch_size: 32
    markdown_dir: "/data/markdown"  # ← 新增
```

### 3.3 路径优先级

`source == "markdown_dir"` 时：
1. 使用请求体 `path`（若非空）
2. Fallback 到 YAML `ingestion.doc.markdown_dir`
3. 两者都为空 → 返回 HTTP 400 `{"detail": "path is required for markdown_dir source"}`

---

## 4. SourceHandler Protocol

### 4.1 `SourceDocument` Dataclass

```python
# src/spma/ingestion/source_handlers/base.py

@dataclass
class SourceDocument:
    text: str                            # 文档正文
    source_id: str                       # 唯一标识（SHA256 of absolute path）
    source_type: str                     # "confluence" | "markdown_dir"
    page_title: str = ""                 # 文件名（不含扩展名）或页面标题
    doc_type: str = "prd"
    version: str = ""
    req_ids: list[str] | None = None
    updated_at: str | None = None        # ISO 8601
```

### 4.2 `SourceHandler` Protocol

```python
class SourceHandler(Protocol):
    async def fetch_documents(
        self, request: DocIngestionRequest
    ) -> AsyncIterator[SourceDocument]: ...
```

---

## 5. MarkdownDirSourceHandler

### 5.1 Flow

```
fetch_documents(request)
  → resolve_path(request.path, config.markdown_dir)    # 确定扫描路径
  → validate_path(resolved_path)                         # 存在性检查
  → expand_glob(resolved_path)                           # glob 展开为文件列表
  → filter_md_files(files)                               # 仅保留 .md 文件
  → filter_by_mtime(files, last_time)                    # 增量模式：mtime 过滤
  → for each file:
      → read_file_content(filepath)                      # UTF-8 读取
      → SourceDocument(
          text=content,
          source_id=sha256(filepath),
          source_type="markdown_dir",
          page_title=stem(filepath),
          updated_at=mtime_iso(filepath),
        )
```

### 5.2 路径解析

```python
def resolve_path(request_path: str | None, config_path: str) -> str:
    if request_path:
        return request_path
    if config_path:
        return config_path
    raise ValueError("path is required for markdown_dir source")
```

### 5.3 Glob 展开

使用 `pathlib.Path.glob()`：
- 单文件（如 `/data/doc.md`）→ `[PosixPath('/data/doc.md')]`
- 目录（如 `/data/docs/`）→ 递归收集所有 `*.md`
- Glob（如 `/data/**/specs/*.md`）→ 标准 glob 语义
- 非法 pattern → 捕获异常，返回 ValueError

### 5.4 增量过滤

```python
async def _get_last_ingestion_time(self) -> float | None:
    latest = await self._run_store.get_latest_successful("doc", source_type="markdown_dir")
    if latest and latest.get("started_at"):
        return datetime.fromisoformat(latest["started_at"]).timestamp()
    return None
```

- `last_time is None`（首次运行）→ 处理所有文件
- `file_mtime > last_time` → 处理
- `file_mtime <= last_time` → 跳过

### 5.5 `get_latest_successful` — PipelineRunStore 新增方法

```python
async def get_latest_successful(
    self, pipeline_type: str, source_type: str
) -> dict | None:
    """返回指定管道类型和源类型的最近一次成功运行记录"""
```

---

## 6. DocIngestionPipeline Changes

### 6.1 新增 `run()` 方法

```python
class DocIngestionPipeline:
    def __init__(self, es_client, vector_store, embedder,
                 chunker=None, source_handlers: dict[str, SourceHandler] | None = None):
        ...
        self._handlers = source_handlers or {}

    async def run(self, request: DocIngestionRequest) -> IngestionResult:
        handler = self._handlers.get(request.source.value)
        if not handler:
            return IngestionResult(
                status="failed",
                errors=[{"error": f"Unsupported source: {request.source.value}"}]
            )

        stats = {"files_processed": 0, "chunks_generated": 0, "errors": 0}
        errors = []

        async for doc in handler.fetch_documents(request):
            try:
                if request.mode == "full" or request.options.force_full_reindex:
                    chunks = await self.update_document(
                        text=doc.text, source_id=doc.source_id,
                        source_type=doc.source_type, page_title=doc.page_title,
                        req_ids=doc.req_ids, doc_type=doc.doc_type, version=doc.version,
                    )
                else:
                    chunks = await self.ingest_document(
                        text=doc.text, source_id=doc.source_id,
                        source_type=doc.source_type, page_title=doc.page_title,
                        req_ids=doc.req_ids, doc_type=doc.doc_type, version=doc.version,
                    )
                stats["files_processed"] += 1
                stats["chunks_generated"] += chunks
            except Exception as e:
                logger.error(f"Failed to ingest {doc.source_id}: {e}")
                errors.append({"source_id": doc.source_id, "error": str(e)})
                stats["errors"] += 1

        return IngestionResult(
            status="completed" if not errors else "completed_with_errors",
            stats=stats,
            errors=errors,
        )
```

- `full` 模式 / `force_full_reindex` → 调用 `update_document()`（先删旧 chunks，再写入新 chunks）
- `incremental` 模式 → 调用 `ingest_document()`（直接写入，mtime 过滤已在 Handler 层完成）

---

## 7. Error Handling

| 场景 | 行为 |
|------|------|
| 路径不存在 | HTTP 400 `{"detail": "Path not found: /xxx"}` |
| 目录下无 `.md` 文件 | 正常完成，`stats.files_processed: 0`，不算错误 |
| 单个文件读取失败（权限等） | 记录错误 `{source_id, error}`，继续下一个 |
| 文件编码非 UTF-8 | 尝试 UTF-8 读取，失败则记录错误跳过 |
| 空文件 | 跳过，不报错 |
| Glob 模式非法 | HTTP 400 `{"detail": "Invalid glob pattern: [xxx]"}` |
| 符号链接 | 跟随符号链接，检测循环（`os.path.realpath` + visited set） |
| 文件超过大小阈值（默认 10MB） | 记录警告跳过（阈值可通过 `options` 配置） |

---

## 8. App Wiring

在 `src/spma/api/app.py` 的 `startup_ingestion()` 中：

```python
from spma.ingestion.source_handlers import MarkdownDirSourceHandler

handlers = {
    "markdown_dir": MarkdownDirSourceHandler(run_store, ingestion_config),
}

doc_pipeline = DocIngestionPipeline(
    es_client=es,
    vector_store=vector_store,
    embedder=embedder,
    chunker=SemanticChunker(),
    source_handlers=handlers,
)
```

> **注意**：`"confluence"` source 当前不通过 `run()` 方法调用（Confluence 的摄入走 webhook 路径 `handle_confluence_webhook()`），因此不在本次 handlers 字典中。后续 `ConfluenceSourceHandler` 封装可独立进行。

---

## 9. File Changes Summary

### New Files

| File | Description |
|------|-------------|
| `src/spma/ingestion/source_handlers/__init__.py` | 导出 `SourceHandler`, `SourceDocument`, `MarkdownDirSourceHandler` |
| `src/spma/ingestion/source_handlers/base.py` | `SourceDocument` dataclass + `SourceHandler` protocol |
| `src/spma/ingestion/source_handlers/markdown_handler.py` | `MarkdownDirSourceHandler` 实现 |
| `tests/unit/ingestion/test_markdown_handler.py` | Handler 单元测试 |
| `tests/unit/ingestion/test_doc_pipeline.py` | Pipeline `run()` 单元测试 |
| `tests/integration/test_markdown_ingestion.py` | 端到端集成测试 |

### Modified Files

| File | Change |
|------|--------|
| `src/spma/api/schemas/ingestion.py` | `DocIngestionRequest` 新增 `path` 字段 |
| `src/spma/ingestion/doc_pipeline.py` | 新增 `run()` 方法，构造函数接收 `source_handlers` |
| `src/spma/ingestion/controller.py` | 无需改动——`run()` 方法已存在于 Pipeline，Controller 中 `_doc_pipeline.run(request)` 调用自动生效 |
| `src/spma/ingestion/run_store.py` | 新增 `get_latest_successful()` 方法 |
| `src/spma/api/app.py` | 装配 handlers 并注入 pipeline |
| `config/ingestion.yaml` | 新增 `markdown_dir` 默认值 |
| `tests/e2e/test_ingestion_api.py` | 新增 markdown_dir E2E 测试用例 |

---

## 10. API Contract

### Request

```http
POST /api/v1/ingest/documents
Authorization: Bearer <admin_token>
Content-Type: application/json

{
    "source": "markdown_dir",
    "mode": "incremental",
    "path": "/data/docs/**/*.md",
    "options": {
        "force_full_reindex": false,
        "dry_run": false
    }
}
```

### Response (202 accepted)

```json
{
    "pipeline_run_id": "ingest-doc-20260619-143000",
    "source": "markdown_dir",
    "mode": "incremental",
    "status": "running"
}
```

### Status Check

```http
GET /api/v1/ingest/status/ingest-doc-20260619-143000

{
    "pipeline_run_id": "ingest-doc-20260619-143000",
    "pipeline_type": "doc",
    "status": "completed",
    "started_at": "2026-06-19T14:30:00Z",
    "completed_at": "2026-06-19T14:30:45Z",
    "duration_seconds": 45,
    "stats": {
        "files_processed": 42,
        "chunks_generated": 168,
        "errors": 1
    },
    "errors": [
        {
            "source_id": "a1b2c3d4e5f6...",
            "error": "Permission denied: /data/docs/restricted.md"
        }
    ]
}
```

---

## 11. Test Strategy

| Level | What | File |
|-------|------|------|
| Schema unit | `path` field validation, defaults, None for non-markdown sources | Extend `tests/unit/api/test_ingestion_schemas.py` |
| Handler unit | Path resolution, glob expansion, .md filtering, mtime filtering, encoding handling, empty file skip, symlink cycle detection | New `tests/unit/ingestion/test_markdown_handler.py` |
| Pipeline unit | `run()` with mock handler: success, partial failure, unsupported source, full vs incremental flow | New `tests/unit/ingestion/test_doc_pipeline.py` |
| Integration | Real temp dir + .md files, full/incremental modes, glob matching, error recovery | New `tests/integration/test_markdown_ingestion.py` |
| E2E | Real POST to endpoint with mock pipeline/controller | Extend `tests/e2e/test_ingestion_api.py` |
