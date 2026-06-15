"""Tests for code agent DI singletons in dependencies.py."""
import pytest


class TestCodeAgentDependencies:
    @pytest.fixture(autouse=True)
    def _reset_globals(self):
        """Reset all singletons to None before each test to prevent leakage."""
        import spma.api.dependencies as dep
        dep._db_pool = None
        dep._file_path_cache = None
        dep._ripgrep_executor = None
        dep._ast_parser = None

    def test_get_db_pool_raises_when_not_set(self):
        """get_db_pool should raise RuntimeError when not initialized."""
        from spma.api import dependencies as dep
        with pytest.raises(RuntimeError, match="db_pool not initialized"):
            dep.get_db_pool()

    def test_get_file_path_cache_raises_when_not_set(self):
        from spma.api import dependencies as dep
        dep.set_file_path_cache(None)
        with pytest.raises(RuntimeError, match="FilePathCache not initialized"):
            dep.get_file_path_cache()

    def test_get_ripgrep_executor_raises_when_not_set(self):
        from spma.api import dependencies as dep
        dep.set_ripgrep_executor(None)
        with pytest.raises(RuntimeError, match="RipgrepExecutor not initialized"):
            dep.get_ripgrep_executor()

    def test_get_ast_parser_raises_when_not_set(self):
        from spma.api import dependencies as dep
        dep.set_ast_parser(None)
        with pytest.raises(RuntimeError, match="ASTParser not initialized"):
            dep.get_ast_parser()

    def test_set_then_get_roundtrips(self):
        """After setting, get should return the same object."""
        from spma.api import dependencies as dep

        class FakePool:
            pass
        class FakeCache:
            pass
        class FakeRipgrep:
            pass
        class FakeParser:
            pass

        pool = FakePool()
        cache = FakeCache()
        ripgrep = FakeRipgrep()
        parser = FakeParser()

        dep.set_db_pool(pool)
        dep.set_file_path_cache(cache)
        dep.set_ripgrep_executor(ripgrep)
        dep.set_ast_parser(parser)

        assert dep.get_db_pool() is pool
        assert dep.get_file_path_cache() is cache
        assert dep.get_ripgrep_executor() is ripgrep
        assert dep.get_ast_parser() is parser
