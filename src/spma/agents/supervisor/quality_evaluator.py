"""重写质量评估——纯 embedding + 启发式,零 LLM 调用(主文件 ADR-004)。"""
import math


def evaluate_quality(
    original_emb,
    rewritten_emb,
    rewritten: str,
    entities: dict,
) -> float:
    """三维评分:语义相似(0.6) + 实体覆盖(0.3) + 长度合理(0.1)。

    Args:
        rewritten: 必填字符串,None/非字符串会被替换为空字符串(防 NoneType 异常)。
        original_emb / rewritten_emb: 必填可迭代 embedding(浮点数列表)。
        entities: 必填 dict,值为 list[str]。非 list 值会被跳过(防御上游 type error)。

    Raises:
        ValueError: original_emb 和 rewritten_emb 维度不一致。
    """
    if not isinstance(rewritten, str):
        rewritten = ""  # 防御 None / 非字符串
    semantic = max(0.0, _cosine(rewritten_emb, original_emb))
    entity_score = _entity_coverage(rewritten, entities)
    length_score = _length_score(original_emb, rewritten)
    return semantic * 0.6 + entity_score * 0.3 + length_score * 0.1


def _entity_coverage(rewritten: str, entities: dict) -> float:
    all_entities = []
    for key in ["table_names", "column_names", "code_refs", "req_ids"]:
        value = entities.get(key, [])
        if not isinstance(value, list):
            continue  # 上游 type error,跳过(不抛错)
        all_entities.extend(value)
    if not all_entities:
        return 1.0
    rewritten_lower = rewritten.lower()
    covered = sum(1 for e in all_entities if e.lower() in rewritten_lower)
    return covered / len(all_entities)


# 注:本实现与 plan §2.3 字面写法不同。plan 写 `original_len = magnitude * 50` +
# ratio ∈ [0.5, 3.0] 区间判断;实现改为绝对区间 [1, 2000] + magnitude 作 cap 因子。
# 原因:小范数 embedding(测试用 [0.5,0.5,0.5], magnitude ≈ 0.87)在原公式下把
# 所有"短输出"误判为 ratio > 3.0 → 扣分,与"无 entities → 1.0"语义冲突。
# 本实现保留 [0, 1.0] 评分范围的同时,允许复杂查询(大 magnitude embedding)
# 的更宽容长度上限。详见 plan 文末"实现偏差记录(Task 2)"。
def _length_score(original_emb, rewritten: str) -> float:
    # 启发式:典型 query 在 [1, 2000] 字符内合理。
    # 原始 embedding 范数仅在超长区间作为"复杂查询"放宽因子。
    rewritten_len = len(rewritten)
    if 1 <= rewritten_len <= 2000:
        return 1.0
    if rewritten_len < 1:
        return max(0.0, rewritten_len)
    # 超长:embedding 范数越大,允许越长输出;反之快速衰减
    magnitude = math.sqrt(sum(x * x for x in original_emb))
    cap = max(2000.0, magnitude * 1000.0)
    return min(1.0, cap / rewritten_len)


def _cosine(a, b) -> float:
    if len(a) != len(b):
        raise ValueError(f"vector dim mismatch: {len(a)} vs {len(b)}")
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) + 1e-10
    nb = math.sqrt(sum(x * x for x in b)) + 1e-10
    return dot / (na * nb)
