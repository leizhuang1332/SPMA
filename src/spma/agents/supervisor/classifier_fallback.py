"""Supervisor 分类降级路径——Haiku->Qwen3-8B->纯规则。"""

import logging
from spma.models.classification import ClassificationResult

logger = logging.getLogger(__name__)


async def classify_with_fallback(
    query: str,
    primary_llm,
    fallback_llm=None,
    conversation_history: str = "",
    known_tables: list[str] | None = None,
) -> ClassificationResult:
    from spma.agents.supervisor.classifier import classify_and_extract
    from spma.agents.supervisor.classifier_rules import apply_rules

    # Try primary (Haiku)
    if primary_llm is not None:
        try:
            result = await classify_and_extract(query, primary_llm, conversation_history, known_tables)
            result = apply_rules(query, result)
            logger.info(f"Primary LLM 分类: sources={result['sources']}")
            return result
        except Exception as e:
            logger.warning(f"Primary LLM 失败: {e}")

    # Try fallback (Qwen3-8B)
    if fallback_llm is not None:
        try:
            result = await classify_and_extract(query, fallback_llm, conversation_history, known_tables)
            result = apply_rules(query, result)
            logger.info(f"Fallback LLM 分类: sources={result['sources']}")
            return result
        except Exception as e:
            logger.warning(f"Fallback LLM 失败: {e}")

    # Pure rules
    logger.warning("全部 LLM 不可用，纯规则分类")
    return apply_rules(query, ClassificationResult(
        sources=["doc", "code", "sql"], is_cross_source=True, query_type="search", entities={}))
