---
phase: "03"
plan: "llamaindex-pipeline"
subsystem: "agents/doc"
tags: ["llamaindex", "retrieval", "pipeline", "hyde", "rrf", "rerank"]
requires: ["llamaindex-embedding", "llamaindex-retrievers"]
provides: ["AdvancedLlamaIndexPipeline", "PipelineConfig", "build_postprocessor_chain"]
affects: []
tech-stack:
  added: ["sentence-transformers"]
  patterns: ["TDD", "dataclass-config", "strategy-pattern", "deferred-initialization"]
key-files:
  created:
    - "src/spma/agents/doc/llamaindex_pipeline.py"
    - "tests/unit/agents/doc/test_llamaindex_pipeline.py"
  modified: []
decisions:
  - "Module-level try/except for optional sentence-transformers dependency to avoid import errors when not installed"
  - "Mock SentenceTransformerRerank in tests to avoid downloading BAAI/bge-reranker-v2-m3 (~1GB) during unit testing"
  - "Deferred import pattern removed from build_postprocessor_chain; moved to module-level for testability via patch"
metrics:
  duration: "~7min"
  completed_date: "2026-06-18"
---

# Phase 03 Plan llamaindex-pipeline: AdvancedLlamaIndexPipeline Summary

## One-liner
AdvancedLlamaIndexPipeline with mode-aware RRF retrieval, Cross-Encoder reranking, and HyDE query expansion - replacing legacy search_node retrieval logic.

## Task Completion

### Task 5: PipelineConfig + build_postprocessor_chain

**PipelineConfig dataclass:**
- Centralized configuration: `vector_top_k`, `bm25_top_k`, `hybrid_final_top_k`
- RRF parameters: `rrf_k=60`, `rrf_bm25_weight=0.5`, `rrf_vector_weight=0.5`
- Mode-specific weights: precise (0.7/0.3), hybrid (0.5/0.5), semantic (0.3/0.7)
- Reranker config: `BAAI/bge-reranker-v2-m3`, `rerank_top_n=10`, `enable_rerank=True`
- HyDE limits: `hyde_max_query_len=30`, `hyde_top_k=10`

**build_postprocessor_chain:**
- `precise` mode: returns empty list (preserves BM25 rank ordering)
- `hybrid`/`semantic` modes: SentenceTransformerRerank + LongContextReorder
- Configurable `rerank_top_n` parameter

### Task 6: AdvancedLlamaIndexPipeline

**Core pipeline:**
- Single instance per PGVector backend `VectorStoreIndex`
- Dynamic mode switching via `search(query, mode, entities)` parameter
- ESClient injected through `ESBM25Retriever` adapter
- Compatible output format: `list[dict]` with chunk_id, source_id, content, score, metadata

**Key methods:**
- `initialize(embedder, hyde_llm)`: Lazy init of VectorStoreIndex and embedding adapter
- `search(query, mode, entities)`: Unified retrieval entry with postprocessing and HyDE
- `_build_retriever(mode, entities)`: Creates mode-weighted HybridRRFRetriever with req_ids filters for precise mode
- `_should_use_hyde(query, entities)`: Short query + no req_ids heuristic
- `_hyde_search(query, hyde_llm)`: Hypothetical Document Expansion with graceful error handling

## Test Results

All 14 unit tests passing, zero regressions on existing 30 doc agent tests (44 total):

| Test Class | Tests | Status |
|---|---|---|
| TestPipelineConfig | 3 | PASSED |
| TestBuildPostprocessorChain | 4 | PASSED |
| TestAdvancedLlamaIndexPipeline | 7 | PASSED |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed `BaseNodePostprocessor` import error**
- **Found during:** GREEN phase - initial import failure
- **Issue:** `from llama_index.core.postprocessor import BaseNodePostprocessor` does not exist in installed llama_index version
- **Fix:** Removed the import. Changed return type annotation from `list[BaseNodePostprocessor]` to `list`. Removed type annotation on chain variable.
- **Files modified:** `src/spma/agents/doc/llamaindex_pipeline.py`
- **Commit:** 74765d6

**2. [Rule 2 - Missing Dependency] Installed `sentence-transformers`**
- **Found during:** Test execution of postprocessor chain tests
- **Issue:** `SentenceTransformerRerank` requires `sentence-transformers` package (with torch, transformers)
- **Fix:** `pip install sentence-transformers` - installed with torch 2.12.1, transformers 5.12.1
- **Files modified:** None (environment change only)

**3. [Rule 3 - Blocking] Mocked SentenceTransformerRerank in tests to avoid model download**
- **Found during:** GREEN phase test execution
- **Issue:** `SentenceTransformerRerank(model="BAAI/bge-reranker-v2-m3")` downloads ~1GB model on first instantiation, causing CI-unfriendly test times
- **Fix:** Patched `SentenceTransformerRerank` with `MagicMock` in postprocessor chain tests. Moved imports from function-local to module-level (`try/except`) so they can be patched at `spma.agents.doc.llamaindex_pipeline.SentenceTransformerRerank`.
- **Files modified:** `src/spma/agents/doc/llamaindex_pipeline.py`, `tests/unit/agents/doc/test_llamaindex_pipeline.py`
- **Commit:** 74765d6

## Commits

- `784f2d8`: `test(llamaindex-pipeline): add failing tests for PipelineConfig, postprocessor chain, and AdvancedLlamaIndexPipeline`
- `74765d6`: `feat(llamaindex-pipeline): implement AdvancedLlamaIndexPipeline with PipelineConfig, postprocessor chain, and HyDE support`

## Self-Check: PASSED

- [x] `src/spma/agents/doc/llamaindex_pipeline.py` exists
- [x] `tests/unit/agents/doc/test_llamaindex_pipeline.py` exists
- [x] Commit `784f2d8` exists (RED)
- [x] Commit `74765d6` exists (GREEN)
- [x] All 14 pipeline tests pass
- [x] All 30 existing doc tests still pass (no regressions)
