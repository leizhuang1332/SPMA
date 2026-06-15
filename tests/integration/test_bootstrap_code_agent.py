"""Integration tests for init_code_agent_deps in bootstrap.py."""
import pytest


@pytest.fixture(autouse=True)
def _reset_code_agent_globals():
    """Reset all Code Agent singletons to None after each test."""
    yield
    import spma.api.dependencies as dep
    dep._db_pool = None
    dep._file_path_cache = None
    dep._ripgrep_executor = None
    dep._ast_parser = None


class TestInitCodeAgentDeps:
    @pytest.mark.asyncio
    async def test_init_code_agent_deps_populates_singletons(self):
        """After init, all 4 singletons should be retrievable."""
        from spma.api import dependencies as dep
        from spma.bootstrap import init_code_agent_deps

        # Use a real asyncpg pool or skip if no DB available
        pool = None
        try:
            import asyncpg
            pool = await asyncpg.create_pool(
                "postgresql://spma:spma123@localhost:5433/spma",
                min_size=1, max_size=2,
            )
        except Exception:
            pytest.skip("PostgreSQL not available")

        try:
            await init_code_agent_deps(pool, repo_base="/repos")

            assert dep.get_db_pool() is pool
            assert dep.get_file_path_cache() is not None
            assert dep.get_ripgrep_executor() is not None
            assert dep.get_ast_parser() is not None

            # RipgrepExecutor should have repo_paths derived from list_repos()
            executor = dep.get_ripgrep_executor()
            assert isinstance(executor._repo_paths, dict)
        finally:
            await pool.close()

    @pytest.mark.asyncio
    async def test_init_code_agent_deps_empty_repos(self):
        """When file_path_cache table is empty, repo_paths should be {}."""
        from spma.api import dependencies as dep
        from spma.bootstrap import init_code_agent_deps

        from unittest.mock import AsyncMock, MagicMock, patch

        # Build a proper async mock for the pool
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])  # empty table

        pool = MagicMock()
        pool.acquire = MagicMock()
        pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

        await init_code_agent_deps(pool, repo_base="/repos")

        # At minimum, ASTParser should be set (zero external deps)
        assert dep.get_ast_parser() is not None
        # RipgrepExecutor should have empty repo_paths
        executor = dep.get_ripgrep_executor()
        assert executor._repo_paths == {}
