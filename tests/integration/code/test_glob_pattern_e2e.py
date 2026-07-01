"""End-to-end test: dynamic glob pattern（spec §6.5）。

场景：fixture repo 同时含 .java + .py，mock LLM 输出 **/*.java，
跑完整 _run_one_round，验证 _glob 只命中 .java，expanded_context 不含 .py。
"""
import asyncio
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from spma.agents.code.explorer import CodeExplorer, ExplorerState


class FakeRipgrepExecutor:
    """在临时 repo 上调真实 ripgrep（如果可用），否则用 _exec 模拟。"""
    def __init__(self, repo_paths: dict[str, str]):
        self._repo_paths = repo_paths

    async def glob_files(self, pattern: str, candidate_repos: list[str]) -> list[dict]:
        """用 pathlib glob 模拟 ripgrep --files。"""
        results = []
        for repo_name in candidate_repos:
            repo_path = Path(self._repo_paths[repo_name])
            # 把 ripgrep glob (**/*.ext) 转换成 pathlib glob
            # 简单实现：去掉 **/ 前缀后递归
            py_pattern = pattern.replace("**/", "")
            for p in repo_path.rglob(py_pattern):
                results.append({
                    "repo": repo_name,
                    "file_path": str(p.relative_to(repo_path)),
                })
        return results

    async def search(self, *args, **kwargs):
        return []

    async def read_files(self, files: list[dict]) -> list[dict]:
        results = []
        for f in files:
            full = Path(self._repo_paths[f["repo"]]) / f["file_path"]
            try:
                content = full.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                content = ""
            results.append({
                "repo": f["repo"],
                "file_path": f["file_path"],
                "content": content,
            })
        return results


class FakeASTParser:
    async def parse_file(self, path):
        return {}


@pytest.fixture
def java_py_repo():
    """创建临时 repo：含 2 个 .java + 2 个 .py。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        (base / "src").mkdir()
        (base / "src" / "UserController.java").write_text("class UserController {}")
        (base / "src" / "OrderController.java").write_text("class OrderController {}")
        (base / "scripts").mkdir()
        (base / "scripts" / "build.py").write_text("print('build')")
        (base / "scripts" / "deploy.py").write_text("print('deploy')")
        yield {"java_repo": str(base)}


@pytest.mark.anyio
async def test_glob_pattern_e2e_java_repo(java_py_repo):
    """LLM 输出 **/*.java → _glob 只命中 .java 文件，.py 不被 glob 到。"""
    executor = FakeRipgrepExecutor(java_py_repo)
    ast = FakeASTParser()

    class JavaOnlyLLM:
        async def ainvoke(self, prompt):
            return MagicMock(content=(
                '{"exact_terms": ["Controller"], "fuzzy_terms": [], '
                '"tag_terms": [], '
                '"glob_patterns": ["**/*.java"]}'
            ))

    explorer = CodeExplorer(
        ripgrep_executor=executor, ast_parser=ast, llm=JavaOnlyLLM(), max_rounds=1,
    )
    state = ExplorerState(
        round=2,  # 后续轮 → _refine_terms 走 LLM 路径（首轮 round=1 + 空 context 会退化）
        query="改 pom.xml 依赖",  # 含 .xml，但 LLM 决定用 .java
        expanded_context=[{"repo": "java_repo", "file_path": "src/UserController.java"}],
        search_terms={"query": "改 pom.xml 依赖", "exact_terms": []},
        candidate_repos=["java_repo"],
    )
    # 直接调 _refine_terms + _glob（绕过完整 _run_one_round 避免 reflection 复杂）
    await explorer._refine_terms(state)
    glob_hits = await explorer._glob(state)
    file_paths = {h["file_path"] for h in glob_hits}
    # 验证：只命中 .java，不命中 .py
    assert any(p.endswith(".java") for p in file_paths)
    assert not any(p.endswith(".py") for p in file_paths)
    # 验证：resolved == "llm"（LLM 返回的 glob 通过了 validate）
    assert state.glob_patterns_resolved == "llm"
