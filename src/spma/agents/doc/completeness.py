"""Doc Agent 完备度判断——3 级递进。

L1: 确定性收敛——结果≥5条 AND req_ids命中 → 自动收敛（不调LLM）
L2: 向量阈值——结果≥5条 AND Top-3相似度>0.85 → 自动收敛
L3: LLM兜底——Haiku判断是否充足
"""

import json
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class CompletenessResult:
    verdict: str          # "converge" | "expand"
    level: str            # "L1" | "L2" | "L3"
    reason: str


async def assess_completeness(
    results: list[dict],
    entities: dict[str, Any],
    llm,
    min_results: int = 5,
    vector_threshold: float = 0.85,
) -> CompletenessResult:
    """3 级完备度判断。"""
    req_ids = entities.get("req_ids", [])

    # L1: 确定性收敛
    if len(results) >= min_results and req_ids:
        return CompletenessResult(verdict="converge", level="L1", reason="deterministic_req_ids")

    # L2: 向量阈值
    if len(results) >= min_results:
        top3_scores = [r.get("score", 0) for r in results[:3]]
        avg_top3 = sum(top3_scores) / len(top3_scores) if top3_scores else 0
        if avg_top3 > vector_threshold:
            return CompletenessResult(verdict="converge", level="L2", reason="vector_threshold")

    # L3: LLM 兜底
    verdict, reason = await _llm_completeness_check(results, entities, llm)
    return CompletenessResult(verdict=verdict, level="L3", reason=reason)


async def _llm_completeness_check(results, entities, llm) -> tuple[str, str]:
    snippets = "\n".join(
        f"- [{r.get('chunk_id', '?')}]: {r.get('content', r.get('snippet', ''))[:200]}"
        for r in results[:10]
    )
    prompt = f"""根据以下检索结果，判断信息是否充足，是否足以回答用户问题。

检索结果摘要:
{snippets}

用户可能关注的实体: {json.dumps(entities, ensure_ascii=False)}

只输出 JSON: {{"assessment": "sufficient" 或 "insufficient", "reason": "判断理由"}}"""

    try:
        resp = await llm.generate(prompt)
        data = json.loads(resp)
        assessment = data.get("assessment", "insufficient")
        if assessment == "sufficient":
            return "converge", "llm_judged_sufficient"
        else:
            return "expand", "llm_judged_insufficient"
    except Exception as e:
        logger.warning(f"LLM 完备度判断失败: {e}，默认进入扩展")
        return "expand", "llm_error_default_expand"
