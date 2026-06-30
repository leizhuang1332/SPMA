"""查询扩展多路策略。

- intent_aware:零 LLM,按 query_type 附加相关词
- synonym_based:零 LLM,用 synonym_map 扩展
- entity_injection:零 LLM,实体追加
- context_aware:LLM 生成(收编 _expand_query)
"""
import logging

logger = logging.getLogger(__name__)


_RELEVANT_WORDS = {
    "search": ["相关文档", "涉及"],
    "data_query": ["字段", "统计"],
    "explain": ["含义", "定义"],
    "trace": ["调用链", "流程"],
}

_SUPPORTED_TYPES = set(_RELEVANT_WORDS.keys())


async def intent_aware(query: str, classification: dict, entities: dict, **_) -> str | None:
    """基于意图的规则扩展:按 query_type 附加 1-2 个相关词。"""
    query_type = classification.get("query_type", "search")
    if query_type not in _SUPPORTED_TYPES:
        return None
    additions = [w for w in _RELEVANT_WORDS[query_type] if w not in query][:2]
    return (f"{query} {' '.join(additions)}") if additions else None


async def synonym_based(
    query: str, classification: dict, entities: dict,
    *, synonym_map=None, **_,
) -> str | None:
    """基于 synonym_map 扩展:命中 user_term → 追加 canonical_term。"""
    if not synonym_map:
        return None
    if not isinstance(synonym_map, dict):
        logger.warning(
            "synonym_based: synonym_map must be dict, got %s",
            type(synonym_map).__name__,
        )
        return None
    expanded = query
    added = 0
    for user_term, canonical_terms in synonym_map.items():
        if user_term in expanded:
            for ct in canonical_terms:
                if ct not in expanded:
                    expanded += f" {ct}"
                    added += 1
    return expanded if added > 0 else None


async def entity_injection(query: str, classification: dict, entities: dict, **_) -> str | None:
    """实体注入:把抽取的实体追加到 query。"""
    expanded = query
    added = 0
    for key in ["table_names", "column_names", "code_refs", "req_ids"]:
        for entity in entities.get(key, []):
            # 防御 None / 非字符串(上游 NER 偶发)
            if not entity or not isinstance(entity, str):
                continue
            if entity not in expanded:
                expanded += f" {entity}"
                added += 1
    return expanded if added > 0 else None


async def context_aware(
    query: str, classification: dict, entities: dict,
    *, llm=None, **_,
) -> str | None:
    """基于 LLM 的上下文扩展(收编 _expand_query)。

    早退条件:无 llm / 不支持的 query_type
    防御:输出超长返回 None(防 prompt 注入)
    """
    if not llm:
        return None
    query_type = classification.get("query_type", "search")
    if query_type not in _SUPPORTED_TYPES:
        return None

    prompt = f"""为以下查询生成扩展版本({query_type}),保留核心语义,增加相关术语和实体。

查询: {query}
实体: {entities}

只输出扩展后的查询,不要添加解释。"""

    try:
        resp = await llm.ainvoke(prompt)
        result = resp.content.strip()
        # 防御 prompt 注入:输出超长被丢弃。
        # 阈值基于 query 长度的 3x + 100 字符,但设下限 200 字符以保护短 query 场景。
        threshold = max(200, len(query) * 3 + 100)
        if len(result) > threshold:
            logger.warning(f"context_aware: output too long ({len(result)} > {threshold}), dropped")
            return None
        return result
    except Exception as e:
        logger.warning(
            "context_aware failed: %s",
            type(e).__name__,
            exc_info=True,  # 保留 traceback 在 stderr,不内联到消息字符串(PII 安全)
        )
        return None
