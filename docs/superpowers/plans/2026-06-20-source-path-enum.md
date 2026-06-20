# SourceDocument source_path & source_type Enum — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `source_path` field for human-readable document references, migrate all ingestion-layer `source_type` magic strings to `DocIngestionSource` enum, and display `source_path` instead of `source_id` in search results.

**Architecture:** `source_id` and `source_path` coexist with separated responsibilities — `source_id` for storage operations (delete/dedup), `source_path` for user display. The new field propagates from `SourceDocument` → `DocChunk` → ES/PGVector → retrieval metadata → generator output.

**Tech Stack:** Python 3.12+, dataclasses, Elasticsearch, PGVector, LlamaIndex

---

## File Structure

| File | Responsibility | Change |
|------|---------------|--------|
| `src/spma/api/schemas/ingestion.py` | `DocIngestionSource` enum (already exists) | No change |
| `src/spma/ingestion/source_handlers/base.py` | `SourceDocument` dataclass | Add `source_path`, enum type |
| `src/spma/ingestion/source_handlers/markdown_handler.py` | Populate `source_path` with absolute file path | Add `source_path`, use enum |
| `src/spma/ingestion/chunkers/semantic_chunker.py` | `DocChunk` dataclass + `split()` + `_make_chunk()` | Add `source_path` through all layers |
| `src/spma/ingestion/doc_pipeline.py` | `ingest_document()`, `update_document()`, `run()`, `_chunk_to_dict()` | Add `source_path` param, enum types, PGVector metadata |
| `src/spma/ingestion/controller.py` | Webhook handler `source` strings | Replace magic strings with enum |
| `config/es_mapping.yaml` | ES index mapping | Add `source_path` keyword field |
| `src/spma/retrieval/es_client.py` | No code changes needed (ES returns `_source` as-is) | No change |
| `src/spma/agents/doc/llamaindex_retrievers.py` | `ESBM25Retriever._aretrieve()` metadata | Add `source_path` to metadata dict |
| `src/spma/agents/doc/llamaindex_pipeline.py` | `search()` return dict | Add `source_path` to output dict |
| `src/spma/agents/synthesis/generator.py` | `_format_results()` display logic | Use `source_path` with fallback to `source_id` |
| `tests/e2e/test_doc_single_source.py` | E2E test data | Add `source_path` to test fixtures |

---

### Task 1: Add `source_path` to `SourceDocument` and `DocChunk` dataclasses

**Files:**
- Modify: `src/spma/ingestion/source_handlers/base.py:11-31`
- Modify: `src/spma/ingestion/chunkers/semantic_chunker.py:20-31`

**Purpose:** Define the new field at the two core data structures so it can flow through the entire pipeline.

- [ ] **Step 1: Add `source_path` to `SourceDocument`**

In `src/spma/ingestion/source_handlers/base.py`, add the field and update the docstring:

```python
@dataclass
class SourceDocument:
    """Standardized document object produced by source handlers."""

    text: str
    """Document body content."""

    source_id: str
    """Unique identifier — SHA256 of absolute file path for markdown, page_id for Confluence."""

    source_type: str
    """"confluence" | "markdown_dir" | "wiki_api"."""

    source_path: str = ""
    """Human-readable source path — absolute file path for markdown, page URL for Confluence/Wiki."""

    page_title: str = ""
    """Document title — filename stem for markdown, page title for Confluence."""

    doc_type: str = "prd"
    version: str = ""
    req_ids: list[str] | None = None
    updated_at: str | None = None
    """ISO 8601 timestamp of last modification."""
```

- [ ] **Step 2: Add `source_path` to `DocChunk`**

In `src/spma/ingestion/chunkers/semantic_chunker.py`, add the field:

```python
@dataclass
class DocChunk:
    chunk_id: str
    content: str
    source_id: str = ""
    source_type: str = ""
    source_path: str = ""
    req_ids: list[str] = field(default_factory=list)
    doc_type: str = ""
    version: str = ""
    updated_at: str = ""
    chunk_index: int = 0
    page_title: str = ""
```

- [ ] **Step 3: Commit**

```bash
git add src/spma/ingestion/source_handlers/base.py src/spma/ingestion/chunkers/semantic_chunker.py
git commit -m "feat: add source_path field to SourceDocument and DocChunk"
```

---

### Task 2: Add `source_path` to ES mapping

**Files:**
- Modify: `config/es_mapping.yaml:18-19`

**Purpose:** Ensure Elasticsearch stores and indexes the new field.

- [ ] **Step 1: Add `source_path` to ES mapping**

In `config/es_mapping.yaml`, add after `source_type`:

```yaml
    source_type:
      type: keyword
    source_path:
      type: keyword
```

- [ ] **Step 2: Commit**

```bash
git add config/es_mapping.yaml
git commit -m "feat: add source_path keyword field to ES mapping"
```

---

### Task 3: Migrate ingestion-layer `source_type` to `DocIngestionSource` enum

**Files:**
- Modify: `src/spma/ingestion/source_handlers/base.py:8,21-22` — import enum, change type annotation
- Modify: `src/spma/ingestion/source_handlers/markdown_handler.py:12,60` — import enum, use enum value
- Modify: `src/spma/ingestion/doc_pipeline.py:36,103` — change parameter types and defaults
- Modify: `src/spma/ingestion/controller.py:209,228` — use enum value

**Purpose:** Eliminate all magic strings for ingestion-layer `source_type`, enforce correctness at the type level.

- [ ] **Step 1: Change `SourceDocument.source_type` type annotation**

In `src/spma/ingestion/source_handlers/base.py`:

Add import at line 8:
```python
from spma.api.schemas.ingestion import DocIngestionRequest, DocIngestionSource
```

Change line 21-22:
```python
    source_type: DocIngestionSource
    """"confluence" | "markdown_dir" | "wiki_api"."""
```
*(replacing `source_type: str`)*

- [ ] **Step 2: Use enum in `MarkdownDirSourceHandler`**

In `src/spma/ingestion/source_handlers/markdown_handler.py`:

Add import at line 12:
```python
from spma.api.schemas.ingestion import DocIngestionRequest, DocIngestionSource
```
*(replacing the existing `DocIngestionRequest` import)*

Change line 60:
```python
                source_type=DocIngestionSource.MARKDOWN_DIR,
```
*(replacing `source_type="markdown_dir"`)*

- [ ] **Step 3: Change `ingest_document()` and `update_document()` type annotations**

In `src/spma/ingestion/doc_pipeline.py`:

Add import at the top (after existing imports):
```python
from spma.api.schemas.ingestion import DocIngestionSource
```

Change `ingest_document()` signature at line 36:
```python
    async def ingest_document(
        self,
        text: str,
        source_id: str,
        source_type: DocIngestionSource = DocIngestionSource.CONFLUENCE,
```
*(replacing `source_type: str = "confluence"`)*

Change `update_document()` signature at line 103:
```python
    async def update_document(
        self,
        text: str,
        source_id: str,
        source_type: DocIngestionSource = DocIngestionSource.CONFLUENCE,
```
*(replacing `source_type: str = "confluence"`)*

- [ ] **Step 4: Replace magic strings in `controller.py`**

In `src/spma/ingestion/controller.py`:

The import `DocIngestionRequest` is already imported from `spma.api.schemas.ingestion`. Add `DocIngestionSource` to that import at line 10-17:
```python
from spma.api.schemas.ingestion import (
    DocIngestionRequest,
    CodeIngestionRequest,
    SchemaIngestionRequest,
    IngestionResponse,
    PipelineRunDetail,
    IngestionResult,
    DocIngestionSource,
)
```

Change line 209:
```python
            source=DocIngestionSource.CONFLUENCE,
```
*(replacing `source="confluence"`)*

Change line 227:
```python
            source=DocIngestionSource.CONFLUENCE,
```
*(replacing `source="confluence"`)*

- [ ] **Step 5: Commit**

```bash
git add src/spma/ingestion/source_handlers/base.py src/spma/ingestion/source_handlers/markdown_handler.py src/spma/ingestion/doc_pipeline.py src/spma/ingestion/controller.py
git commit -m "refactor: migrate ingestion source_type to DocIngestionSource enum"
```

---

### Task 4: Propagate `source_path` through ingestion pipeline

**Files:**
- Modify: `src/spma/ingestion/source_handlers/markdown_handler.py:57-65` — populate `source_path`
- Modify: `src/spma/ingestion/chunkers/semantic_chunker.py:52-137` — add `source_path` to `split()` and `_make_chunk()`
- Modify: `src/spma/ingestion/doc_pipeline.py:32-41,47-56,70-85,99-108,114-122,130-186,188-201` — add `source_path` through `ingest_document()`, `update_document()`, `run()`, `_chunk_to_dict()`

**Purpose:** Ensure `source_path` flows from handler → chunker → storage (ES + PGVector).

- [ ] **Step 1: Populate `source_path` in MarkdownHandler**

In `src/spma/ingestion/source_handlers/markdown_handler.py`, change the `yield SourceDocument(...)` block at lines 57-65:

```python
            yield SourceDocument(
                text=content,
                source_id=self.make_source_id(filepath),
                source_type=DocIngestionSource.MARKDOWN_DIR,
                source_path=str(filepath.resolve()),
                page_title=filepath.stem,
                updated_at=datetime.fromtimestamp(
                    os.path.getmtime(filepath), tz=timezone.utc
                ).isoformat(),
            )
```

- [ ] **Step 2: Add `source_path` to `SemanticChunker.split()`**

In `src/spma/ingestion/chunkers/semantic_chunker.py`, change the `split()` method signature at lines 52-61:

```python
    def split(
        self,
        text: str,
        source_id: str = "",
        source_type: str = "",
        source_path: str = "",
        req_ids: list[str] | None = None,
        doc_type: str = "",
        version: str = "",
        updated_at: str = "",
        page_title: str = "",
    ) -> list[DocChunk]:
```

And update the internal calls within `split()`:

Line 70-73 — first `_make_chunk` call:
```python
            return [self._make_chunk(
                text, 0, source_id, source_type, source_path, req_ids,
                doc_type, version, updated_at, page_title,
            )]
```

Lines 80-83 — second `_make_chunk` call:
```python
            chunks.append(self._make_chunk(
                content, i, source_id, source_type, source_path, req_ids,
                doc_type, version, updated_at, page_title,
            ))
```

Lines 88-91 — fallback `_make_chunk` call:
```python
                chunks.append(self._make_chunk(
                    content, i, source_id, source_type, source_path, req_ids,
                    doc_type, version, updated_at, page_title,
                ))
```

- [ ] **Step 3: Add `source_path` to `SemanticChunker._make_chunk()`**

In `src/spma/ingestion/chunkers/semantic_chunker.py`, change the method at lines 124-137:

```python
    def _make_chunk(self, content, index, source_id, source_type, source_path, req_ids,
                    doc_type, version, updated_at, page_title) -> DocChunk:
        return DocChunk(
            chunk_id=str(uuid.uuid4()),
            content=content,
            source_id=source_id,
            source_type=source_type,
            source_path=source_path,
            req_ids=list(req_ids),
            doc_type=doc_type,
            version=version,
            updated_at=updated_at,
            chunk_index=index,
            page_title=page_title,
        )
```

- [ ] **Step 4: Add `source_path` parameter to `ingest_document()`**

In `src/spma/ingestion/doc_pipeline.py`, change the signature at lines 32-41:

```python
    async def ingest_document(
        self,
        text: str,
        source_id: str,
        source_type: DocIngestionSource = DocIngestionSource.CONFLUENCE,
        source_path: str = "",
        req_ids: list[str] | None = None,
        doc_type: str = "prd",
        version: str = "",
        page_title: str = "",
    ) -> int:
```

And update the `chunker.split()` call at lines 47-56 to pass `source_path`:

```python
        chunks = self.chunker.split(
            text=text,
            source_id=source_id,
            source_type=source_type,
            source_path=source_path,
            req_ids=req_ids,
            doc_type=doc_type,
            version=version,
            updated_at=datetime.now(timezone.utc).isoformat(),
            page_title=page_title,
        )
```

And update the PGVector metadata dict at lines 70-85 to include `source_path`:

```python
                batch = [
                    {
                        "node_id": chunk.chunk_id,
                        "text": chunk.content,
                        "embedding": emb,
                        "metadata": {
                            "source_id": chunk.source_id,
                            "source_type": chunk.source_type,
                            "source_path": chunk.source_path,
                            "req_ids": chunk.req_ids,
                            "doc_type": chunk.doc_type,
                            "version": chunk.version,
                            "updated_at": chunk.updated_at,
                            "chunk_index": chunk.chunk_index,
                            "page_title": chunk.page_title,
                        },
                    }
                    for chunk, emb in zip(chunks, embeddings)
                ]
```

- [ ] **Step 5: Add `source_path` parameter to `update_document()`**

In `src/spma/ingestion/doc_pipeline.py`, change the signature at lines 99-108:

```python
    async def update_document(
        self,
        text: str,
        source_id: str,
        source_type: DocIngestionSource = DocIngestionSource.CONFLUENCE,
        source_path: str = "",
        req_ids: list[str] | None = None,
        doc_type: str = "prd",
        version: str = "",
        page_title: str = "",
    ) -> int:
```

And update the `ingest_document()` call at lines 114-122 to pass `source_path`:

```python
        return await self.ingest_document(
            text=text,
            source_id=source_id,
            source_type=source_type,
            source_path=source_path,
            req_ids=req_ids,
            doc_type=doc_type,
            version=version,
            page_title=page_title,
        )
```

- [ ] **Step 6: Pass `source_path` in `run()` method**

In `src/spma/ingestion/doc_pipeline.py`, update both `update_document()` and `ingest_document()` calls within `run()` at lines 153-174 to include `source_path=doc.source_path`:

For the `update_document` branch (lines 156-164):
```python
                    chunks = await self.update_document(
                        text=doc.text,
                        source_id=doc.source_id,
                        source_type=doc.source_type,
                        source_path=doc.source_path,
                        page_title=doc.page_title,
                        req_ids=doc.req_ids,
                        doc_type=doc.doc_type,
                        version=doc.version,
                    )
```

For the `ingest_document` branch (lines 166-174):
```python
                    chunks = await self.ingest_document(
                        text=doc.text,
                        source_id=doc.source_id,
                        source_type=doc.source_type,
                        source_path=doc.source_path,
                        page_title=doc.page_title,
                        req_ids=doc.req_ids,
                        doc_type=doc.doc_type,
                        version=doc.version,
                    )
```

- [ ] **Step 7: Add `source_path` to `_chunk_to_dict()`**

In `src/spma/ingestion/doc_pipeline.py`, change `_chunk_to_dict()` at lines 188-201:

```python
    @staticmethod
    def _chunk_to_dict(chunk: DocChunk) -> dict:
        return {
            "chunk_id": chunk.chunk_id,
            "source_id": chunk.source_id,
            "source_type": chunk.source_type,
            "source_path": chunk.source_path,
            "req_ids": chunk.req_ids,
            "content": chunk.content,
            "doc_type": chunk.doc_type,
            "version": chunk.version,
            "updated_at": chunk.updated_at,
            "chunk_index": chunk.chunk_index,
            "page_title": chunk.page_title,
        }
```

- [ ] **Step 8: Commit**

```bash
git add src/spma/ingestion/source_handlers/markdown_handler.py src/spma/ingestion/chunkers/semantic_chunker.py src/spma/ingestion/doc_pipeline.py
git commit -m "feat: propagate source_path through ingestion pipeline"
```

---

### Task 5: Propagate `source_path` through retrieval pipeline

**Files:**
- Modify: `src/spma/agents/doc/llamaindex_retrievers.py:78` — add `source_path` to `ESBM25Retriever` metadata
- Modify: `src/spma/agents/doc/llamaindex_pipeline.py:179-188` — add `source_path` to `search()` output

**Purpose:** Ensure `source_path` survives the retrieval and fusion process and reaches the generator.

- [ ] **Step 1: Add `source_path` to ESBM25Retriever metadata**

In `src/spma/agents/doc/llamaindex_retrievers.py`, change the metadata dict at lines 77-83:

```python
                metadata={
                    "source_id": r.get("source_id"),
                    "source_type": r.get("source_type", "bm25"),
                    "source_path": r.get("source_path", ""),
                    "req_ids": r.get("req_ids", []),
                    "retrieval_source": "bm25",
                    **(r.get("metadata") or {}),
                },
```

- [ ] **Step 2: Add `source_path` to `AdvancedLlamaIndexPipeline.search()` output**

In `src/spma/agents/doc/llamaindex_pipeline.py`, change the return dict at lines 179-188:

```python
        return [
            {
                "chunk_id": n.node.node_id,
                "source_id": n.node.metadata.get("source_id"),
                "source_path": n.node.metadata.get("source_path", ""),
                "content": n.node.get_content(),
                "score": float(n.score),
                "metadata": n.node.metadata or {},
            }
            for n in all_results[:20]
        ]
```

- [ ] **Step 3: Commit**

```bash
git add src/spma/agents/doc/llamaindex_retrievers.py src/spma/agents/doc/llamaindex_pipeline.py
git commit -m "feat: propagate source_path through retrieval pipeline"
```

---

### Task 6: Update Generator to display `source_path`

**Files:**
- Modify: `src/spma/agents/synthesis/generator.py:31-32`

**Purpose:** Show the human-readable `source_path` instead of `source_id` in search result citations, with fallback for backward compatibility.

- [ ] **Step 1: Change citation display format**

In `src/spma/agents/synthesis/generator.py`, change the `_format_results()` function at lines 25-33:

```python
def _format_results(citations: list[dict], label: str) -> str:
    if not citations:
        return f"[来自{label}] 无结果"
    lines = [f"[来自{label}]"]
    for i, c in enumerate(citations):
        snippet = c.get("snippet", c.get("content", ""))[:300]
        source_ref = c.get("source_path") or c.get("source_id", "?")
        lines.append(f"{i + 1}. {source_ref}\n> {snippet}")
    return "\n".join(lines)
```

The key change: `source_ref = c.get("source_path") or c.get("source_id", "?")` — prefers `source_path` when available, falls back to `source_id` for backward compatibility with existing data.

- [ ] **Step 2: Commit**

```bash
git add src/spma/agents/synthesis/generator.py
git commit -m "feat: display source_path instead of source_id in search citations"
```

---

### Task 7: Update E2E tests

**Files:**
- Modify: `tests/e2e/test_doc_single_source.py:10-23,30-43`

**Purpose:** Ensure test fixtures include `source_path` and verify the field is present in retrieval results.

- [ ] **Step 1: Add `source_path` to test fixtures**

In `tests/e2e/test_doc_single_source.py`, add `"source_path"` to both test fixtures.

`test_precise_search_by_req_id` fixture (lines 11-22):
```python
        await test_es_client.index_chunks([
            {
                "chunk_id": "test-req-001",
                "source_id": "confluence:test-001",
                "source_type": "confluence",
                "source_path": "https://confluence.example.com/display/SPACE/Login+Module",
                "req_ids": ["REQ-TEST-001"],
                "content": "## 用户登录模块\n用户登录需要用户名和密码。[REQ-TEST-001]",
                "doc_type": "prd",
                "version": "v1.0",
                "updated_at": "2026-06-01T00:00:00Z",
                "chunk_index": 0,
                "page_title": "登录模块 PRD",
            }
        ])
```

`test_semantic_search_short_query` fixture (lines 31-42):
```python
        await test_es_client.index_chunks([
            {
                "chunk_id": "test-sem-001",
                "source_id": "confluence:test-001",
                "source_type": "confluence",
                "source_path": "https://confluence.example.com/display/SPACE/Payment+Flow",
                "req_ids": [],
                "content": "## 支付流程\n支付流程包括用户下单、第三方支付回调、订单状态更新三个步骤。",
                "doc_type": "prd",
                "version": "v1.0",
                "updated_at": "2026-06-01T00:00:00Z",
                "chunk_index": 0,
                "page_title": "支付流程 PRD",
            }
        ])
```

- [ ] **Step 2: Add assertion for `source_path` in search results**

Add after line 26 (`assert result.get("has_exact_match") or len(result.get("final_results", [])) >= 1`):

```python
        # 验证 source_path 被正确传递
        final_results = result.get("final_results", [])
        if final_results:
            assert final_results[0].get("source_path") == "https://confluence.example.com/display/SPACE/Login+Module"
```

Add after line 45 (`assert len(result.get("final_results", [])) >= 1`):

```python
        # 验证 source_path 被正确传递
        final_results = result.get("final_results", [])
        if final_results:
            assert "source_path" in final_results[0]
```

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/test_doc_single_source.py
git commit -m "test: add source_path to E2E test fixtures and assertions"
```

---

### Task 8: Run tests and verify

**Files:** None (verification only)

**Purpose:** Confirm all existing tests continue to pass and the new field propagates correctly.

- [ ] **Step 1: Run unit tests related to ingestion**

```bash
cd /Users/Ray/TraeProjects/SPMA && python -m pytest tests/ -k "ingest" -v --timeout=30 2>&1 | head -80
```

Expected: All ingestion-related tests pass or skip (if services unavailable).

- [ ] **Step 2: Run unit tests related to retrieval**

```bash
cd /Users/Ray/TraeProjects/SPMA && python -m pytest tests/ -k "retriev" -v --timeout=30 2>&1 | head -80
```

Expected: All retrieval-related tests pass or skip.

- [ ] **Step 3: Verify enum migration — grep for remaining magic strings**

```bash
cd /Users/Ray/TraeProjects/SPMA && grep -rn '"markdown_dir"\|"confluence"\|"wiki_api"' src/spma/ingestion/ --include="*.py"
```

Expected: Only hits in `__init__.py` exports or comments — no remaining magic string assignments in production code.

- [ ] **Step 4: Final commit if any cleanup needed**

```bash
git add -A && git diff --cached --stat
```

If no changes remain, no commit needed.
