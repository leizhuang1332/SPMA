"""Code Agent 完备度判断——v2: 7 种收敛模式（design-13 §3.4）。

7 mode = 5 确定性 + 2 LLM 路径：
    1. goal_verified: code_refs 非空 + total ≥ 3 + fallback_layer = 0
    2. stuck: round ≥ 2 + new_files_this_round=0 + previous_new_files=0
    3. regression: round_over_round_ratio < 0.5 + 本轮 total 减少
    4. diminishing_returns: new_files_rate < 0.10
    5. cap_reached: call_depth ≥ max_rounds 或 total_files ≥ max_files
    6. llm_judged: 5 确定性全不命中 + LLM sufficient
    7. expand: 5 确定性全不命中 + LLM insufficient（LLM 失败兜底）

向后兼容：`legacy_levels=True` 时返回 L1/L2/L3（旧测试）；新调用方传 `legacy_levels=False`。
"""
import json
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class CodeCompletenessResult:
    verdict: str          # "converge" | "expand"
    level: str            # 7 种之一 或 legacy "L1"/"L2"/"L3"
    reason: str
    should_reflect: bool = False  # Task 1：diminishing_returns 时由 assess 设为 True，触发反思层


# v2 模式 → 旧 L 级别映射（向后兼容用）
_V2_TO_LEGACY = {
    "goal_verified": "L1",
    "stuck": "L2",
    "regression": "L2",
    "diminishing_returns": "L2",
    "cap_reached": "L2",
    "llm_judged": "L3",
    "expand": "L3",
}


async def assess_code_completeness(
    ripgrep_results: list[dict],
    expanded_context: list[dict],
    entities: dict,
    call_depth: int,
    new_files_this_round: int,
    fallback_layer: int,
    llm=None,
    min_results: int = 3,
    *,
    previous_new_files: int = 0,    # 新增：stuck 判定
    max_files: int = 50,             # 新增：cap_reached 判定
    max_rounds: int = 6,             # 新增：cap_reached 判定
    round: int = 0,                  # 新增：stuck 守卫（round ≥ 2）
    total_files: int = 0,            # 新增：diminishing_returns 判定
    legacy_levels: bool = True,      # 向后兼容：True 时返回 L1/L2/L3
) -> CodeCompletenessResult:
    """v2: 7 种收敛模式判定（默认返回 legacy 级别以保持向后兼容）。"""
    total_results = len(ripgrep_results) + len(expanded_context)
    code_refs = entities.get("code_refs", []) or []

    # 确定性 1: goal_verified
    if total_results >= min_results and code_refs and fallback_layer == 0:
        return _make_result("converge", "goal_verified", "deterministic_code_refs", legacy_levels)

    # 确定性 5: cap_reached（硬约束优先于 stuck——max_rounds 是不可逾越的截断）
    if call_depth >= max_rounds or total_files >= max_files:
        reason = "max_rounds" if call_depth >= max_rounds else "max_files"
        return _make_result("converge", "cap_reached", reason, legacy_levels)

    # 确定性 2: stuck（首轮豁免）
    if round >= 2 and new_files_this_round == 0 and previous_new_files == 0:
        return _make_result("converge", "stuck", "no_new_files_two_rounds", legacy_levels)

    # 确定性 3: regression（ratio < 0.5 且本轮 total 减少）
    if previous_new_files > 0:
        ratio = new_files_this_round / previous_new_files
        if ratio < 0.5 and new_files_this_round < previous_new_files:
            return _make_result("converge", "regression", f"ratio={ratio:.2f}", legacy_levels)

    # 确定性 4: diminishing_returns（new_files_rate < 0.10）
    if total_files > 0:
        new_files_rate = new_files_this_round / total_files
        if new_files_rate < 0.10 and new_files_this_round < 3:
            result = _make_result(
                "converge", "diminishing_returns", f"rate={new_files_rate:.2f}", legacy_levels,
            )
            # Task 1: 5 mode 未收敛但仍低效 → 触发反思层调整搜索策略
            result.should_reflect = True
            return result

    # 向后兼容：legacy L2 条件（call_depth >= 2 或 no_new_files with sufficient results）
    # 仅在 legacy_levels=True 时生效，确保旧测试通过
    if legacy_levels and total_results >= min_results and (call_depth >= 2 or new_files_this_round == 0):
        reason = "max_call_depth" if call_depth >= 2 else "no_new_files"
        return CodeCompletenessResult(verdict="converge", level="L2", reason=reason)

    # LLM 路径
    if llm is not None:
        verdict, reason = await _llm_code_completeness_check(
            ripgrep_results, expanded_context, entities, llm,
        )
        level = "llm_judged" if verdict == "converge" else "expand"
        return _make_result(verdict, level, reason, legacy_levels)

    # LLM 不可用 → 兜底 expand
    return _make_result("expand", "expand", "no_llm_default_expand", legacy_levels)


def _make_result(verdict: str, v2_level: str, reason: str, legacy_levels: bool) -> CodeCompletenessResult:
    """根据 legacy_levels 开关决定返回的 level 字符串。"""
    if legacy_levels:
        level = _V2_TO_LEGACY.get(v2_level, v2_level)
    else:
        level = v2_level
    if level != v2_level and v2_level != "expand":
        logger.debug(f"v2 level '{v2_level}' → legacy '{level}'")
    return CodeCompletenessResult(verdict=verdict, level=level, reason=reason)


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
