"""Code Agent ripgrep 搜索执行器——分层执行 exact→stem→fuzzy→llm_retry。"""

import asyncio
import json
import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

RG_BASE_ARGS = ["rg", "--json", "--no-heading", "--color", "never", "--max-count", "50"]
RG_MAX_DEPTH = ["--max-depth", "10"]

# 敏感路径检查规则（design-13 §8 风险缓解）
# 5 类模式：.env / secrets.* / .git/ / *.pem / *.key
# 用结构化数据声明：(predicate_kind, value)
SENSITIVE_PATH_RULES: list[tuple[str, str]] = [
    ("filename_eq", ".env"),
    ("filename_startswith", "secrets."),
    ("path_contains", ".git"),
    ("filename_endswith", ".pem"),
    ("filename_endswith", ".key"),
]


def _is_sensitive_path(file_path: str) -> bool:
    """检查路径是否匹配敏感路径黑名单。

    5 条规则（数据驱动）：.env / secrets.* / .git / *.pem / *.key
    自定义实现而非 fnmatch 因为 fnmatch 不支持 **，无法匹配根级 .env / .key
    （fnmatch.fnmatch('.env', '**/.env') 返回 False）。
    """
    parts = file_path.split("/")
    filename = parts[-1]
    for kind, value in SENSITIVE_PATH_RULES:
        if kind == "filename_eq" and filename == value:
            return True
        if kind == "filename_startswith" and filename.startswith(value):
            return True
        if kind == "filename_endswith" and filename.endswith(value):
            return True
        if kind == "path_contains" and value in parts:
            return True
    return False


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
                    try:
                        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self._timeout)
                    except asyncio.TimeoutError:
                        proc.terminate()
                        try:
                            await asyncio.wait_for(proc.wait(), timeout=2.0)
                        except asyncio.TimeoutError:
                            proc.kill()
                            await proc.wait()
                        logger.warning(f"git log timeout for {repo_name} tag={tag}")
                        continue
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
                except Exception as e:
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
                try:
                    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self._timeout)
                except asyncio.TimeoutError:
                    proc.terminate()
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=2.0)
                    except asyncio.TimeoutError:
                        proc.kill()
                        await proc.wait()
                    logger.warning(f"rg timeout for {repo_name} term={term}")
                    continue
                if proc.returncode not in (0, 1):
                    logger.warning(f"rg exited {proc.returncode} for {repo_name}: {stderr.decode('utf-8', errors='replace')[:200]}")
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
            except Exception as e:
                logger.error(f"rg error for {repo_name}: {e}")

        return results

    async def glob_files(self, pattern: str, candidate_repos: list[str]) -> list[dict]:
        """Glob 模式匹配，发现目录结构。

        Args:
            pattern: glob 模式（如 "**/*.py"）
            candidate_repos: 候选仓库名列表

        Returns:
            [{"repo": str, "file_path": str}, ...]
            敏感路径（.env / secrets.* / .git/ / *.pem / *.key）被过滤
        """
        results: list[dict] = []
        for repo_name in candidate_repos:
            repo_path = self._repo_paths.get(repo_name)
            if not repo_path:
                continue
            try:
                proc = await asyncio.create_subprocess_exec(
                    "rg", "--files", "--glob", pattern, repo_path,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self._timeout)
            except asyncio.TimeoutError:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
                logger.warning(f"glob_files timeout for {repo_name} pattern={pattern}")
                continue
            except Exception as e:
                logger.error(f"glob_files error for {repo_name}: {e}")
                continue
            if proc.returncode not in (0, 1):
                logger.warning(f"rg --files exited {proc.returncode} for {repo_name}: {stderr.decode('utf-8', errors='replace')[:200]}")
                continue
            for line in stdout.decode("utf-8", errors="replace").strip().split("\n"):
                if not line:
                    continue
                # 转为相对路径（Windows 跨驱动器会抛 ValueError，try 内保护）
                try:
                    rel_path = os.path.relpath(line, repo_path)
                except ValueError:
                    rel_path = line  # 兜底：保留绝对路径（仅用于过滤判定）
                if _is_sensitive_path(rel_path):
                    continue
                results.append({"repo": repo_name, "file_path": rel_path})
        return results

    async def read_files(self, files: list[dict]) -> list[dict]:
        """读取指定文件内容。

        Args:
            files: [{"repo": str, "file_path": str}, ...]

        Returns:
            [{"repo": str, "file_path": str, "content": str}, ...]
            敏感路径被过滤；I/O 错误静默跳过。
        """
        results: list[dict] = []
        for f in files:
            if _is_sensitive_path(f["file_path"]):
                continue
            repo_path = self._repo_paths.get(f["repo"])
            if not repo_path:
                continue
            full_path = os.path.join(repo_path, f["file_path"])
            try:
                with open(full_path, "r", encoding="utf-8", errors="ignore") as fp:
                    content = fp.read()
                results.append({
                    "repo": f["repo"],
                    "file_path": f["file_path"],
                    "content": content,
                })
            except Exception as e:
                logger.warning(f"read_files failed for {full_path}: {e}")
                continue
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
