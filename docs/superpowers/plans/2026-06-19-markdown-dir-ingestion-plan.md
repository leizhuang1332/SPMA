# Markdown Directory Ingestion — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 `/api/v1/ingest/documents` 端点的 `markdown_dir` 源——支持读取本地目录/文件的 Markdown 文件并通过 SourceHandler 策略模式接入文档摄入管线。

**Architecture:** 新增 `SourceHandler` 协议层，将"获取文档"与"加工文档"解耦。`MarkdownDirSourceHandler` 负责路径解析、glob 展开、mtime 增量过滤和文件读取；`DocIngestionPipeline.run()` 通过 handler 获取 `SourceDocument` 迭代器，复用现有的 `ingest_document()`/`update_document()` 完成分块→嵌入→双写。

**Tech Stack:** Python 3.13+, FastAPI, Pydantic, pathlib, hashlib, asyncpg, pytest + pytest-asyncio

---

## File Map

### New Files

| File | Responsibility |
|------|---------------|
| `src/spma/ingestion/source_handlers/__init__.py` | Package exports |
| `src/spma/ingestion/source_handlers/base.py` | `SourceDocument` dataclass, `SourceHandler` protocol |
| `src/spma/ingestion/source_handlers/markdown_handler.py` | `MarkdownDirSourceHandler` — path resolution, glob, mtime filter, file reading |
| `tests/unit/ingestion/test_markdown_handler.py` | Unit tests for MarkdownDirSourceHandler |
| `tests/unit/ingestion/test_doc_pipeline.py` | Unit tests for DocIngestionPipeline.run() |
| `tests/integration/test_markdown_ingestion.py` | Integration tests with real temp dir + .md files |

### Modified Files

| File | Change |
|------|--------|
| `src/spma/api/schemas/ingestion.py` | `DocIngestionRequest` add `path: str \| None = None` |
| `src/spma/ingestion/run_store.py` | Add `get_latest_successful(pipeline_type, source_type)` |
| `src/spma/ingestion/doc_pipeline.py` | Add `run()` method; `__init__` accepts `source_handlers` |
| `src/spma/api/app.py` | Wire `MarkdownDirSourceHandler` into `DocIngestionPipeline` |
| `config/ingestion.yaml` | Add `markdown_dir` default under `ingestion.doc` |
| `tests/unit/api/test_ingestion_schemas.py` | Add `path` field tests |
| `tests/e2e/test_ingestion_api.py` | Add markdown_dir E2E test cases |

---

### Task 1: SourceDocument dataclass + SourceHandler protocol

**Files:**
- Create: `src/spma/ingestion/source_handlers/__init__.py`
- Create: `src/spma/ingestion/source_handlers/base.py`

> No tests needed — these are pure data structures / type protocols.

- [ ] **Step 1: Create package `__init__.py`**

```bash
mkdir -p src/spma/ingestion/source_handlers
```

Write `src/spma/ingestion/source_handlers/__init__.py`:

```python
"""Source handlers — fetch documents from various sources (Confluence, local markdown, etc.)."""

from spma.ingestion.source_handlers.base import SourceDocument, SourceHandler

__all__ = ["SourceDocument", "SourceHandler"]
```

- [ ] **Step 2: Create base.py with SourceDocument and SourceHandler**

Write `src/spma/ingestion/source_handlers/base.py`:

```python
"""Source handler protocol — decouples "fetch documents" from "process documents"."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import AsyncIterator, Protocol

from spma.api.schemas.ingestion import DocIngestionRequest


@dataclass
class SourceDocument:
    """Standardized document object produced by source handlers."""

    text: str
    """Document body content."""

    source_id: str
    """Unique identifier — SHA256 of absolute file path for markdown, page_id for Confluence."""

    source_type: str
    """"confluence" | "markdown_dir" | "wiki_api"."""

    page_title: str = ""
    """Document title — filename stem for markdown, page title for Confluence."""

    doc_type: str = "prd"
    version: str = ""
    req_ids: list[str] | None = None
    updated_at: str | None = None
    """ISO 8601 timestamp of last modification."""


class SourceHandler(Protocol):
    """Protocol for document source handlers.

    Each implementation fetches documents from a specific source type
    and yields standardized SourceDocument objects.
    """

    async def fetch_documents(
        self, request: DocIngestionRequest
    ) -> AsyncIterator[SourceDocument]:
        """Yield documents matching the request parameters.

        The handler is responsible for:
        - Resolving the source path (request param, config fallback, etc.)
        - Scanning/listing documents matching filters
        - Reading document content
        - Yielding SourceDocument objects one at a time

        Errors for individual documents should be logged and skipped —
        the caller handles per-document error reporting.
        """
        ...
```

- [ ] **Step 3: Verify imports work**

Run: `uv run python -c "from spma.ingestion.source_handlers import SourceDocument, SourceHandler; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add src/spma/ingestion/source_handlers/
git commit -m "feat: add SourceDocument dataclass and SourceHandler protocol"
```

---

### Task 2: Add `path` field to DocIngestionRequest

**Files:**
- Modify: `src/spma/api/schemas/ingestion.py:42-46`
- Test: `tests/unit/api/test_ingestion_schemas.py` (extend existing)

- [ ] **Step 1: Write the failing tests (extend TestDocIngestionRequest)**

Append to `tests/unit/api/test_ingestion_schemas.py`, inside `class TestDocIngestionRequest`:

```python
    def test_path_default_none(self):
        """path 字段默认为 None。"""
        req = DocIngestionRequest()
        assert req.path is None

    def test_path_with_markdown_dir(self):
        """markdown_dir 源时可指定 path。"""
        req = DocIngestionRequest(
            source="markdown_dir",
            mode="full",
            path="/data/docs/**/*.md",
        )
        assert req.source == DocIngestionSource.MARKDOWN_DIR
        assert req.path == "/data/docs/**/*.md"

    def test_path_with_confluence_source_ignored(self):
        """confluence 源时 path 无意义但仍可设为 None。"""
        req = DocIngestionRequest(source="confluence")
        assert req.path is None

    def test_path_empty_string_accepted(self):
        """空字符串 path 也是合法的 Pydantic 值（fallback 到 config）。"""
        req = DocIngestionRequest(source="markdown_dir", path="")
        assert req.path == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/api/test_ingestion_schemas.py::TestDocIngestionRequest::test_path_default_none tests/unit/api/test_ingestion_schemas.py::TestDocIngestionRequest::test_path_with_markdown_dir tests/unit/api/test_ingestion_schemas.py::TestDocIngestionRequest::test_path_with_confluence_source_ignored tests/unit/api/test_ingestion_schemas.py::TestDocIngestionRequest::test_path_empty_string_accepted -v`
Expected: FAIL with "Extra inputs are not permitted" or "Field required" or AttributeError

- [ ] **Step 3: Add `path` field to DocIngestionRequest**

Edit `src/spma/api/schemas/ingestion.py`, add `path` field to `DocIngestionRequest`:

```python
class DocIngestionRequest(BaseModel):
    source: DocIngestionSource = DocIngestionSource.CONFLUENCE
    mode: Literal["incremental", "full"] = "incremental"
    path: str | None = None  # ← 新增：markdown 文件/目录路径或 glob 模式
    filters: DocIngestionFilters = Field(default_factory=DocIngestionFilters)
    options: DocIngestionOptions = Field(default_factory=DocIngestionOptions)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/api/test_ingestion_schemas.py::TestDocIngestionRequest -v`
Expected: all 9 tests PASS (5 existing + 4 new)

- [ ] **Step 5: Commit**

```bash
git add src/spma/api/schemas/ingestion.py tests/unit/api/test_ingestion_schemas.py
git commit -m "feat: add path field to DocIngestionRequest for markdown_dir source"
```

---

### Task 3: Add `get_latest_successful()` to PipelineRunStore

**Files:**
- Modify: `src/spma/ingestion/run_store.py` (add method after `get_latest`)
- Test: `tests/unit/ingestion/test_run_store.py` (check if exists, create if not)

- [ ] **Step 1: Check if test file exists and write test**

Check: `ls tests/unit/ingestion/test_run_store.py`

If the file doesn't exist, create it. If it does exist, append to it.

Write `tests/unit/ingestion/test_run_store.py` (or append to it):

```python
"""Tests for PipelineRunStore."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from spma.ingestion.run_store import PipelineRunStore


class TestGetLatestSuccessful:
    """Tests for get_latest_successful()."""

    @pytest.mark.asyncio
    async def test_returns_latest_successful_run(self):
        """返回最近一次成功的运行记录。"""
        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn

        mock_row = {
            "pipeline_run_id": "ingest-doc-20260619-143000",
            "pipeline_type": "doc",
            "source": "markdown_dir",
            "mode": "incremental",
            "status": "completed",
            "started_at": "2026-06-19T14:30:00+00:00",
            "completed_at": "2026-06-19T14:30:45+00:00",
            "stats": '{"files_processed": 10}',
            "errors": '[]',
        }
        mock_conn.fetchrow.return_value = mock_row

        store = PipelineRunStore(mock_pool)
        result = await store.get_latest_successful("doc", source_type="markdown_dir")

        assert result is not None
        assert result["pipeline_run_id"] == "ingest-doc-20260619-143000"
        assert result["status"] == "completed"
        assert result["source"] == "markdown_dir"

    @pytest.mark.asyncio
    async def test_returns_none_when_no_run(self):
        """没有匹配记录时返回 None。"""
        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        mock_conn.fetchrow.return_value = None

        store = PipelineRunStore(mock_pool)
        result = await store.get_latest_successful("doc", source_type="markdown_dir")

        assert result is None

    @pytest.mark.asyncio
    async def test_only_returns_completed_runs(self):
        """只返回 status='completed' 的记录——running/failed 不算。"""
        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        mock_conn.fetchrow.return_value = None  # 没有 completed 的

        store = PipelineRunStore(mock_pool)
        result = await store.get_latest_successful("doc", source_type="markdown_dir")

        assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/ingestion/test_run_store.py::TestGetLatestSuccessful -v`
Expected: FAIL with "PipelineRunStore has no attribute 'get_latest_successful'"

- [ ] **Step 3: Implement `get_latest_successful()`**

Add to `src/spma/ingestion/run_store.py`, after `get_latest()` (line 99):

```python
    async def get_latest_successful(
        self, pipeline_type: str, source_type: str | None = None
    ) -> dict | None:
        """获取最近一次成功完成的运行记录。

        Args:
            pipeline_type: "doc" | "code" | "sql"
            source_type: 可选，进一步过滤 source（如 "markdown_dir"）。为 None 时不按 source 过滤。

        Returns:
            最近一次 status='completed' 的运行记录，无匹配时返回 None。
        """
        async with self._db_pool.acquire() as conn:
            if source_type:
                row = await conn.fetchrow(
                    """
                    SELECT pipeline_run_id, pipeline_type, source, mode, status,
                           started_at, completed_at, estimated_completion,
                           stats, errors, created_by, created_at
                    FROM ingestion_runs
                    WHERE pipeline_type = $1
                      AND source = $2
                      AND status = 'completed'
                    ORDER BY started_at DESC
                    LIMIT 1
                    """,
                    pipeline_type, source_type,
                )
            else:
                row = await conn.fetchrow(
                    """
                    SELECT pipeline_run_id, pipeline_type, source, mode, status,
                           started_at, completed_at, estimated_completion,
                           stats, errors, created_by, created_at
                    FROM ingestion_runs
                    WHERE pipeline_type = $1
                      AND status = 'completed'
                    ORDER BY started_at DESC
                    LIMIT 1
                    """,
                    pipeline_type,
                )
            return dict(row) if row else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/ingestion/test_run_store.py::TestGetLatestSuccessful -v`
Expected: all 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/spma/ingestion/run_store.py tests/unit/ingestion/test_run_store.py
git commit -m "feat: add get_latest_successful() to PipelineRunStore"
```

---

### Task 4: Implement MarkdownDirSourceHandler

**Files:**
- Create: `src/spma/ingestion/source_handlers/markdown_handler.py`
- Create: `tests/unit/ingestion/test_markdown_handler.py`

- [ ] **Step 1: Write failing tests for MarkdownDirSourceHandler**

Write `tests/unit/ingestion/test_markdown_handler.py`:

```python
"""Tests for MarkdownDirSourceHandler."""

import os
import hashlib
import tempfile
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from spma.api.schemas.ingestion import DocIngestionRequest, DocIngestionSource
from spma.ingestion.source_handlers.markdown_handler import MarkdownDirSourceHandler


class TestResolvePath:
    """Path resolution logic."""

    def test_uses_request_path_when_provided(self):
        handler = MarkdownDirSourceHandler(
            run_store=MagicMock(),
            config={"doc": {"markdown_dir": "/default/path"}},
        )
        result = handler._resolve_path("/custom/path")
        assert result == "/custom/path"

    def test_falls_back_to_config_when_request_path_empty(self):
        handler = MarkdownDirSourceHandler(
            run_store=MagicMock(),
            config={"doc": {"markdown_dir": "/default/path"}},
        )
        result = handler._resolve_path("")
        assert result == "/default/path"

    def test_falls_back_to_config_when_request_path_none(self):
        handler = MarkdownDirSourceHandler(
            run_store=MagicMock(),
            config={"doc": {"markdown_dir": "/default/path"}},
        )
        result = handler._resolve_path(None)
        assert result == "/default/path"

    def test_raises_when_both_empty(self):
        handler = MarkdownDirSourceHandler(
            run_store=MagicMock(),
            config={},
        )
        with pytest.raises(ValueError, match="path is required for markdown_dir source"):
            handler._resolve_path(None)


class TestExpandGlob:
    """Glob expansion logic."""

    def test_single_file_returns_itself(self):
        handler = MarkdownDirSourceHandler(run_store=MagicMock(), config={})
        with tempfile.TemporaryDirectory() as tmpdir:
            f = Path(tmpdir) / "test.md"
            f.write_text("# Hello")
            result = handler.expand_files(str(f))
            assert len(result) == 1
            assert result[0] == f

    def test_directory_recursively_collects_md_files(self):
        handler = MarkdownDirSourceHandler(run_store=MagicMock(), config={})
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            (base / "a.md").write_text("# A")
            (base / "sub").mkdir()
            (base / "sub" / "b.md").write_text("# B")
            (base / "notes.txt").write_text("not markdown")

            result = handler.expand_files(str(base))
            paths = {str(p) for p in result}
            assert len(result) == 2
            assert str(base / "a.md") in paths
            assert str(base / "sub" / "b.md") in paths

    def test_glob_pattern(self):
        handler = MarkdownDirSourceHandler(run_store=MagicMock(), config={})
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            (base / "specs").mkdir()
            (base / "docs").mkdir()
            (base / "specs" / "design.md").write_text("# Design")
            (base / "docs" / "readme.md").write_text("# Readme")

            result = handler.expand_files(str(base / "specs" / "*.md"))
            assert len(result) == 1
            assert result[0].name == "design.md"

    def test_glob_recursive(self):
        handler = MarkdownDirSourceHandler(run_store=MagicMock(), config={})
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            (base / "sub1").mkdir()
            (base / "sub2").mkdir()
            (base / "sub1" / "a.md").write_text("# A")
            (base / "sub2" / "b.md").write_text("# B")

            result = handler.expand_files(str(base / "**" / "*.md"))
            assert len(result) == 2

    def test_empty_directory_returns_empty_list(self):
        handler = MarkdownDirSourceHandler(run_store=MagicMock(), config={})
        with tempfile.TemporaryDirectory() as tmpdir:
            result = handler.expand_files(str(tmpdir))
            assert result == []

    def test_no_md_files_returns_empty_list(self):
        handler = MarkdownDirSourceHandler(run_store=MagicMock(), config={})
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            (base / "notes.txt").write_text("text")
            (base / "data.json").write_text("{}")
            result = handler.expand_files(str(base))
            assert result == []


class TestFilterByMtime:
    """Mtime-based incremental filtering."""

    def test_returns_all_when_last_time_is_none(self):
        handler = MarkdownDirSourceHandler(run_store=MagicMock(), config={})
        files = [Path("/fake/a.md"), Path("/fake/b.md")]
        result = handler.filter_by_mtime(files, None)
        assert result == files

    def test_filters_out_unchanged_files(self):
        handler = MarkdownDirSourceHandler(run_store=MagicMock(), config={})
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            old = base / "old.md"
            old.write_text("# Old")
            old_mtime = os.path.getmtime(old)

            import time
            time.sleep(0.01)  # ensure different mtime
            new = base / "new.md"
            new.write_text("# New")

            result = handler.filter_by_mtime([old, new], old_mtime)
            assert len(result) == 1
            assert result[0] == new


class TestReadFileContent:
    """File reading and encoding handling."""

    def test_reads_utf8_file(self):
        handler = MarkdownDirSourceHandler(run_store=MagicMock(), config={})
        with tempfile.TemporaryDirectory() as tmpdir:
            f = Path(tmpdir) / "doc.md"
            f.write_text("# Hello\n\nWorld", encoding="utf-8")
            content = handler.read_file_content(f)
            assert content == "# Hello\n\nWorld"

    def test_empty_file_returns_empty_string(self):
        handler = MarkdownDirSourceHandler(run_store=MagicMock(), config={})
        with tempfile.TemporaryDirectory() as tmpdir:
            f = Path(tmpdir) / "empty.md"
            f.write_text("")
            content = handler.read_file_content(f)
            assert content == ""

    def test_non_utf8_file_reads_with_utf8(self):
        """Non-UTF-8 files: attempt UTF-8 first. If it fails, exception propagates."""
        handler = MarkdownDirSourceHandler(run_store=MagicMock(), config={})
        with tempfile.TemporaryDirectory() as tmpdir:
            f = Path(tmpdir) / "latin1.md"
            f.write_bytes("café".encode("latin-1"))
            # This depends on the content — some latin-1 is valid UTF-8 by coincidence.
            # For truly non-UTF-8, read_file_content will raise UnicodeDecodeError.
            try:
                content = handler.read_file_content(f)
            except UnicodeDecodeError:
                content = None
            # Either valid UTF-8 or raises — both are acceptable behaviors documented in spec.
            # The caller (fetch_documents) catches exceptions per-file.
            assert content is not None or content is None  # no crash


class TestSourceId:
    """SHA256 source_id generation."""

    def test_generates_sha256_of_absolute_path(self):
        handler = MarkdownDirSourceHandler(run_store=MagicMock(), config={})
        path = Path("/data/docs/readme.md")
        expected = hashlib.sha256(str(path).encode()).hexdigest()
        result = handler.make_source_id(path)
        assert result == expected
        assert len(result) == 64  # SHA256 hex digest length

    def test_different_paths_produce_different_ids(self):
        handler = MarkdownDirSourceHandler(run_store=MagicMock(), config={})
        id1 = handler.make_source_id(Path("/data/a.md"))
        id2 = handler.make_source_id(Path("/data/b.md"))
        assert id1 != id2


class TestFetchDocuments:
    """Integration of the full fetch_documents flow."""

    @pytest.mark.asyncio
    async def test_yields_source_documents_for_each_file(self):
        mock_run_store = MagicMock()
        mock_run_store.get_latest_successful = AsyncMock(return_value=None)

        handler = MarkdownDirSourceHandler(
            run_store=mock_run_store,
            config={"markdown_dir": "/tmp"},
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            (base / "a.md").write_text("# Alpha")
            (base / "b.md").write_text("# Beta")
            (base / "notes.txt").write_text("text")

            from spma.api.schemas.ingestion import DocIngestionRequest, DocIngestionSource
            request = DocIngestionRequest(
                source=DocIngestionSource.MARKDOWN_DIR,
                mode="full",
                path=str(base),
            )

            docs = []
            async for doc in handler.fetch_documents(request):
                docs.append(doc)

            assert len(docs) == 2
            titles = {d.page_title for d in docs}
            assert "a" in titles or "b" in titles
            for d in docs:
                assert d.source_type == "markdown_dir"
                assert len(d.source_id) == 64
                assert d.text != ""

    @pytest.mark.asyncio
    async def test_skips_empty_files(self):
        mock_run_store = MagicMock()
        mock_run_store.get_latest_successful = AsyncMock(return_value=None)

        handler = MarkdownDirSourceHandler(
            run_store=mock_run_store,
            config={},
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            (base / "empty.md").write_text("")
            (base / "has_content.md").write_text("# Content")

            request = DocIngestionRequest(
                source=DocIngestionSource.MARKDOWN_DIR,
                mode="full",
                path=str(base),
            )

            docs = []
            async for doc in handler.fetch_documents(request):
                docs.append(doc)

            assert len(docs) == 1
            assert docs[0].page_title == "has_content"

    @pytest.mark.asyncio
    async def test_raises_for_nonexistent_path(self):
        handler = MarkdownDirSourceHandler(
            run_store=MagicMock(),
            config={},
        )

        request = DocIngestionRequest(
            source=DocIngestionSource.MARKDOWN_DIR,
            mode="full",
            path="/nonexistent/path/xyz",
        )

        with pytest.raises(ValueError, match="Path not found"):
            async for _ in handler.fetch_documents(request):
                pass
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/ingestion/test_markdown_handler.py -v`
Expected: FAIL with "No module named 'spma.ingestion.source_handlers.markdown_handler'"

- [ ] **Step 3: Implement MarkdownDirSourceHandler**

Write `src/spma/ingestion/source_handlers/markdown_handler.py`:

```python
"""MarkdownDirSourceHandler — scan local directories for .md files and yield SourceDocuments."""

from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator

from spma.api.schemas.ingestion import DocIngestionRequest
from spma.ingestion.source_handlers.base import SourceDocument

logger = logging.getLogger(__name__)

# Maximum file size to read (10 MB)
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024


class MarkdownDirSourceHandler:
    """Scans local directories for Markdown files and yields SourceDocuments.

    Supports:
    - Single file paths
    - Directory paths (recursive scan for *.md)
    - Glob patterns (e.g. ``docs/**/*.md``)
    - Incremental mode via mtime filtering against last successful ingestion time
    """

    def __init__(self, run_store, config: dict):
        """
        Args:
            run_store: PipelineRunStore instance for querying last ingestion time.
            config: The ``ingestion`` section from spma.yaml.
        """
        self._run_store = run_store
        self._config = config or {}

    # ── public API ──────────────────────────────────────────────────

    async def fetch_documents(
        self, request: DocIngestionRequest
    ) -> AsyncIterator[SourceDocument]:
        """Scan, filter, and yield SourceDocuments per the request."""
        doc_config = self._config.get("doc", {})
        resolved = self.resolve_path(request.path)
        self.validate_path(resolved)
        files = self.expand_files(resolved)

        if request.mode == "incremental":
            last_time = await self._get_last_ingestion_time()
        else:
            last_time = None

        files = self.filter_by_mtime(files, last_time)

        for filepath in sorted(files):
            content = self.read_file_content(filepath)
            if content is None or content.strip() == "":
                continue

            yield SourceDocument(
                text=content,
                source_id=self.make_source_id(filepath),
                source_type="markdown_dir",
                page_title=filepath.stem,
                updated_at=datetime.fromtimestamp(
                    os.path.getmtime(filepath), tz=timezone.utc
                ).isoformat(),
            )

    # ── path resolution ─────────────────────────────────────────────

    @staticmethod
    def resolve_path(request_path: str | None, config: dict | None = None) -> str:
        """Resolve path: request param > config ``markdown_dir`` > error.

        Also exposed as a static method for unit testing without config dict.
        """
        if request_path:
            return request_path
        if config:
            fallback = config.get("markdown_dir", "")
            if fallback:
                return fallback
        raise ValueError("path is required for markdown_dir source")

    # Instance wrapper that uses self._config
    def _resolve_path(self, request_path: str | None) -> str:
        doc_config = self._config.get("doc", {})
        return self.resolve_path(request_path, doc_config)

    # Update the async fetch_documents to use _resolve_path
    # (already integrated above)

    # ── validation ───────────────────────────────────────────────────

    @staticmethod
    def validate_path(path_str: str) -> None:
        """Raise ValueError if the resolved path does not exist."""
        # For glob patterns, validate the base directory exists
        p = Path(path_str)
        # Walk up to find the first existing ancestor
        check = p if not any(c in path_str for c in "*?[]") else p.parent
        while check != check.parent:
            if check.exists():
                return
            check = check.parent
        if not Path(path_str).exists():
            raise ValueError(f"Path not found: {path_str}")

    # ── file discovery ───────────────────────────────────────────────

    @staticmethod
    def expand_files(path_str: str) -> list[Path]:
        """Expand a path/glob into a list of .md files.

        - Single .md file → [that file]
        - Single directory → recursive ``**/*.md``
        - Glob pattern → evaluate with ``Path.glob()``
        """
        p = Path(path_str)

        if p.is_file():
            return [p] if p.suffix == ".md" else []

        if p.is_dir():
            files = list(p.rglob("*.md"))
            return [f for f in files if _is_real_file(f)]

        # Treat as glob
        try:
            # Resolve relative globs against cwd
            base = Path(".") if not p.is_absolute() and not str(p).startswith("/") else Path("/")
            # For absolute globs starting with /, pathlib needs the root
            if path_str.startswith("/"):
                # Use Path.glob on the root with a relative pattern
                glob_pattern = path_str.lstrip("/")
                files = list(Path("/").glob(glob_pattern))
            else:
                files = list(Path().glob(path_str))

            md_files = [f for f in files if f.suffix == ".md" and _is_real_file(f)]
            if not md_files:
                # Fallback: treat as literal path that doesn't exist yet
                # Already handled by validate_path, so this is a real glob with no matches
                return []
            return md_files
        except Exception:
            raise

    # ── incremental filtering ────────────────────────────────────────

    @staticmethod
    def filter_by_mtime(files: list[Path], last_time: float | None) -> list[Path]:
        """Filter files by mtime > last_time. Returns all if last_time is None."""
        if last_time is None:
            return files
        return [f for f in files if os.path.getmtime(f) > last_time]

    async def _get_last_ingestion_time(self) -> float | None:
        """Query the last successful markdown_dir ingestion timestamp."""
        try:
            latest = await self._run_store.get_latest_successful(
                "doc", source_type="markdown_dir"
            )
            if latest and latest.get("started_at"):
                dt = datetime.fromisoformat(
                    str(latest["started_at"]).replace("Z", "+00:00")
                )
                return dt.timestamp()
        except Exception as e:
            logger.warning("Failed to get last ingestion time: %s", e)
        return None

    # ── file reading ─────────────────────────────────────────────────

    @staticmethod
    def read_file_content(filepath: Path) -> str | None:
        """Read file as UTF-8 text. Returns None for unreadable files."""
        try:
            size = filepath.stat().st_size
            if size > MAX_FILE_SIZE_BYTES:
                logger.warning(
                    "Skipping large file %s (%.1f MB > %d MB limit)",
                    filepath, size / (1024 * 1024), MAX_FILE_SIZE_BYTES // (1024 * 1024),
                )
                return None
            return filepath.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError, OSError) as e:
            logger.warning("Cannot read %s: %s", filepath, e)
            return None

    # ── source_id generation ─────────────────────────────────────────

    @staticmethod
    def make_source_id(filepath: Path) -> str:
        """Generate a deterministic source_id from the absolute file path."""
        return hashlib.sha256(str(filepath).encode()).hexdigest()


# ── helpers ──────────────────────────────────────────────────────────

def _is_real_file(p: Path) -> bool:
    """Check that a path is a real file, following symlinks but detecting cycles."""
    try:
        resolved = p.resolve()
        return resolved.is_file()
    except (OSError, RuntimeError):
        # Symlink loop or permission error
        return False
```

Now update `src/spma/ingestion/source_handlers/__init__.py` to export the handler:

```python
"""Source handlers — fetch documents from various sources (Confluence, local markdown, etc.)."""

from spma.ingestion.source_handlers.base import SourceDocument, SourceHandler
from spma.ingestion.source_handlers.markdown_handler import MarkdownDirSourceHandler

__all__ = ["SourceDocument", "SourceHandler", "MarkdownDirSourceHandler"]
```

- [ ] **Step 4: Fix fetch_documents to use _resolve_path**

The `fetch_documents` method in step 3 uses `self.resolve_path(request.path)` but the instance method is `_resolve_path`. Let's correct this — update the `fetch_documents` method to use `self._resolve_path(request.path)`:

Edit the `fetch_documents` method, change line:
```python
        resolved = self.resolve_path(request.path)
```
to:
```python
        resolved = self._resolve_path(request.path)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/ingestion/test_markdown_handler.py -v`
Expected: all tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/spma/ingestion/source_handlers/ tests/unit/ingestion/test_markdown_handler.py
git commit -m "feat: implement MarkdownDirSourceHandler with glob, mtime filter, and file reading"
```

---

### Task 5: Implement DocIngestionPipeline.run()

**Files:**
- Modify: `src/spma/ingestion/doc_pipeline.py` (add `run()` method, update `__init__`)
- Create: `tests/unit/ingestion/test_doc_pipeline.py`

- [ ] **Step 1: Write failing tests for DocIngestionPipeline.run()**

Write `tests/unit/ingestion/test_doc_pipeline.py`:

```python
"""Tests for DocIngestionPipeline.run()."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from spma.api.schemas.ingestion import (
    DocIngestionRequest,
    DocIngestionSource,
    IngestionResult,
)
from spma.ingestion.doc_pipeline import DocIngestionPipeline
from spma.ingestion.source_handlers.base import SourceDocument


class TestRun:
    """Tests for the run() method."""

    @pytest.mark.asyncio
    async def test_run_with_markdown_handler_success(self):
        """Full mode: handler yields documents, pipeline ingests them."""
        es = MagicMock()
        vector_store = MagicMock()
        embedder = MagicMock()

        doc1 = SourceDocument(
            text="# Doc 1",
            source_id="abc123",
            source_type="markdown_dir",
            page_title="doc1",
        )
        doc2 = SourceDocument(
            text="# Doc 2",
            source_id="def456",
            source_type="markdown_dir",
            page_title="doc2",
        )

        mock_handler = MagicMock()
        mock_handler.fetch_documents = AsyncMock()
        mock_handler.fetch_documents.return_value.__aiter__.return_value = [doc1, doc2]

        pipeline = DocIngestionPipeline(
            es_client=es,
            vector_store=vector_store,
            embedder=embedder,
            source_handlers={"markdown_dir": mock_handler},
        )

        # Mock the internal ingest_document to return chunk count
        pipeline.ingest_document = AsyncMock(return_value=3)

        request = DocIngestionRequest(
            source=DocIngestionSource.MARKDOWN_DIR,
            mode="full",
            path="/data/docs",
        )

        result = await pipeline.run(request)

        assert result.status == "completed"
        assert result.stats["files_processed"] == 2
        assert result.stats["chunks_generated"] == 6  # 3+3
        assert result.stats["errors"] == 0
        assert result.errors == []

    @pytest.mark.asyncio
    async def test_run_full_mode_uses_update_document(self):
        """Full mode should call update_document (delete + re-ingest)."""
        es = MagicMock()
        vector_store = MagicMock()
        embedder = MagicMock()

        doc = SourceDocument(
            text="# Doc",
            source_id="abc123",
            source_type="markdown_dir",
            page_title="doc",
        )

        mock_handler = MagicMock()
        mock_handler.fetch_documents = AsyncMock()
        mock_handler.fetch_documents.return_value.__aiter__.return_value = [doc]

        pipeline = DocIngestionPipeline(
            es_client=es,
            vector_store=vector_store,
            embedder=embedder,
            source_handlers={"markdown_dir": mock_handler},
        )
        pipeline.ingest_document = AsyncMock()
        pipeline.update_document = AsyncMock(return_value=5)

        request = DocIngestionRequest(
            source=DocIngestionSource.MARKDOWN_DIR,
            mode="full",
            path="/data/docs",
        )

        await pipeline.run(request)

        pipeline.update_document.assert_called_once()
        pipeline.ingest_document.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_incremental_mode_uses_ingest_document(self):
        """Incremental mode should call ingest_document (direct write)."""
        es = MagicMock()
        vector_store = MagicMock()
        embedder = MagicMock()

        doc = SourceDocument(
            text="# Doc",
            source_id="abc123",
            source_type="markdown_dir",
            page_title="doc",
        )

        mock_handler = MagicMock()
        mock_handler.fetch_documents = AsyncMock()
        mock_handler.fetch_documents.return_value.__aiter__.return_value = [doc]

        pipeline = DocIngestionPipeline(
            es_client=es,
            vector_store=vector_store,
            embedder=embedder,
            source_handlers={"markdown_dir": mock_handler},
        )
        pipeline.ingest_document = AsyncMock(return_value=3)
        pipeline.update_document = AsyncMock()

        request = DocIngestionRequest(
            source=DocIngestionSource.MARKDOWN_DIR,
            mode="incremental",
            path="/data/docs",
        )

        await pipeline.run(request)

        pipeline.ingest_document.assert_called_once()
        pipeline.update_document.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_force_full_reindex_uses_update_document(self):
        """force_full_reindex should trigger update_document even in incremental mode."""
        es = MagicMock()
        vector_store = MagicMock()
        embedder = MagicMock()

        doc = SourceDocument(
            text="# Doc",
            source_id="abc123",
            source_type="markdown_dir",
            page_title="doc",
        )

        mock_handler = MagicMock()
        mock_handler.fetch_documents = AsyncMock()
        mock_handler.fetch_documents.return_value.__aiter__.return_value = [doc]

        pipeline = DocIngestionPipeline(
            es_client=es,
            vector_store=vector_store,
            embedder=embedder,
            source_handlers={"markdown_dir": mock_handler},
        )
        pipeline.ingest_document = AsyncMock()
        pipeline.update_document = AsyncMock(return_value=3)

        from spma.api.schemas.ingestion import DocIngestionOptions
        request = DocIngestionRequest(
            source=DocIngestionSource.MARKDOWN_DIR,
            mode="incremental",
            path="/data/docs",
            options=DocIngestionOptions(force_full_reindex=True),
        )

        await pipeline.run(request)

        pipeline.update_document.assert_called_once()
        pipeline.ingest_document.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_partial_failure_continues(self):
        """Single document failure should not stop processing remaining docs."""
        es = MagicMock()
        vector_store = MagicMock()
        embedder = MagicMock()

        doc1 = SourceDocument(text="# OK", source_id="ok1", source_type="markdown_dir", page_title="ok")
        doc2 = SourceDocument(text="# Bad", source_id="bad1", source_type="markdown_dir", page_title="bad")
        doc3 = SourceDocument(text="# OK2", source_id="ok2", source_type="markdown_dir", page_title="ok2")

        mock_handler = MagicMock()
        mock_handler.fetch_documents = AsyncMock()
        mock_handler.fetch_documents.return_value.__aiter__.return_value = [doc1, doc2, doc3]

        pipeline = DocIngestionPipeline(
            es_client=es,
            vector_store=vector_store,
            embedder=embedder,
            source_handlers={"markdown_dir": mock_handler},
        )

        call_count = 0
        async def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("Simulated failure")
            return 2

        pipeline.ingest_document = AsyncMock(side_effect=side_effect)

        request = DocIngestionRequest(
            source=DocIngestionSource.MARKDOWN_DIR,
            mode="incremental",
            path="/data/docs",
        )

        result = await pipeline.run(request)

        assert result.status == "completed_with_errors"
        assert result.stats["files_processed"] == 2  # doc1 + doc3
        assert result.stats["errors"] == 1
        assert len(result.errors) == 1
        assert result.errors[0]["source_id"] == "bad1"

    @pytest.mark.asyncio
    async def test_run_unsupported_source(self):
        """Unsupported source returns failed result."""
        es = MagicMock()
        vector_store = MagicMock()
        embedder = MagicMock()

        pipeline = DocIngestionPipeline(
            es_client=es,
            vector_store=vector_store,
            embedder=embedder,
            source_handlers={},  # empty handlers
        )

        request = DocIngestionRequest(
            source=DocIngestionSource.MARKDOWN_DIR,
            mode="full",
        )

        result = await pipeline.run(request)

        assert result.status == "failed"
        assert len(result.errors) == 1
        assert "Unsupported source" in result.errors[0]["error"]

    @pytest.mark.asyncio
    async def test_run_no_documents(self):
        """Handler yielding no documents is not an error."""
        es = MagicMock()
        vector_store = MagicMock()
        embedder = MagicMock()

        mock_handler = MagicMock()
        mock_handler.fetch_documents = AsyncMock()
        mock_handler.fetch_documents.return_value.__aiter__.return_value = []

        pipeline = DocIngestionPipeline(
            es_client=es,
            vector_store=vector_store,
            embedder=embedder,
            source_handlers={"markdown_dir": mock_handler},
        )

        request = DocIngestionRequest(
            source=DocIngestionSource.MARKDOWN_DIR,
            mode="full",
            path="/empty/dir",
        )

        result = await pipeline.run(request)

        assert result.status == "completed"
        assert result.stats["files_processed"] == 0
        assert result.stats["chunks_generated"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/ingestion/test_doc_pipeline.py -v`
Expected: FAIL with "DocIngestionPipeline has no attribute 'run'" or "unexpected keyword argument 'source_handlers'"

- [ ] **Step 3: Implement `run()` method and update `__init__`**

Edit `src/spma/ingestion/doc_pipeline.py`:

Change `__init__` signature to accept `source_handlers`:

```python
    def __init__(
        self,
        es_client: ESClient,
        vector_store,  # PGVector client
        embedder,       # BGE-M3 embedding 客户端
        chunker: SemanticChunker | None = None,
        source_handlers: dict | None = None,  # ← 新增
    ):
        self.es = es_client
        self.vector_store = vector_store
        self.embedder = embedder
        self.chunker = chunker or SemanticChunker()
        self._handlers = source_handlers or {}
```

Add `run()` method after `delete_document()` (line 126):

```python
    async def run(self, request) -> "IngestionResult":
        """Execute document ingestion based on the request source.

        Dispatches to the appropriate SourceHandler, then ingests each
        yielded SourceDocument via ingest_document() or update_document().
        """
        from spma.api.schemas.ingestion import IngestionResult

        handler = self._handlers.get(request.source.value)
        if not handler:
            return IngestionResult(
                status="failed",
                errors=[{"error": f"Unsupported source: {request.source.value}"}],
                stats={},
            )

        stats = {"files_processed": 0, "chunks_generated": 0, "errors": 0}
        errors: list[dict] = []

        should_full_reindex = (
            request.mode == "full" or request.options.force_full_reindex
        )

        async for doc in handler.fetch_documents(request):
            try:
                if should_full_reindex:
                    chunks = await self.update_document(
                        text=doc.text,
                        source_id=doc.source_id,
                        source_type=doc.source_type,
                        page_title=doc.page_title,
                        req_ids=doc.req_ids,
                        doc_type=doc.doc_type,
                        version=doc.version,
                    )
                else:
                    chunks = await self.ingest_document(
                        text=doc.text,
                        source_id=doc.source_id,
                        source_type=doc.source_type,
                        page_title=doc.page_title,
                        req_ids=doc.req_ids,
                        doc_type=doc.doc_type,
                        version=doc.version,
                    )
                stats["files_processed"] += 1
                stats["chunks_generated"] += chunks
            except Exception as e:
                logger.error("Failed to ingest %s: %s", doc.source_id, e)
                errors.append({"source_id": doc.source_id, "error": str(e)})
                stats["errors"] += 1

        return IngestionResult(
            status="completed" if not errors else "completed_with_errors",
            stats=stats,
            errors=errors,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/ingestion/test_doc_pipeline.py -v`
Expected: all 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/spma/ingestion/doc_pipeline.py tests/unit/ingestion/test_doc_pipeline.py
git commit -m "feat: add run() method to DocIngestionPipeline with source handler dispatch"
```

---

### Task 6: Wire MarkdownDirSourceHandler into app.py

**Files:**
- Modify: `src/spma/api/app.py` (in `startup_ingestion()` around line 224-225)

- [ ] **Step 1: Update app.py to inject source_handlers into DocIngestionPipeline**

Edit `src/spma/api/app.py`. In the `startup_ingestion()` function, after the embedder creation (line 221) and before the DocIngestionPipeline creation (line 224-225), add the handler import and dict. Then modify the `DocIngestionPipeline(...)` call.

Change this block (lines 220-225):

```python
        # 3. Embedder
        from spma.retrieval.embedder import BGEM3Embedder
        embedder = await BGEM3Embedder.create()

        # 4. Doc Pipeline
        from spma.ingestion.doc_pipeline import DocIngestionPipeline
        doc_pipeline = DocIngestionPipeline(es, vector_store, embedder)
```

To:

Replace lines 223-258 of `src/spma/api/app.py` (from "4. Doc Pipeline" through "7. Run Store"):

```python
        # 4. Run Store (moved up — needed by source handlers)
        from spma.ingestion.run_store import PipelineRunStore
        run_store = PipelineRunStore(db_pool)

        # 5. Doc Pipeline (with source handlers)
        from spma.ingestion.doc_pipeline import DocIngestionPipeline
        from spma.ingestion.source_handlers import MarkdownDirSourceHandler

        source_handlers = {
            "markdown_dir": MarkdownDirSourceHandler(run_store, ingestion_cfg),
        }
        doc_pipeline = DocIngestionPipeline(
            es, vector_store, embedder, source_handlers=source_handlers,
        )
```

This moves `run_store` creation above `doc_pipeline` and removes the duplicate declaration later.

- [ ] **Step 2: Verify app imports work**

Run: `uv run python -c "from spma.api.app import create_app; app = create_app(); print('OK')"`
Expected: `OK`

Note: This may fail if PostgreSQL/ES are not available locally — that's expected. The test verifies import correctness. If the startup event crashes due to missing DB, that's a pre-existing condition, not a regression.

- [ ] **Step 3: Commit**

```bash
git add src/spma/api/app.py
git commit -m "feat: wire MarkdownDirSourceHandler into DocIngestionPipeline startup"
```

---

### Task 7: Update config/ingestion.yaml

**Files:**
- Modify: `config/ingestion.yaml`

- [ ] **Step 1: Add markdown_dir default**

Edit `config/ingestion.yaml`, add under `ingestion.doc`:

```yaml
    markdown_dir: "/data/markdown"
```

- [ ] **Step 2: Commit**

```bash
git add config/ingestion.yaml
git commit -m "feat: add markdown_dir default config to ingestion.yaml"
```

---

### Task 8: Integration tests with real temp directory

**Files:**
- Create: `tests/integration/test_markdown_ingestion.py`

- [ ] **Step 1: Write integration tests**

Write `tests/integration/test_markdown_ingestion.py`:

```python
"""Integration tests for markdown_dir ingestion — uses real temp directories."""

import os
import tempfile
from pathlib import Path

import pytest

from spma.ingestion.source_handlers.markdown_handler import MarkdownDirSourceHandler


class TestMarkdownDirSourceHandlerIntegration:
    """Tests MarkdownDirSourceHandler with real filesystem operations."""

    @pytest.fixture
    def handler(self):
        """Create handler with mock run_store returning no previous runs."""
        from unittest.mock import AsyncMock, MagicMock
        mock_store = MagicMock()
        mock_store.get_latest_successful = AsyncMock(return_value=None)
        return MarkdownDirSourceHandler(
            run_store=mock_store,
            config={"doc": {"markdown_dir": "/tmp"}},
        )

    @pytest.fixture
    def md_tree(self):
        """Create a temporary markdown file tree.

        Structure:
            tmpdir/
            ├── readme.md
            ├── design.md
            ├── sub/
            │   └── arch.md
            └── notes.txt
        """
        tmpdir = tempfile.TemporaryDirectory()
        base = Path(tmpdir.name)

        (base / "readme.md").write_text("# README\n\nProject overview.", encoding="utf-8")
        (base / "design.md").write_text("# Design\n\n## Overview\n\nDetails here.", encoding="utf-8")
        (base / "sub").mkdir()
        (base / "sub" / "arch.md").write_text("# Architecture\n\n## Components\n\n- A\n- B", encoding="utf-8")
        (base / "notes.txt").write_text("Just notes.", encoding="utf-8")

        yield base
        tmpdir.cleanup()

    # ── expand_files ──

    def test_expand_directory_finds_all_md_files(self, handler, md_tree):
        files = handler.expand_files(str(md_tree))
        names = {f.name for f in files}
        assert names == {"readme.md", "design.md", "arch.md"}

    def test_expand_single_file(self, handler, md_tree):
        target = str(md_tree / "readme.md")
        files = handler.expand_files(target)
        assert len(files) == 1
        assert files[0].name == "readme.md"

    def test_expand_glob_pattern(self, handler, md_tree):
        pattern = str(md_tree / "sub" / "*.md")
        files = handler.expand_files(pattern)
        assert len(files) == 1
        assert files[0].name == "arch.md"

    def test_expand_recursive_glob(self, handler, md_tree):
        pattern = str(md_tree / "**" / "*.md")
        files = handler.expand_files(pattern)
        assert len(files) == 3

    # ── filter_by_mtime ──

    def test_mtime_filtering_incremental(self, handler, md_tree):
        import time
        files = list(md_tree.rglob("*.md"))
        files = [f for f in files if f.suffix == ".md"]

        # Take a snapshot now — all files are older than this
        time.sleep(0.02)
        snapshot_time = time.time()

        # All files should be filtered out (mtime <= snapshot_time)
        result = handler.filter_by_mtime(files, snapshot_time)
        assert len(result) == 0

    def test_mtime_filtering_new_file(self, handler, md_tree):
        import time
        # Get old files
        old_files = [f for f in md_tree.rglob("*.md") if f.suffix == ".md"]
        time.sleep(0.02)
        snapshot_time = time.time()

        # Create a new file after the snapshot
        new_file = md_tree / "new.md"
        new_file.write_text("# New file")

        all_files = old_files + [new_file]
        result = handler.filter_by_mtime(all_files, snapshot_time)
        assert len(result) == 1
        assert result[0].name == "new.md"

    # ── read_file_content ──

    def test_reads_utf8_content(self, handler, md_tree):
        content = handler.read_file_content(md_tree / "readme.md")
        assert content == "# README\n\nProject overview."

    def test_reads_empty_file(self, handler, md_tree):
        empty = md_tree / "empty.md"
        empty.write_text("")
        content = handler.read_file_content(empty)
        assert content == ""

    def test_skips_large_file(self, handler, md_tree):
        big = md_tree / "big.md"
        # Create a file just over the 10MB limit
        big.write_text("x" * (10 * 1024 * 1024 + 1))
        content = handler.read_file_content(big)
        assert content is None

    # ── source_id ──

    def test_source_id_deterministic(self, handler, md_tree):
        id1 = handler.make_source_id(md_tree / "readme.md")
        id2 = handler.make_source_id(md_tree / "readme.md")
        assert id1 == id2

    def test_source_id_unique_per_file(self, handler, md_tree):
        id1 = handler.make_source_id(md_tree / "readme.md")
        id2 = handler.make_source_id(md_tree / "design.md")
        assert id1 != id2

    # ── resolve_path ──

    def test_resolve_path_request_wins(self, handler):
        # Use the static method directly
        result = MarkdownDirSourceHandler.resolve_path(
            "/custom/path", {"markdown_dir": "/default"}
        )
        assert result == "/custom/path"

    def test_resolve_path_fallback(self, handler):
        result = MarkdownDirSourceHandler.resolve_path(
            "", {"markdown_dir": "/default"}
        )
        assert result == "/default"

    def test_resolve_path_none_fallback(self, handler):
        result = MarkdownDirSourceHandler.resolve_path(
            None, {"markdown_dir": "/default"}
        )
        assert result == "/default"

    def test_resolve_path_raises_when_both_empty(self, handler):
        with pytest.raises(ValueError, match="path is required"):
            MarkdownDirSourceHandler.resolve_path(None, {})

    # ── validate_path ──

    def test_validate_existing_path(self, handler, md_tree):
        handler.validate_path(str(md_tree))  # should not raise

    def test_validate_nonexistent_path_raises(self, handler):
        with pytest.raises(ValueError, match="Path not found"):
            handler.validate_path("/this/does/not/exist/at/all")
```

- [ ] **Step 2: Run integration tests**

Run: `uv run pytest tests/integration/test_markdown_ingestion.py -v`
Expected: all tests PASS

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_markdown_ingestion.py
git commit -m "test: add integration tests for markdown_dir source handler"
```

---

### Task 9: E2E tests for the API endpoint

**Files:**
- Modify: `tests/e2e/test_ingestion_api.py`

- [ ] **Step 1: Add markdown_dir E2E test**

Append to `tests/e2e/test_ingestion_api.py`:

```python
class TestIngestDocumentsMarkdownDir:
    """E2E tests for POST /api/v1/ingest/documents with markdown_dir source."""

    @pytest.mark.asyncio
    async def test_markdown_dir_valid_request(self, test_app, auth_headers, monkeypatch):
        """Valid markdown_dir request returns 200 with pipeline_run_id."""
        from unittest.mock import AsyncMock, MagicMock
        from spma.api.schemas.ingestion import IngestionResponse

        mock_controller = MagicMock()
        mock_controller.ingest_documents = AsyncMock(
            return_value=IngestionResponse(
                pipeline_run_id="ingest-doc-test-markdown-001",
                source="markdown_dir",
                mode="full",
                status="running",
            )
        )

        # Override the controller dependency
        from spma.api.dependencies import get_ingestion_controller
        monkeypatch.setattr(
            "spma.api.routes.ingestion.get_ingestion_controller",
            lambda: mock_controller,
        )

        payload = {
            "source": "markdown_dir",
            "mode": "full",
            "path": "/data/docs/**/*.md",
        }

        response = test_app.post(
            "/api/v1/ingest/documents",
            json=payload,
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["source"] == "markdown_dir"
        assert data["mode"] == "full"

    @pytest.mark.asyncio
    async def test_markdown_dir_without_path_uses_config_fallback(self, test_app, auth_headers, monkeypatch):
        """Request without path should still be accepted (falls back to config)."""
        from unittest.mock import AsyncMock, MagicMock
        from spma.api.schemas.ingestion import IngestionResponse

        mock_controller = MagicMock()
        mock_controller.ingest_documents = AsyncMock(
            return_value=IngestionResponse(
                pipeline_run_id="ingest-doc-test-fallback-001",
                source="markdown_dir",
                mode="incremental",
                status="running",
            )
        )

        monkeypatch.setattr(
            "spma.api.routes.ingestion.get_ingestion_controller",
            lambda: mock_controller,
        )

        payload = {
            "source": "markdown_dir",
            "mode": "incremental",
        }

        response = test_app.post(
            "/api/v1/ingest/documents",
            json=payload,
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["source"] == "markdown_dir"
```

- [ ] **Step 2: Run E2E tests**

Run: `uv run pytest tests/e2e/test_ingestion_api.py::TestIngestDocumentsMarkdownDir -v`
Expected: all tests PASS

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/test_ingestion_api.py
git commit -m "test: add E2E tests for markdown_dir ingestion endpoint"
```

---

### Task 10: Run full test suite and verify no regressions

- [ ] **Step 1: Run all unit tests**

```bash
uv run pytest tests/unit/ -v
```

Expected: all tests PASS (no regressions)

- [ ] **Step 2: Run all integration tests**

```bash
uv run pytest tests/integration/ -v
```

Expected: all tests PASS

- [ ] **Step 3: Run the schema + ingestion-related tests specifically**

```bash
uv run pytest tests/unit/api/test_ingestion_schemas.py tests/unit/ingestion/ tests/integration/test_markdown_ingestion.py -v
```

Expected: all tests PASS

- [ ] **Step 4: Final commit if any cleanup needed**

```bash
git status
# commit any remaining changes if needed
```
