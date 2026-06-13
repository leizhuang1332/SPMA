"""Code Agent ripgrep 搜索执行器——分层执行 exact→stem→fuzzy→llm_retry。"""

import asyncio
import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

RG_BASE_ARGS = ["rg", "--json", "--no-heading", "--color", "never", "--max-count", "50"]
RG_MAX_DEPTH = ["--max-depth", "10"]


class RipgrepExecutor:
    """ripgrep 搜索执行器。按 fallback_layer 逐层降级。"""

    def __init__(self, repo_paths: dict[str, str], timeout_seconds: float = 5.0):
        self._repo_paths = repo_paths
        self._timeout = timeout_seconds

    async def search(
        self,
        search_terms: dict,
        candidate_repos: list[str],
        fallback_layer: int = 0,
    ) -> list[dict]:
        """按 fallback_layer 执行分层搜索。
        0=exact, 1=stem, 2=fuzzy, 3=llm_retry
        Returns list of {repo, file_path, line_number, match_text, match_type, confidence}
        """
        all_results: list[dict] = []

        if fallback_layer == 0:
            terms = search_terms.get("exact_terms", [])
            for term in terms:
                results = await self._rg_search(term, candidate_repos, exact=True, case_sensitive=False)
                for r in results:
                    r["match_type"] = "exact"
                    r["confidence"] = 0.95
                all_results.extend(results)
                if len(all_results) >= 10:
                    break

        elif fallback_layer == 1:
            terms = search_terms.get("exact_terms", []) + search_terms.get("fuzzy_terms", [])
            for term in terms:
                parts = self._stem_split(term)
                for part in parts:
                    if len(part) >= 3:
                        results = await self._rg_search(part, candidate_repos, exact=False, case_sensitive=False)
                        for r in results:
                            r["match_type"] = "stem"
                            r["confidence"] = 0.7
                        all_results.extend(results)

        elif fallback_layer == 2:
            terms = search_terms.get("exact_terms", []) + search_terms.get("fuzzy_terms", [])
            for term in terms:
                results = await self._rg_search(term, candidate_repos, exact=False, case_sensitive=False)
                for r in results:
                    r["match_type"] = "fuzzy"
                    r["confidence"] = 0.4
                all_results.extend(results)

        elif fallback_layer == 3:
            terms = search_terms.get("fuzzy_terms", []) + search_terms.get("exact_terms", [])
            for term in terms:
                results = await self._rg_search(term, candidate_repos, exact=False, case_sensitive=False)
                for r in results:
                    r["match_type"] = "llm_retry"
                    r["confidence"] = 0.3
                all_results.extend(results)

        # Deduplicate by (repo, file_path, line_number)
        seen = set()
        deduped = []
        for r in all_results:
            key = (r["repo"], r["file_path"], r["line_number"])
            if key not in seen:
                seen.add(key)
                deduped.append(r)
        return deduped[:50]

    async def search_gitlog(
        self, tag_terms: list[str], candidate_repos: list[str],
    ) -> list[dict]:
        """Search git log for tag_terms (req_ids, author)."""
        results: list[dict] = []
        for repo_name in candidate_repos:
            repo_path = self._repo_paths.get(repo_name)
            if not repo_path:
                continue
            for tag in tag_terms:
                if tag.startswith("author:"):
                    author = tag[7:]
                    cmd = ["git", "-C", repo_path, "log", "--author", author, "--oneline", "-n", "20"]
                else:
                    cmd = ["git", "-C", repo_path, "log", "--grep", tag, "--oneline", "-n", "20"]
                try:
                    proc = await asyncio.create_subprocess_exec(
                        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                    )
                    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self._timeout)
                    if proc.returncode == 0:
                        for line in stdout.decode("utf-8", errors="replace").strip().split("\n"):
                            if line:
                                parts = line.split(" ", 1)
                                results.append({
                                    "repo": repo_name,
                                    "commit_hash": parts[0] if parts else "",
                                    "commit_message": parts[1] if len(parts) > 1 else "",
                                    "match_type": "gitlog",
                                    "confidence": 0.9 if tag.startswith("author:") else 0.85,
                                })
                except (asyncio.TimeoutError, Exception) as e:
                    logger.warning(f"git log failed for {repo_name} tag={tag}: {e}")
        return results

    async def _rg_search(
        self, term: str, repos: list[str], exact: bool = True, case_sensitive: bool = False,
    ) -> list[dict]:
        """Execute ripgrep search across candidate repos."""
        if not term or len(term) < 2:
            return []

        results: list[dict] = []
        for repo_name in repos:
            repo_path = self._repo_paths.get(repo_name)
            if not repo_path:
                continue

            args = list(RG_BASE_ARGS)
            if exact:
                args.extend(["-w", "-F"])
            if not case_sensitive:
                args.append("-i")
            args.extend(RG_MAX_DEPTH)
            args.append(term)
            args.append(repo_path)

            try:
                proc = await asyncio.create_subprocess_exec(
                    *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self._timeout)
                if proc.returncode not in (0, 1):
                    logger.warning(f"rg exited {proc.returncode} for {repo_name}: {stderr.decode()[:200]}")
                    continue

                for line in stdout.decode("utf-8", errors="replace").strip().split("\n"):
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        if data.get("type") == "match":
                            match_data = data.get("data", {})
                            path = match_data.get("path", {})
                            file_path = path.get("text", "")
                            line_number = data.get("line_number") or match_data.get("line_number", 0)
                            lines = match_data.get("lines", {})
                            match_text = lines.get("text", "").strip()
                            results.append({
                                "repo": repo_name,
                                "file_path": file_path,
                                "line_number": line_number,
                                "match_text": match_text[:200],
                            })
                    except (json.JSONDecodeError, KeyError):
                        continue
            except asyncio.TimeoutError:
                logger.warning(f"rg timeout for {repo_name} term={term}")
            except Exception as e:
                logger.error(f"rg error for {repo_name}: {e}")

        return results

    @staticmethod
    def _stem_split(term: str) -> list[str]:
        """Split CamelCase and snake_case terms into stems."""
        parts = []
        if "_" in term:
            parts.extend(term.split("_"))
        camel_parts = re.findall(r'[A-Z]?[a-z]+|[A-Z]+(?=[A-Z][a-z]|\d|\b)', term)
        if camel_parts and len(camel_parts) > 1:
            parts.extend(p.lower() for p in camel_parts)
        if not parts:
            parts.append(term.lower())
        return list(set(parts))
