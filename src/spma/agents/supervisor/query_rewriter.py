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
    resp_obj = await llm.ainvoke(prompt)
    resp = resp_obj.content
    keywords = [k.strip() for k in resp.split(",") if k.strip()]
    return f"{query} {' '.join(keywords[:5])}"


async def _decompose_query(query: str, entities: dict, sources: list[str], llm) -> list[dict]:
    entities_str = str({k: v for k, v in entities.items() if v})
    prompt = f"""将以下复杂查询分解为 2-4 个独立的子查询，每个子查询面向单一数据源。
已抽取实体: {entities_str}
可用数据源: {', '.join(sources)}
用户查询: {query}
输出 JSON: [{{"query": "子查询", "target": "doc|code|sql"}}, ...]"""
    resp_obj = await llm.ainvoke(prompt)
    resp = resp_obj.content
    try:
        return json.loads(resp)
    except json.JSONDecodeError:
        return []


async def _normalize_with_synonyms(
    query: str,
    synonym_map: dict | None,
    entities: dict,
) -> str:
    """同义词标准化：用户用语 → 系统标准术语"""
    if not synonym_map:
        return query

    normalized = query

    # 基于 synonym_map 的术语替换
    for user_term, system_terms in synonym_map.items():
        if user_term in normalized:
            normalized = normalized.replace(user_term, " ".join(system_terms))

    # 基于实体的精确映射
    entity_terms = []
    for key in ["table_names", "column_names", "code_refs", "req_ids"]:
        if key in entities and entities[key]:
            entity_terms.extend(entities[key])

    if entity_terms:
        normalized = f"{normalized} {' '.join(entity_terms)}"

    return normalized.strip()


async def _evaluate_quality(
    original: str,
    rewritten: str,
    llm,
) -> float:
    """评估重写查询与原始查询的语义相似度（0-1）"""
    if not llm:
        return 0.5

    prompt = f"""评估以下重写查询是否保持了原始查询的核心语义。

评分标准：
- 1.0：完全一致，语义无偏差
- 0.8-0.9：略有扩展，但核心语义保持
- 0.5-0.7：有一定偏差，但仍相关
- < 0.5：语义偏差严重或完全无关

原始查询: {original}
重写查询: {rewritten}

评分(0-1):"""

    try:
        resp_obj = await llm.ainvoke(prompt)
        return float(resp_obj.content.strip())
    except (ValueError, AttributeError):
        return 0.5
