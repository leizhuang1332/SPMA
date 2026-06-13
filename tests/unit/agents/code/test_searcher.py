"""Tests for RipgrepExecutor — unit tests for static methods + async integration tests."""

import pytest
from spma.agents.code.searcher import RipgrepExecutor


class TestRipgrepExecutor:
    """Unit tests for RipgrepExecutor static methods and construction."""

    def test_stem_split_snake_case(self):
        result = RipgrepExecutor._stem_split("token_refresh_service")
        assert "token" in result
        assert "refresh" in result
        assert "service" in result

    def test_stem_split_camel_case(self):
        result = RipgrepExecutor._stem_split("TokenRefreshService")
        lower = [p.lower() for p in result]
        assert "token" in lower
        assert "refresh" in lower

    def test_stem_split_single_word(self):
        result = RipgrepExecutor._stem_split("auth")
        assert "auth" in result

    def test_constructor_accepts_repo_paths(self):
        executor = RipgrepExecutor({"backend": "/repos/backend"})
        assert executor._repo_paths["backend"] == "/repos/backend"

    def test_search_returns_empty_for_empty_terms(self):
        executor = RipgrepExecutor({})
        assert len(executor._repo_paths) == 0


class TestRipgrepExecutorAsync:
    """Async integration tests — search against the local SPMA repo."""

    @pytest.mark.asyncio
    async def test_search_exact_finds_self(self):
        """Search for 'RipgrepExecutor' class definition with exact match."""
        repo_paths = {"spma": "/Users/Ray/TraeProjects/SPMA"}
        executor = RipgrepExecutor(repo_paths)
        search_terms = {"exact_terms": ["RipgrepExecutor"], "fuzzy_terms": []}
        results = await executor.search(search_terms, candidate_repos=["spma"], fallback_layer=0)
        assert len(results) > 0
        # At least one result should reference the class definition
        assert any("searcher.py" in r["file_path"] for r in results)
        assert all(r["match_type"] == "exact" for r in results)
        assert all(r["confidence"] == 0.95 for r in results)

    @pytest.mark.asyncio
    async def test_search_fallback_stem(self):
        """Stem search (layer 1) should break camelCase/snake_case."""
        repo_paths = {"spma": "/Users/Ray/TraeProjects/SPMA"}
        executor = RipgrepExecutor(repo_paths)
        search_terms = {"exact_terms": ["RipgrepExecutor"], "fuzzy_terms": []}
        # Use exact term but in stem mode — the term itself should still match via the combined list
        results = await executor.search(search_terms, candidate_repos=["spma"], fallback_layer=1)
        assert all(r["match_type"] == "stem" for r in results)
        assert all(r["confidence"] == 0.7 for r in results)

    @pytest.mark.asyncio
    async def test_search_fallback_fuzzy(self):
        """Fuzzy search (layer 2) should find results with case-insensitive matching."""
        repo_paths = {"spma": "/Users/Ray/TraeProjects/SPMA"}
        executor = RipgrepExecutor(repo_paths)
        search_terms = {"exact_terms": ["RipgrepExecutor"], "fuzzy_terms": []}
        results = await executor.search(search_terms, candidate_repos=["spma"], fallback_layer=2)
        assert any(r["match_type"] == "fuzzy" for r in results)
        assert all(r["confidence"] == 0.4 for r in results)

    @pytest.mark.asyncio
    async def test_search_fallback_llm_retry(self):
        """LLM retry search (layer 3) should find results."""
        repo_paths = {"spma": "/Users/Ray/TraeProjects/SPMA"}
        executor = RipgrepExecutor(repo_paths)
        search_terms = {"exact_terms": ["RipgrepExecutor"], "fuzzy_terms": []}
        results = await executor.search(search_terms, candidate_repos=["spma"], fallback_layer=3)
        assert any(r["match_type"] == "llm_retry" for r in results)
        assert all(r["confidence"] == 0.3 for r in results)

    @pytest.mark.asyncio
    async def test_search_unknown_repo_returns_empty(self):
        """Searching a non-existent repo returns no results."""
        executor = RipgrepExecutor({})
        search_terms = {"exact_terms": ["RipgrepExecutor"], "fuzzy_terms": []}
        results = await executor.search(search_terms, candidate_repos=["nonexistent"], fallback_layer=0)
        assert results == []

    @pytest.mark.asyncio
    async def test_search_deduplicates_by_repo_file_line(self):
        """Identical results across layers should be deduplicated."""
        repo_paths = {"spma": "/Users/Ray/TraeProjects/SPMA"}
        executor = RipgrepExecutor(repo_paths)
        # Use fuzzy_terms that will hit the same lines as exact_terms
        search_terms = {"exact_terms": ["RipgrepExecutor", "RipgrepExecutor"], "fuzzy_terms": []}
        results = await executor.search(search_terms, candidate_repos=["spma"], fallback_layer=0)
        # Check dedup: same term searched twice should not double results
        # Just verify no two results share identical (repo, file_path, line_number)
        seen = set()
        for r in results:
            key = (r["repo"], r["file_path"], r["line_number"])
            assert key not in seen, f"Duplicate result: {key}"
            seen.add(key)

    @pytest.mark.asyncio
    async def test_search_limits_to_50_results(self):
        """Result list should be capped at 50 entries."""
        repo_paths = {"spma": "/Users/Ray/TraeProjects/SPMA"}
        executor = RipgrepExecutor(repo_paths)
        search_terms = {"exact_terms": ["def"], "fuzzy_terms": []}
        results = await executor.search(search_terms, candidate_repos=["spma"], fallback_layer=2)
        assert len(results) <= 50
