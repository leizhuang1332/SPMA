"""Code Agent 完备度判断——3 级递进。"""

import json
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class CodeCompletenessResult:
    verdict: str          # "converge" | "expand"
    level: str            # "L1" | "L2" | "L3"
    reason: str


async def assess_code_completeness(
    ripgrep_results: list[dict],
    expanded_context: list[dict],
    entities: dict,
    call_depth: int,
    new_files_this_round: int,
    fallback_layer: int,
    llm=None,
    min_results: int = 3,
) -> CodeCompletenessResult:
    total_results = len(ripgrep_results) + len(expanded_context)
    code_refs = entities.get("code_refs", []) or []

    if total_results >= min_results and code_refs and fallback_layer == 0:
        logger.info(f"L1 收敛: {total_results} results + exact code_refs match")
        return CodeCompletenessResult(verdict="converge", level="L1", reason="deterministic_code_refs")

    if total_results >= min_results and (call_depth >= 2 or new_files_this_round == 0):
        reason = "max_call_depth" if call_depth >= 2 else "no_new_files"
        logger.info(f"L2 收敛: {total_results} results, {reason}")
        return CodeCompletenessResult(verdict="converge", level="L2", reason=reason)

    if llm is not None:
        verdict, reason = await _llm_code_completeness_check(ripgrep_results, expanded_context, entities, llm)
        return CodeCompletenessResult(verdict=verdict, level="L3", reason=reason)

    logger.warning("无 LLM 可用，默认扩展")
    return CodeCompletenessResult(verdict="expand", level="L3", reason="no_llm_default_expand")


async def _llm_code_completeness_check(ripgrep_results, expanded_context, entities, llm) -> tuple[str, str]:
    snippets = []
    for r in ripgrep_results[:10]:
        snippets.append(f"- [{r.get('file_path', '?')}:{r.get('line_number', '?')}]: {r.get('match_text', '')[:150]}")
    for f in expanded_context[:5]:
        snippets.append(f"- [EXPANDED] {f.get('file_path', '?')}: calls={f.get('calls', [])[:3]}")

    snippets_text = "\n".join(snippets) if snippets else "无结果"
    prompt = f"""根据以下代码搜索结果，判断信息是否足以定位到用户想要的代码实现。

用户关注的实体: {json.dumps({k: v for k, v in entities.items() if v}, ensure_ascii=False)}
代码搜索结果摘要:
{snippets_text}
只输出 JSON: {{"assessment": "sufficient" 或 "insufficient", "reason": "判断理由"}}"""

    try:
        resp_obj = await llm.ainvoke(prompt)
        resp = resp_obj.content
        data = json.loads(resp)
        if data.get("assessment") == "sufficient":
            return "converge", "llm_judged_sufficient"
        return "expand", "llm_judged_insufficient"
    except Exception as e:
        logger.warning(f"LLM 完备度判断失败: {e}，默认扩展")
        return "expand", "llm_error_default_expand"
