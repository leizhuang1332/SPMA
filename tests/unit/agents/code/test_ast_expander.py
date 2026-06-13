import pytest
from pathlib import Path
from spma.agents.code.ast_expander import expand_via_ast


class MockASTParser:
    def __init__(self, results=None):
        self.results = results or {}
    async def parse_file(self, file_path):
        return self.results.get(file_path, {"calls": [], "called_by": [], "imports": []})


@pytest.mark.anyio
class TestASTExpander:
    async def test_returns_empty_without_parser(self):
        results = [{"repo": "backend", "file_path": "src/auth.py"}]
        expanded = await expand_via_ast(results, {"backend": "/tmp/fake_repo"}, ast_parser=None)
        assert expanded == []

    async def test_returns_empty_without_results(self):
        expanded = await expand_via_ast([], {}, ast_parser=MockASTParser())
        assert expanded == []

    async def test_expands_call_targets(self):
        repo_root = Path("/tmp/fake_repo")
        src_dir = repo_root / "src"
        src_dir.mkdir(parents=True, exist_ok=True)
        auth_file = src_dir / "auth.py"
        auth_file.write_text("def dummy(): pass")

        parser = MockASTParser({str(auth_file): {"calls": [{"file": "src/token.py"}], "called_by": [], "imports": []}})
        results = [{"repo": "backend", "file_path": "src/auth.py"}]
        expanded = await expand_via_ast(results, {"backend": str(repo_root)}, ast_parser=parser)
        assert len(expanded) >= 1
