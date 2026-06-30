"""查询分解多路策略。

- template_based:零 LLM,识别 '涉及哪些 X 和 Y' 模式
- entity_guided:零 LLM,按 entity 类型分发到 source
- llm_based:LLM 智能分解(收编 _decompose_query,保留 4 步 JSON 兜底)
"""
import json
import re
import logging

logger = logging.getLogger(__name__)


async def template_based(query: str, entities: dict, sources: list[str], **_) -> list[dict] | None:
    """规则模板分解:识别显式多意图模式。"""
    entities = entities or {}  # 防御 None(与 llm_based 防御模式一致)
    if not sources:
        return None
    if "和" in query and ("涉及哪些" in query or "以及" in query):
        parts = query.split("和")
        if len(parts) == 2 and parts[0].strip() and parts[1].strip():
            return [
                {"query": query.replace("和", f",面向{source}的"), "target": source}
                for source in sources
            ]

    entity_types_found = sum(
        1 for k in ["table_names", "code_refs", "req_ids"]
        if entities.get(k)
    )
    if entity_types_found >= 2:
        return [{"query": query, "target": s} for s in sources]

    return None


async def entity_guided(query: str, entities: dict, sources: list[str], **_) -> list[dict] | None:
    """实体导向:按 entity 类型 → source 映射,差异化生成。"""
    entities = entities or {}  # 防御 None
    if not sources:
        return None
    entity_source_map = {
        "table_names": ["database"],
        "column_names": ["database"],
        "code_refs": ["codebase"],
        "req_ids": ["requirements"],
    }

    per_source_entities: dict[str, list[str]] = {}
    for source in sources:
        ents = []
        for entity_key, source_list in entity_source_map.items():
            if source in source_list and entities.get(entity_key):
                ents.extend(entities[entity_key])
        per_source_entities[source] = ents

    # 全部 source 实体都不足 → 早退,避免 N 个相同子查询(主文件 §3.4 ADR)
    # 判定:非空实体 source 数量 <= 1 时无法差异化
    non_empty_sources = [s for s in sources if per_source_entities.get(s)]
    if len(non_empty_sources) <= 1:
        return None

    result = []
    for source in sources:
        ents = per_source_entities[source]
        if ents:
            result.append({"query": f"{query} {' '.join(ents)}", "target": source})
        else:
            result.append({"query": query, "target": source})
    return result


async def llm_based(
    query: str, entities: dict, sources: list[str],
    *, llm=None, **_,
) -> list[dict] | None:
    """LLM 智能分解(收编 _decompose_query,保留 4 步 JSON 解析兜底)。

    阈值语义:len(content) > 5000 严格大于,5000 字符也会被兜底为 broadcast。
    """
    if not sources:
        return None
    if not llm:
        return [{"query": query, "target": s} for s in sources]

    entities_str = str({k: v for k, v in (entities or {}).items() if v})
    prompt = f"""将以下复杂查询分解为 {len(sources)} 个独立的子查询,每个子查询面向单一数据源。

已抽取实体: {entities_str}
可用数据源: {', '.join(sources)}
用户查询: {query}

输出格式要求:
- 必须输出合法的 JSON 数组
- 每个元素包含 "query" 和 "target" 两个字段
- "target" 必须是 {', '.join(sources)} 中的一个
- 子查询应覆盖原始查询的所有核心意图

输出示例:
[{{"query": "子查询1", "target": "doc"}}, {{"query": "子查询2", "target": "code"}}]"""

    try:
        resp = await llm.ainvoke(prompt)
        content = resp.content
        if content is None or not content:
            logger.warning("llm_based: empty content from LLM, fallback to broadcast")
            return [{"query": query, "target": s} for s in sources]
        if len(content) > 5000:
            logger.warning("llm_based: output too long (%d), fallback to broadcast", len(content))
            return [{"query": query, "target": s} for s in sources]  # PII 安全格式:占位符 %d
        # 策略 1: 直接 JSON
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass
        # 策略 2: 正则提取
        m = re.search(r'\[.*\]', content, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass
        # 策略 3: 键值对提取
        target_patterns = {
            source: re.search(rf'{source}[\s:]+["\']([^"\']+)["\']', content)
            for source in sources
        }
        result = []
        for source, pattern in target_patterns.items():
            if pattern:
                result.append({"query": pattern.group(1), "target": source})
        if result:
            return result
        # 策略 4: 兜底
        return [{"query": query, "target": source} for source in sources]
    except Exception as e:
        # PII 安全: %s + type(e).__name__ + exc_info=True
        logger.warning(
            "llm_based failed: %s",
            type(e).__name__,
            exc_info=True,
        )
        return [{"query": query, "target": s} for s in sources]
