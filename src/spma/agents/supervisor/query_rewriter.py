"""Supervisor 查询改写器——标准化、扩展、分解。"""

import hashlib
import json
import logging

logger = logging.getLogger(__name__)


def _history_fingerprint(conversation_history: str) -> str:
    """取最近 3 轮作为 fingerprint(sha256[:16]);空历史返回 'none'。"""
    if not conversation_history:
        return "none"
    turns = [t for t in conversation_history.splitlines() if t.strip()][-3:]
    return hashlib.sha256("|".join(turns).encode("utf-8")).hexdigest()[:16]


async def rewrite_queries(
    query: str,
    classification: dict,
    entities: dict,
    llm,
    synonym_map: dict | None = None,
    conversation_history: str = "",
    *,
    cache=None,
    audit_buffer=None,
    weights_version: int = 1,
    synonym_version: int = 1,
) -> dict[str, str]:
    """
    查询重写主函数 - 五阶段管道 + 可选缓存

    参数(新增):
        cache: QueryCache 实例,None 时禁用缓存
        audit_buffer: QrAuditBuffer 实例,None 时禁用审计
        weights_version: 当前权重版本号(参与 cache key)
        synonym_version: 当前 synonym 版本号(参与 cache key)
    """
    if cache is not None:
        async def _compute(query: str, entities: dict) -> dict:
            return await _do_rewrite_pipeline(
                query, classification, entities, llm, synonym_map, conversation_history
            )

        history_fp = _history_fingerprint(conversation_history)
        cached = await cache.lookup_or_compute(
            query=query,
            history_fingerprint=history_fp,
            entities=entities,
            weights_version=weights_version,
            synonym_version=synonym_version,
            compute=_compute,
        )
        result = dict(cached)

        if audit_buffer is not None:
            await audit_buffer.enqueue({
                "request_id": hashlib.md5(
                    (query + history_fp).encode()).hexdigest(),
                "ts": None,  # 由 audit buffer 补
                "query_hash": hashlib.sha256(query.encode()).hexdigest()[:16],
                "rewritten_hash": hashlib.sha256(
                    (result.get("expanded") or "").encode()).hexdigest()[:16],
                "pii_types": [],
                "stage": "rewrite",
                "strategy_weights": None,
                "weights_version": weights_version,
                "synonym_version": synonym_version,
                "latency_ms": 0,  # 调用方可在 graph 层补
                "cache_hit_l1": cached.get("cache_layer") == "l1",
                "cache_hit_l2": cached.get("cache_layer") == "l2",
                "cache_layer": cached.get("cache_layer"),
                "error_stage": None,
                "fallback_level": None,
            })
        return result
    # cache=None 走原 5 阶段管道
    return await _do_rewrite_pipeline(
        query, classification, entities, llm, synonym_map, conversation_history
    )


async def _do_rewrite_pipeline(
    query, classification, entities, llm, synonym_map, conversation_history,
) -> dict:
    """原 rewrite_queries 主体(去掉外层 cache wrap)."""
    result: dict[str, str] = {"original": query}

    # 阶段一：同义词标准化
    normalized = await _normalize_with_synonyms(query, synonym_map, entities)
    result["normalized"] = normalized

    # 阶段二：指代消解
    resolved = await _resolve_references(normalized, conversation_history, llm)
    result["resolved"] = resolved

    # 阶段三：查询扩展（触发条件：查询长度 <= 50 或 query_type == "search"）
    # 注意：阈值从 30 调整为 50，以覆盖更多中等长度查询的扩展场景
    query_type = classification.get("query_type", "search")
    sources = classification.get("sources", [])
    is_cross_source = classification.get("is_cross_source", False)

    should_expand = len(query) <= 50 or query_type == "search"
    if should_expand and llm:
        expanded = await _expand_query(resolved, classification, entities, llm)
        result["expanded"] = expanded
    else:
        result["expanded"] = resolved

    # 阶段四：查询分解（仅跨源时执行）
    if is_cross_source and len(sources) > 1 and llm:
        try:
            sub_queries = await _decompose_query(resolved, entities, sources, llm)
            for sq in sub_queries:
                target = sq.get("target", "")
                if target in sources:
                    result[target] = sq.get("query", resolved)
        except Exception as e:
            logger.warning(f"查询分解失败: {e}")
            # Fallback: 各 source 使用扩展后的查询
            for source in sources:
                result[source] = result.get("expanded", resolved)
    else:
        # 非跨源或无 LLM 时，各 source 使用扩展后的查询
        for source in sources:
            result[source] = result.get("expanded", resolved)

    # 日志记录
    logger.info(f"Query rewrite: original={query[:50]}, "
                f"sources={sources}, "
                f"expanded={result.get('expanded', '')[:50] if result.get('expanded') else None}")

    return result


async def _decompose_query(
    query: str,
    entities: dict,
    sources: list[str],
    llm,
) -> list[dict]:
    """跨源查询分解：多层级容错"""
    if not sources:
        return []

    # 无 LLM 时返回默认子查询
    if not llm:
        return [{"query": query, "target": source} for source in sources]

    # Guard against None entities
    if entities is None:
        entities = {}

    import re

    entities_str = str({k: v for k, v in entities.items() if v})

    prompt = f"""将以下复杂查询分解为 {len(sources)} 个独立的子查询，每个子查询面向单一数据源。

已抽取实体: {entities_str}
可用数据源: {', '.join(sources)}
用户查询: {query}

输出格式要求：
- 必须输出合法的 JSON 数组
- 每个元素包含 "query" 和 "target" 两个字段
- "target" 必须是 {', '.join(sources)} 中的一个
- 子查询应覆盖原始查询的所有核心意图

输出示例：
[{{"query": "子查询1", "target": "doc"}}, {{"query": "子查询2", "target": "code"}}]"""

    try:
        resp_obj = await llm.ainvoke(prompt)
        resp = resp_obj.content

        # 策略1：直接 JSON 解析
        try:
            return json.loads(resp)
        except json.JSONDecodeError:
            pass

        # 策略2：正则提取 JSON 数组
        json_match = re.search(r'\[.*\]', resp, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                pass

        # 策略3：提取键值对
        target_patterns = {
            source: re.search(rf'{source}[\s:]+["\']([^"\']+)["\']', resp)
            for source in sources
        }
        result = []
        for source, pattern in target_patterns.items():
            if pattern:
                result.append({"query": pattern.group(1), "target": source})

        if result:
            return result

        # 策略4：兜底——每个 source 返回原始查询
        return [{"query": query, "target": source} for source in sources]

    except Exception:
        return [{"query": query, "target": source} for source in sources]


async def _normalize_with_synonyms(
    query: str,
    synonym_map: dict | None,
    entities: dict,
) -> str:
    """同义词标准化：用户用语 → 系统标准术语"""
    if not synonym_map:
        return query

    normalized = query

    # 基于 synonym_map 的术语替换（按术语长度降序排列，确保长术语优先匹配）
    for user_term, system_terms in sorted(synonym_map.items(), key=lambda x: len(x[0]), reverse=True):
        if user_term in normalized:
            normalized = normalized.replace(user_term, " ".join(system_terms))

    # 基于实体的精确映射
    entity_terms = []
    for key in ["table_names", "column_names", "code_refs", "req_ids"]:
        if key in entities and entities[key]:
            entity_terms.extend(entities[key])

    if entity_terms:
        existing_terms = set(normalized.lower().split())
        new_terms = [t for t in entity_terms if t.lower() not in existing_terms]
        if new_terms:
            normalized = f"{normalized} {' '.join(new_terms)}"

    return normalized.strip()


async def _resolve_references(
    query: str,
    conversation_history: str,
    llm,
) -> str:
    """指代消解：基于对话历史解析指代性表达式"""
    if not conversation_history:
        return query

    reference_patterns = ["这个", "那个", "上次", "之前", "刚才", "上述", "此"]
    has_reference = any(pattern in query for pattern in reference_patterns)

    if not has_reference:
        return query

    if not llm:
        return query

    prompt = f"""你是一个上下文理解助手。请根据对话历史，将以下查询中的指代性表达式还原为具体内容。

对话历史：
{conversation_history}

当前查询：
{query}

要求：
1. 将"这个问题"、"那个需求"等指代性表达式替换为具体内容
2. 保持查询的核心语义不变
3. 输出还原后的完整查询，不要添加额外解释"""

    try:
        resp_obj = await llm.ainvoke(prompt)
        return resp_obj.content.strip()
    except Exception as e:
        logger.warning(f"指代消解失败: {e}")
        return query  # Fallback to original query on error


async def _expand_query(
    query: str,
    classification: dict,
    entities: dict,
    llm,
) -> str:
    """基于意图的查询扩展"""
    if not llm:
        return query

    # Guard against None entities
    if entities is None:
        entities = {}

    query_type = classification.get("query_type", "search")

    if query_type == "search":
        prompt = f"""为以下搜索查询生成扩展查询，保留核心语义，增加相关术语和实体。

查询: {query}
已识别实体: {entities}
要求:
1. 保留原始查询的核心语义
2. 增加相关的技术术语和实体名称
3. 输出一个扩展后的完整查询（不是关键词列表）
4. 查询长度控制在原查询的 1.5-2 倍"""

    elif query_type == "data_query":
        prompt = f"""将以下数据查询扩展为更精确的查询，包含表名、字段名等技术术语。

查询: {query}
已知表: {entities.get('table_names', [])}
已知字段: {entities.get('column_names', [])}
已知指标: {entities.get('metrics', [])}
要求:
1. 将中文术语转换为可能的表名/字段名
2. 保留原始查询的统计意图
3. 输出扩展后的查询"""

    elif query_type == "explain":
        prompt = f"""将以下解释性查询扩展为更详细的查询，增加相关的技术概念和实现细节。

查询: {query}
已识别实体: {entities}
要求:
1. 保留原始查询的解释意图
2. 增加相关的技术概念和实现细节
3. 输出扩展后的查询"""

    elif query_type == "trace":
        prompt = f"""将以下追踪查询扩展为更精确的查询，包含具体的追踪路径和关联实体。

查询: {query}
已识别实体: {entities}
要求:
1. 保留原始查询的追踪意图
2. 增加具体的追踪路径和关联实体
3. 输出扩展后的查询"""

    else:
        return query

    try:
        resp_obj = await llm.ainvoke(prompt)
        expanded = resp_obj.content.strip()

        # 质量校验：质量低于 0.5 时回退到原查询
        if await _evaluate_quality(query, expanded, llm) < 0.5:
            return query

        return expanded
    except Exception as e:
        logger.warning(f"查询扩展失败: {e}")
        return query


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
