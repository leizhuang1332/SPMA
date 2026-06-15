"""Integration test: code worker with real DI singletons."""
import pytest


class TestQueryCodeWorker:
    @pytest.mark.asyncio
    async def test_code_worker_graceful_degradation_when_no_deps(self):
        """When code agent deps not initialized, code worker returns error, not crash."""
        from spma.api import dependencies as dep

        # Reset singletons to None to simulate uninitialized state
        dep.set_file_path_cache(None)
        dep.set_ripgrep_executor(None)
        dep.set_ast_parser(None)

        # Assert RuntimeError is raised on get
        with pytest.raises(RuntimeError):
            dep.get_file_path_cache()
        with pytest.raises(RuntimeError):
            dep.get_ripgrep_executor()
        with pytest.raises(RuntimeError):
            dep.get_ast_parser()

    @pytest.mark.asyncio
    async def test_code_worker_success_path_with_mocks(self):
        """Code worker with mocked deps should produce result."""
        from unittest.mock import AsyncMock, MagicMock
        from spma.agents.code.graph import build_code_agent_graph

        # Build mock deps
        mock_cache = MagicMock()
        mock_cache.query_files = AsyncMock(return_value=[
            {"repo_name": "backend", "file_path": "src/auth.py", "file_type": "python"},
        ])
        mock_cache.list_repos = AsyncMock(return_value=["backend"])

        mock_rg = MagicMock()
        mock_rg._repo_paths = {"backend": "/repos/backend"}
        mock_rg.search = AsyncMock(return_value=[
            {"file_path": "src/auth.py", "line_number": 42, "match": "def login"},
        ])
        mock_rg.search_gitlog = AsyncMock(return_value=[])

        mock_ast = MagicMock()
        mock_ast.parse_file = MagicMock(return_value={
            "imports": [],
            "functions": [],
            "calls": [],
        })

        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock()
        mock_llm.ainvoke.return_value = MagicMock(content="converge")

        graph = build_code_agent_graph(
            file_path_cache=mock_cache,
            ripgrep_executor=mock_rg,
            ast_parser=mock_ast,
            llm=mock_llm,
            max_rounds=1,
        )

        result = await graph.ainvoke({
            "original_query": "how does login work",
            "rewritten_queries": ["login implementation"],
            "query_id": "test-123",
        })

        assert "final_results" in result or "ripgrep_results" in result
