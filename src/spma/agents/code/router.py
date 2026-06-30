"""Code Agent 路由层——Stage 0/1/2 三段式（design-13 §3.1 + §3.3）。

主路径：repo_registry（DB 单一真相源）+ LLM 精排；
兜底：file_path_cache 的 exact_file_match / module_lookup / broad_search。
"""
import json
import logging
import re

logger = logging.getLogger(__name__)


def _parse_llm_json(content: str) -> dict | None:
    """从 LLM 响应中提取 JSON。容忍 markdown code block 包裹。"""
    if not content:
        return None
    # 去掉 markdown code block
    content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return None


async def route_repos(
    entities: dict,
    file_path_cache,
    max_candidates: int = 5,
    *,
    query: str = "",                # 新增：用户原始查询
    repo_registry=None,             # 新增：RepoRegistry 实例（主路径）
    llm=None,                       # 新增：可选 LLM
    two_stage_threshold: int = 5,   # 新增：仓库数 > 此阈值走两阶段
) -> dict:
    """根据用户查询和实体信息路由到候选仓库。

    Stage 0 决策：
        if repo_registry is None:
            → 旧路径（exact_file_match / module_lookup / broad_search）
        elif len(active_repos) <= two_stage_threshold:
            → 单阶段 LLM 路由（route_method="db_registry_match_single"）
        else:
            → 两阶段：Stage 1 pg_trgm → Stage 2 LLM 精排（"db_registry_match_two_stage"）
    """
    # 旧路径：repo_registry 为 None 时走向后兼容
    if repo_registry is None:
        return await _route_repos_legacy(entities, file_path_cache, max_candidates)

    # 新路径：Stage 0/1/2 三段式
    active_repos = await repo_registry.list_active_repos()
    if not active_repos:
        logger.warning("repo_registry 无 enabled 记录，降级到 broad_search")
        return await _route_repos_legacy(entities, file_path_cache, max_candidates)

    if len(active_repos) <= two_stage_threshold:
        candidates = active_repos
        route_method = "db_registry_match_single"
    else:
        candidates = await repo_registry.list_repos_by_keyword(query or "", top_k=20)
        route_method = "db_registry_match_two_stage"

    # Stage 2：LLM 精排（llm=None 时直接返回 candidates）
    if llm is None or not query:
        selected = [r.repo_name for r in candidates][:max_candidates]
        confidence = "high" if len(selected) <= 3 else "medium"
        return {
            "candidate_repos": selected,
            "route_method": route_method,
            "route_confidence": confidence,
        }

    # Stage 2 LLM 精排
    repo_list = "\n".join([
        f"- {r.repo_name}（{r.display_name}）：{r.description}（关键词：{', '.join(r.tags)}）"
        for r in candidates
    ])
    prompt = f"""根据用户查询，选择最相关的代码仓库：

用户查询：{query}

仓库列表：
{repo_list}

请输出 JSON：{{"repo_names": ["仓库名1", "..."], "reason": "..."}}"""

    try:
        resp = await llm.ainvoke(prompt)
        parsed = _parse_llm_json(resp.content)
    except Exception as e:
        logger.warning(f"Stage 2 LLM 调用失败: {e}，降级到 module_lookup")
        return await _route_repos_legacy(entities, file_path_cache, max_candidates)

    if not parsed or "repo_names" not in parsed:
        logger.warning("Stage 2 LLM 返回 JSON 解析失败，降级到 module_lookup")
        return await _route_repos_legacy(entities, file_path_cache, max_candidates)

    # 过滤不在 candidates 中的仓库名（防 LLM 幻觉）
    valid_names = {r.repo_name for r in candidates}
    selected = [n for n in parsed["repo_names"] if n in valid_names][:max_candidates]
    if not selected:
        logger.warning("Stage 2 LLM 返回仓库名都不在 candidates 中，降级到 broad_search")
        return await _route_repos_legacy(entities, file_path_cache, max_candidates)

    confidence = "high" if len(selected) <= 3 else "medium"
    return {
        "candidate_repos": selected,
        "route_method": route_method,
        "route_confidence": confidence,
    }


async def _route_repos_legacy(
    entities: dict, file_path_cache, max_candidates: int = 5,
) -> dict:
    """旧路径：file_path_cache 走 exact_file_match / module_lookup / broad_search。
    与原 route_repos 行为完全一致（向后兼容）。
    """
    code_refs = entities.get("code_refs", []) or []
    module = entities.get("module", "")

    candidate_repos: set[str] = set()

    # 1. code_refs 精确匹配
    try:
        for ref in code_refs[:3]:
            matches = await file_path_cache.query_files(ref, limit=5)
            for m in matches:
                candidate_repos.add(m["repo_name"])
    except Exception:
        logger.warning("code_refs 路由查询失败，降级到 module 路由", exc_info=True)

    if candidate_repos:
        return {
            "candidate_repos": list(candidate_repos)[:max_candidates],
            "route_method": "exact_file_match",
            "route_confidence": "high" if len(candidate_repos) <= 3 else "medium",
        }

    # 2. module 映射
    if module:
        try:
            matches = await file_path_cache.query_files(module, limit=10)
            for m in matches:
                candidate_repos.add(m["repo_name"])
        except Exception:
            logger.warning("module 路由查询失败，降级到兜底路由", exc_info=True)

    if candidate_repos:
        return {
            "candidate_repos": list(candidate_repos)[:max_candidates],
            "route_method": "module_lookup",
            "route_confidence": "medium",
        }

    # 3. 兜底
    try:
        all_repos = await file_path_cache.list_repos()
        return {
            "candidate_repos": all_repos[:max_candidates],
            "route_method": "broad_search",
            "route_confidence": "low",
        }
    except Exception:
        logger.warning("兜底 list_repos 查询失败", exc_info=True)
        return {
            "candidate_repos": [],
            "route_method": "broad_search",
            "route_confidence": "low",
        }
