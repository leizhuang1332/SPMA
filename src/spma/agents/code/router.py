"""文件路径路由——通过 file_path_cache 表将查询路由到候选仓库。

设计依据: SPMA-design-03 文件路径路由
"""

import logging

logger = logging.getLogger(__name__)


async def route_repos(
    entities: dict,
    file_path_cache,  # FilePathCache instance
    max_candidates: int = 5,
) -> dict:
    """根据实体信息从 file_path_cache 中路由到候选仓库。

    Args:
        entities: WorkerEntities dict with code_refs, module, etc.
        file_path_cache: FilePathCache 实例，提供 query_files 方法
        max_candidates: 最多返回的候选仓库数

    Returns:
        dict with keys:
        - candidate_repos: list[str] — 候选仓库名
        - route_method: str — "exact_file_match" | "module_lookup" | "broad_search"
        - route_confidence: str — "high" | "medium" | "low"
    """
    code_refs = entities.get("code_refs", []) or []
    module = entities.get("module")

    candidate_repos: set[str] = set()

    # 1. code_refs 精确匹配 → 直接定位仓库
    for ref in code_refs[:3]:
        matches = await file_path_cache.query_files(ref, limit=5)
        for m in matches:
            candidate_repos.add(m["repo_name"])

    if candidate_repos:
        logger.info(f"code_refs 路由: {list(candidate_repos)[:max_candidates]}")
        return {
            "candidate_repos": list(candidate_repos)[:max_candidates],
            "route_method": "exact_file_match",
            "route_confidence": "high" if len(candidate_repos) <= 3 else "medium",
        }

    # 2. module 映射 → 查 repo_registry 的 dir_module_map
    if module:
        matches = await file_path_cache.query_files(module, limit=10)
        for m in matches:
            candidate_repos.add(m["repo_name"])

    if candidate_repos:
        logger.info(f"module 路由: {list(candidate_repos)[:max_candidates]}")
        return {
            "candidate_repos": list(candidate_repos)[:max_candidates],
            "route_method": "module_lookup",
            "route_confidence": "medium",
        }

    # 3. 兜底：返回所有已注册仓库
    all_repos = await file_path_cache.list_repos()
    logger.info(f"兜底路由: {len(all_repos)} repos")
    return {
        "candidate_repos": all_repos[:max_candidates],
        "route_method": "broad_search",
        "route_confidence": "low",
    }
