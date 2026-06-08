"""透明度标注生成。"""

from typing import TypedDict


class TransparencyAnnotation(TypedDict):
    level: str
    icon: str
    message: str
    details: str | None


def generate_transparency_annotations(audit_result, worker_failures: list[str], token_exhausted: bool = False) -> list[TransparencyAnnotation]:
    annotations: list[TransparencyAnnotation] = []
    for worker_type in worker_failures:
        annotations.append({"level": "warning", "icon": "⚠️", "message": f"仅基于{worker_type}结果", "details": f"{worker_type} Agent 未能返回结果"})
    if audit_result.unverified_claims:
        annotations.append({"level": "warning", "icon": "❌", "message": "引用未验证", "details": f"{len(audit_result.unverified_claims)} 条陈述缺少引用支撑"})
    for c in audit_result.contradictions:
        annotations.append({"level": "error", "icon": "⚡", "message": "跨源矛盾", "details": f"{c.get('claim_a', '?')} vs {c.get('claim_b', '?')}"})
    if audit_result.coverage_gaps:
        annotations.append({"level": "info", "icon": "❓", "message": "方面未回答", "details": "; ".join(audit_result.coverage_gaps)})
    if token_exhausted:
        annotations.append({"level": "warning", "icon": "📊", "message": "Token预算耗尽", "details": "生成因 Token 预算限制而截断"})
    return annotations
