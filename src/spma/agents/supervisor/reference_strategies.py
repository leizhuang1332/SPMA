"""指代消解的多路策略。

- rule_based:零 LLM,基于 entity 类型+代词模式
- entity_based:零 LLM,按出现顺序一一替换代词
- llm_semantic:LLM 语义分析(收编已有 _resolve_references)
"""
import logging
import re

logger = logging.getLogger(__name__)


# 代词模式:
# - 双字(高置信度): 这个, 那个, 上次, 之前, 刚才, 上述, 此 → 直接子串匹配
# - 强代词(几乎无歧义): 它, 该 → 直接子串匹配(中文里基本只作代词)
# - 易混淆代词(避免误判 '这样'/'那样'/'这种'/'其余'): 这, 那, 其 → 零宽断言要求独立词
_REFERENCE_PATTERN = re.compile(
    r"这个|那个|上次|之前|刚才|上述|此"
    r"|它|该"
    r"|(?<![一-鿿a-zA-Z0-9_])这(?![一-鿿a-zA-Z0-9_])"
    r"|(?<![一-鿿a-zA-Z0-9_])那(?![一-鿿a-zA-Z0-9_])"
    r"|(?<![一-鿿a-zA-Z0-9_])其(?![一-鿿a-zA-Z0-9_])"
)


def _has_reference(query: str) -> bool:
    """检查 query 是否包含代词性表达。

    使用零宽断言精确匹配易混淆代词('这'/'那'/'其'),避免误判 '这样'/'那样'/'其余' 等常见无回指表达;
    强代词 '它'/'该' 直接匹配。
    """
    return bool(_REFERENCE_PATTERN.search(query))


async def rule_based(query: str, history: str, entities: dict, **_) -> str | None:
    """规则策略:用已知 entity 替换代词。

    模式:这个需求 / 那个需求 / 这个表 / 那个表 ...
    """
    if not _has_reference(query):
        return None

    resolved = query
    replacements = 0
    entity_types = {
        "需求": entities.get("req_ids", []),
        "表": entities.get("table_names", []),
        "字段": entities.get("column_names", []),
        "模块": entities.get("code_refs", []),
    }
    for pattern, entity_list in entity_types.items():
        if not entity_list:
            continue
        if f"这个{pattern}" in resolved:
            resolved = resolved.replace(f"这个{pattern}", entity_list[0], 1)
            replacements += 1
        if f"那个{pattern}" in resolved:
            resolved = resolved.replace(f"那个{pattern}", entity_list[-1], 1)
            replacements += 1

    return resolved if replacements > 0 else None


async def entity_based(query: str, history: str, entities: dict, **_) -> str | None:
    """实体策略:对所有 entity 按出现顺序配对代词,一对一替换。"""
    if not _has_reference(query):
        return None

    all_entities = []
    for key in ["req_ids", "table_names", "column_names", "code_refs"]:
        all_entities.extend(entities.get(key, []))

    if not all_entities:
        return None

    resolved = query
    for i, pronoun in enumerate(["它", "该", "这", "那", "其"]):
        if pronoun in resolved and i < len(all_entities):
            resolved = resolved.replace(pronoun, all_entities[i], 1)

    return resolved if resolved != query else None


async def llm_semantic(query: str, history: str, llm, **_) -> str | None:
    """LLM 语义策略(收编 _resolve_references):通过 prompt 让 LLM 替换代词。

    早退条件:无 history / 无 llm / 无代词
    防御:输出超长返回 None(防 prompt 注入)
    """
    if not history or not llm:
        return None
    if not _has_reference(query):
        return None

    prompt = f"""你是一个上下文理解助手。请根据对话历史,将以下查询中的指代性表达式还原为具体内容。

对话历史:
{history}

当前查询:
{query}

要求:
1. 将"这个问题"、"那个需求"等指代性表达式替换为具体内容
2. 保持查询的核心语义不变
3. 输出还原后的完整查询,不要添加额外解释"""

    try:
        resp = await llm.ainvoke(prompt)
        result = resp.content.strip()
        # 防御 prompt 注入:输出超长被丢弃。
        # 阈值基于 query 长度的 3x + 100 字符,但设下限 200 字符以保护短 query 场景。
        threshold = max(200, len(query) * 3 + 100)
        if len(result) > threshold:
            logger.warning(f"llm_semantic: output too long ({len(result)} > {threshold}), dropped")
            return None
        return result
    except Exception as e:
        logger.warning(f"llm_semantic failed: {type(e).__name__}: {e}")
        return None
