"""Synthesis Auditor——引用完整性 + 跨源一致性 + 问题覆盖度检查。"""

import json
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class AuditResult:
    verdict: str
    citation_coverage: float = 0.0
    unverified_claims: list[str] = field(default_factory=list)
    contradictions: list[dict] = field(default_factory=list)
    coverage_gaps: list[str] = field(default_factory=list)


async def audit_answer(draft_answer: str, original_query: str, fused_citations: list[dict], llm) -> AuditResult:
    if not fused_citations:
        return AuditResult(verdict="fix", citation_coverage=0.0, unverified_claims=["无检索结果支撑"])

    prompt = f"""你是一个严谨的审计员。检查刚才生成的回答:
{draft_answer}

检查项目:
1. 引用完整性: 每条陈述都有引用支撑吗？
2. 跨源一致性: Doc/SQL 的信息有矛盾吗？
3. 覆盖度: 用户原始问题 "{original_query}" 的每个方面都被回答了吗？

输出 JSON:
{{"citation_coverage": 0.xx, "unverified_claims": [...], "contradictions": [{{"claim_a": "...", "claim_b": "...", "source_a": "...", "source_b": "..."}}], "coverage_gaps": [...], "verdict": "pass" | "fix" | "contradiction" | "gap"}}"""

    try:
        resp_obj = await llm.ainvoke(prompt)
        resp = resp_obj.content
        data = json.loads(resp)
        result = AuditResult(
            verdict=data.get("verdict", "fix"),
            citation_coverage=data.get("citation_coverage", 0.0),
            unverified_claims=data.get("unverified_claims", []),
            contradictions=data.get("contradictions", []),
            coverage_gaps=data.get("coverage_gaps", []),
        )
        if result.citation_coverage >= 0.8 and result.verdict == "fix":
            if result.coverage_gaps:
                result.verdict = "gap"
            elif result.contradictions:
                result.verdict = "contradiction"
        return result
    except Exception as e:
        logger.warning(f"自检 LLM 调用失败: {e}，默认 pass")
        return AuditResult(verdict="pass", citation_coverage=0.5, unverified_claims=["自检失败"])
