import pytest
from pathlib import Path
from spma.agents.code.graph import build_code_agent_graph
from spma.agents.code.state import CodeAgentState


class MockLLM:
    _call_count: int = 0
    async def generate(self, prompt):
        self._call_count += 1
        if self._call_count == 1:
            return '{"assessment": "insufficient", "reason": "need_more_files"}'
        return '{"assessment": "sufficient", "reason": "ok"}'
    async def is_available(self):
        return True
    async def reset(self):
        self._call_count = 0


class MockFilePathCache:
    async def query_files(self, keyword, limit=5):
        return [{"repo_name": "backend", "file_path": f"src/auth/{keyword}"}]
    async def list_repos(self):
        return ["backend"]


class MockRipgrepExecutor:
    def __init__(self):
        self._repo_paths = {"backend": "/fake/backend"}
    async def search(self, search_terms, candidate_repos, fallback_layer=0):
        return [
            {"repo": "backend", "file_path": "src/auth/oauth.py", "line_number": 42,
             "match_text": "def token_refresh(token):", "match_type": "exact", "confidence": 0.95},
            {"repo": "backend", "file_path": "src/auth/oauth.py", "line_number": 58,
             "match_text": "token = Token.objects.get(key=key)", "match_type": "exact", "confidence": 0.90},
            {"repo": "backend", "file_path": "src/auth/token.py", "line_number": 10,
             "match_text": "class Token(models.Model):", "match_type": "exact", "confidence": 0.92},
        ]
    async def search_gitlog(self, tag_terms, candidate_repos):
        return []


class MockASTParser:
    async def parse_file(self, file_path):
        return {"calls": [{"file": "src/auth/token.py"}], "called_by": [], "imports": ["django.db"]}


@pytest.mark.anyio
class TestCodeAgentLoop:
    async def test_l1_convergence_single_round(self):
        graph = build_code_agent_graph(
            file_path_cache=MockFilePathCache(),
            ripgrep_executor=MockRipgrepExecutor(),
            ast_parser=MockASTParser(),
            llm=MockLLM(),
        )
        initial_state: CodeAgentState = {
            "original_query": "oauth.py 的 token_refresh 函数",
            "entities": {"code_refs": ["oauth.py", "token_refresh"]},
            "round": 1,
            "fallback_layer": 0,
            "max_rounds": 3,
            "timeout_ms": 2000,
        }
        result = await graph.ainvoke(initial_state)
        assert result["assessment"] == "converge"
        assert result["convergence_reason"].startswith("L1")
        assert len(result.get("ripgrep_results", [])) >= 3

    async def test_expand_loop_when_insufficient(self, tmp_path):
        # Create real stub files so expand_via_ast path-check passes
        repo_base = tmp_path / "backend"
        auth_dir = repo_base / "src" / "auth"
        auth_dir.mkdir(parents=True)
        (auth_dir / "oauth.py").write_text("def token_refresh(): pass")
        (auth_dir / "token.py").write_text("class Token: pass")
        (auth_dir / "session.py").write_text("session_key = 'test'")

        call_count = [0]
        class SparseRipgrep(MockRipgrepExecutor):
            def __init__(self):
                self._repo_paths = {"backend": str(repo_base)}
            async def search(self, search_terms, candidate_repos, fallback_layer=0):
                call_count[0] += 1
                if call_count[0] == 1:
                    return [{"repo": "backend", "file_path": "src/auth/oauth.py",
                             "line_number": 42, "match_text": "token_refresh", "match_type": "fuzzy", "confidence": 0.5}]
                return [
                    {"repo": "backend", "file_path": "src/auth/oauth.py", "line_number": 42,
                     "match_text": "def token_refresh", "match_type": "exact", "confidence": 0.95},
                    {"repo": "backend", "file_path": "src/auth/token.py", "line_number": 10,
                     "match_text": "class Token", "match_type": "exact", "confidence": 0.90},
                    {"repo": "backend", "file_path": "src/auth/session.py", "line_number": 5,
                     "match_text": "session_key", "match_type": "stem", "confidence": 0.70},
                ]

        graph = build_code_agent_graph(
            file_path_cache=MockFilePathCache(),
            ripgrep_executor=SparseRipgrep(),
            ast_parser=MockASTParser(),
            llm=MockLLM(),
        )
        initial_state: CodeAgentState = {
            "original_query": "认证模块 token 管理",
            "entities": {"module": "认证"},
            "round": 1,
            "fallback_layer": 1,
            "max_rounds": 3,
            "timeout_ms": 2000,
        }
        result = await graph.ainvoke(initial_state)
        assert result["assessment"] == "converge"
        assert len(result.get("expanded_context", [])) >= 1
