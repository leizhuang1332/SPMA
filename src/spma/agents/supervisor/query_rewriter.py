"""Supervisor 查询改写器——标准化、扩展、分解。"""

import json
import logging

logger = logging.getLogger(__name__)


async def rewrite_queries(
    query: str,
    classification: dict,
    entities: dict,
    llm,
    synonym_map: dict | None = None,
) -> dict[str, str]:
    result: dict[str, str] = {"original": query}
    sources = classification.get("sources", [])
    is_cross_source = classification.get("is_cross_source", False)

    # Short query expansion (<=30 chars)
    if len(query) <= 30 and llm is not None:
        try:
            expanded = await _expand_query(query, llm)
            if expanded:
                result["expanded"] = expanded
        except Exception as e:
            logger.warning(f"查询扩展失败: {e}")

    # Cross-source decomposition
    if is_cross_source and len(sources) > 1 and llm is not None:
        try:
            sub_queries = await _decompose_query(query, entities, sources, llm)
            for sq in sub_queries:
                target = sq.get("target", "")
                if target in sources:
                    result[target] = sq.get("query", query)
        except Exception as e:
            logger.warning(f"查询分解失败: {e}")

    for source in sources:
        if source not in result:
            result[source] = result.get("expanded", query)

    return result


async def _expand_query(query: str, llm) -> str:
    prompt = f"为以下用户查询生成 3-5 个相关的搜索关键词或术语（仅输出关键词列表，用逗号分隔）。\n查询: {query}\n关键词:"
    resp = await llm.generate(prompt)
    keywords = [k.strip() for k in resp.split(",") if k.strip()]
    return f"{query} {' '.join(keywords[:5])}"


async def _decompose_query(query: str, entities: dict, sources: list[str], llm) -> list[dict]:
    entities_str = str({k: v for k, v in entities.items() if v})
    prompt = f"""将以下复杂查询分解为 2-4 个独立的子查询，每个子查询面向单一数据源。
已抽取实体: {entities_str}
可用数据源: {', '.join(sources)}
用户查询: {query}
输出 JSON: [{{"query": "子查询", "target": "doc|code|sql"}}, ...]"""
    resp = await llm.generate(prompt)
    try:
        return json.loads(resp)
    except json.JSONDecodeError:
        return []
