"""quality_evaluator 单测(主文件 ADR-004:零 LLM)。"""
import pytest

from spma.agents.supervisor.quality_evaluator import evaluate_quality


def test_high_score_for_similar_text():
    """语义相似的文本评分高。"""
    emb = [0.5, 0.5, 0.5]
    score = evaluate_quality(emb, emb, "test", {})
    # 收紧:完全相同 embedding + 短文本 + 无 entities → 全 1.0
    assert score == pytest.approx(1.0, abs=0.01)


def test_low_score_for_dissimilar_text():
    """语义完全相反的文本评分低。"""
    emb_a = [1.0, 0.0, 0.0]
    emb_b = [0.0, 1.0, 0.0]
    score = evaluate_quality(emb_a, emb_b, "test", {})
    # cosine = 0,但 entity + length 各 1.0 → 0.6*0 + 0.3*1 + 0.1*1 = 0.4
    assert score == pytest.approx(0.4, abs=0.01)


def test_entity_coverage_penalizes_missing_entities():
    """实体覆盖率高 → entity_score 高。"""
    emb = [0.5, 0.5, 0.5]
    score_full = evaluate_quality(emb, emb, "t_user user_id REQ-123", {
        "table_names": ["t_user"], "column_names": ["user_id"], "req_ids": ["REQ-123"],
    })
    score_partial = evaluate_quality(emb, emb, "REQ-123", {
        "table_names": ["t_user"], "column_names": ["user_id"], "req_ids": ["REQ-123"],
    })
    assert score_full > score_partial


def test_no_entities_gives_perfect_entity_score():
    """无 entities 时 entity_score = 1.0(不扣分)。"""
    emb = [0.5, 0.5, 0.5]
    score = evaluate_quality(emb, emb, "anything", {})
    assert score == pytest.approx(1.0, abs=0.01)


def test_length_penalty_for_very_long_output():
    """超长输出被扣分。"""
    emb = [0.5, 0.5, 0.5]
    short_score = evaluate_quality(emb, emb, "x" * 10, {})
    long_score = evaluate_quality(emb, emb, "x" * 10000, {})
    assert short_score > long_score


# ===== Fix 4 新增测试 =====


def test_dimension_mismatch_raises_value_error():
    """original_emb 与 rewritten_emb 维度不一致 → 抛 ValueError。"""
    with pytest.raises(ValueError, match="vector dim mismatch"):
        evaluate_quality([0.1, 0.2], [0.1, 0.2, 0.3], "text", {})


def test_none_rewritten_falls_back_to_empty_string():
    """rewritten=None → 不抛错,score 在 [0, 1.0] 区间。"""
    emb = [0.5, 0.5, 0.5]
    score = evaluate_quality(emb, emb, None, {})  # type: ignore[arg-type]
    # None → rewritten="";length_score 走 rewritten_len<1 分支 → 0.0
    # entity_score = 1.0(空 entities);semantic = 1.0(同 embedding)
    # 总分 = 0.6*1.0 + 0.3*1.0 + 0.1*0.0 = 0.9
    assert 0.0 <= score <= 1.0


def test_non_list_entity_value_skipped():
    """entities 中某 key 的 value 是非 list → 跳过该 key,不抛错。"""
    emb = [0.5, 0.5, 0.5]
    # 故意把 table_names 设为字符串(应跳过)
    score = evaluate_quality(emb, emb, "user_id", {
        "table_names": "t_user",  # 应跳过
        "column_names": ["user_id"],  # 应保留
    })
    # 只有 column_names ["user_id"] 被计入,entity coverage 100%
    # semantic=1.0, entity=1.0, length=1.0 → 1.0
    assert score == pytest.approx(1.0, abs=0.01)


def test_non_string_rewritten_falls_back_to_empty():
    """rewritten 非字符串(数字/列表等)→ 不抛错,降级处理。"""
    emb = [0.5, 0.5, 0.5]
    score = evaluate_quality(emb, emb, 123, {})  # type: ignore[arg-type]
    # rewrite 视为空字符串,length_score 边界返回 0
    assert 0.0 <= score <= 1.0
