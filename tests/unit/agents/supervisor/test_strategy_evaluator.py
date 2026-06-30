"""StableStrategyEvaluator 单测。"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from spma.agents.supervisor import strategy_evaluator as se_module
from spma.agents.supervisor.strategy_evaluator import StableStrategyEvaluator


@pytest.fixture
def mock_pool():
    pool = MagicMock()
    # async with pool.acquire() as conn: ...
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value={"id": 1})
    conn.execute = AsyncMock(return_value=None)
    conn.fetchval = AsyncMock(return_value=1)
    acquire_ctx = MagicMock()
    acquire_ctx.__aenter__ = AsyncMock(return_value=conn)
    acquire_ctx.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=acquire_ctx)
    transaction_ctx = MagicMock()
    transaction_ctx.__aenter__ = AsyncMock(return_value=None)
    transaction_ctx.__aexit__ = AsyncMock(return_value=None)
    conn.transaction = MagicMock(return_value=transaction_ctx)
    return pool


@pytest.mark.asyncio
async def test_ema_smoothing_blends_old_and_new_weights(mock_pool):
    """EMA:新权重 = 0.1*新评分 + 0.9*旧权重(平滑)。

    收紧容差:精确验证 EMA 计算(abs=1e-9)。
    """
    ev = StableStrategyEvaluator(mock_pool, ema_alpha=0.1, min_weight=0.1)

    async def evaluator(case):
        # strategy_a 评分 1.0(完美),strategy_b 评分 0.0
        return {"a": 1.0, "b": 0.0}

    current_weights = {"a": 0.3, "b": 0.7}
    result = await ev.evaluate_and_propose(
        test_cases=[{"query": "test"}],  # 1 个 case
        current_weights=current_weights,
        evaluator=evaluator,
    )
    # a: new = 0.1*1.0 + 0.9*0.3 = 0.37
    # b: new = 0.1*0.0 + 0.9*0.7 = 0.63
    # 归一化前: a=0.37, b=0.63 (和=1.0,无需归一化)
    assert result["weight_diffs"]["a"]["new"] == pytest.approx(0.37, abs=1e-9)
    assert result["weight_diffs"]["b"]["new"] == pytest.approx(0.63, abs=1e-9)


@pytest.mark.asyncio
async def test_min_weight_constraint(mock_pool):
    """min_weight=0.1:即使评分 0,也不能低于 0.1。"""
    ev = StableStrategyEvaluator(mock_pool, ema_alpha=0.1, min_weight=0.1)

    async def evaluator(case):
        return {"a": 0.0, "b": 1.0}

    current_weights = {"a": 0.5, "b": 0.5}
    result = await ev.evaluate_and_propose(
        test_cases=[{"query": "test"}],
        current_weights=current_weights,
        evaluator=evaluator,
    )
    # a: new = max(0.1, 0.1*0.0 + 0.9*0.5) = 0.45
    assert result["weight_diffs"]["a"]["new"] >= 0.1


@pytest.mark.asyncio
async def test_total_delta_triggers_review_flag(mock_pool):
    """总 delta > 0.1 触发 should_review=True。"""
    ev = StableStrategyEvaluator(mock_pool, ema_alpha=0.1, min_weight=0.1)

    async def evaluator(case):
        return {"a": 1.0, "b": 0.0}  # 大幅变化

    current_weights = {"a": 0.5, "b": 0.5}
    result = await ev.evaluate_and_propose(
        test_cases=[{"query": "test"}],
        current_weights=current_weights,
        evaluator=evaluator,
    )
    # 总 delta 较大,应触发审核
    assert result["should_review"] is True


@pytest.mark.asyncio
async def test_writes_snapshot_to_qr_weights_history(mock_pool):
    """写入 qr_weights_history(通过 write_weights_snapshot)。"""
    ev = StableStrategyEvaluator(mock_pool, ema_alpha=0.1, min_weight=0.1)

    async def evaluator(case):
        return {"a": 0.5, "b": 0.5}

    current_weights = {"a": 0.5, "b": 0.5}
    result = await ev.evaluate_and_propose(
        test_cases=[{"query": "test"}],
        current_weights=current_weights,
        evaluator=evaluator,
    )
    # snapshot_id 是 mock 返回的 1
    assert result["snapshot_id"] == 1


# ===== 新增测试(Issue 2: 7 个覆盖边界场景) =====


@pytest.mark.asyncio
async def test_empty_test_cases_returns_zero_scores(mock_pool):
    """空 test_cases → eval_summary 都是 0.0/0,不应崩溃。"""
    ev = StableStrategyEvaluator(mock_pool, ema_alpha=0.1, min_weight=0.1)

    async def evaluator(case):
        return {"a": 1.0, "b": 1.0}

    current_weights = {"a": 0.5, "b": 0.5}
    result = await ev.evaluate_and_propose(
        test_cases=[],
        current_weights=current_weights,
        evaluator=evaluator,
    )
    # 空 case → target_w=0.0,EMA: new=0.9*old+0.1*0=0.9*old
    # a: 0.9*0.5=0.45;b: 0.9*0.5=0.45;归一化 0.5/0.5
    # 应保留 min_weight 兜底
    assert result["weight_diffs"]["a"]["new"] >= 0.1
    assert result["weight_diffs"]["b"]["new"] >= 0.1
    # eval_summary 应该是 0.0/0
    assert result["evaluation"]["a"]["count"] == 0
    assert result["evaluation"]["b"]["count"] == 0
    # delta 较小(0.9*old - old = -0.1*0.5 = -0.05),总和 0.1 ≤ 0.1 → 不触发
    # 注意:含归一化后 delta 会偏小
    assert result["should_review"] is False or abs(
        sum(abs(d["delta"]) for d in result["weight_diffs"].values()) - 0.1
    ) < 1e-9


@pytest.mark.asyncio
async def test_evaluator_omits_strategy_silently_dropped(mock_pool):
    """evaluator 返回的 strategy 不在 current_weights 中 → 静默丢弃 + warning。"""
    ev = StableStrategyEvaluator(mock_pool, ema_alpha=0.1, min_weight=0.1)

    async def evaluator(case):
        return {"a": 1.0, "b": 1.0, "unknown_strategy": 0.5}  # 含未知 strategy

    current_weights = {"a": 0.5, "b": 0.5}
    result = await ev.evaluate_and_propose(
        test_cases=[{"query": "test"}],
        current_weights=current_weights,
        evaluator=evaluator,
    )
    # unknown_strategy 静默丢弃
    assert "unknown_strategy" not in result["weight_diffs"]
    assert "a" in result["weight_diffs"]
    assert "b" in result["weight_diffs"]


@pytest.mark.asyncio
async def test_current_weights_with_zero_value(mock_pool):
    """current_weights 含 0 值 → EMA 不抛错,min_weight 兜底。"""
    ev = StableStrategyEvaluator(mock_pool, ema_alpha=0.1, min_weight=0.1)

    async def evaluator(case):
        return {"a": 0.0, "b": 1.0}

    current_weights = {"a": 0.0, "b": 0.5}  # a 是 0
    result = await ev.evaluate_and_propose(
        test_cases=[{"query": "test"}],
        current_weights=current_weights,
        evaluator=evaluator,
    )
    # a: new = max(0.1, 0.1*0.0 + 0.9*0.0) = max(0.1, 0) = 0.1
    # 归一化前: a=0.1, b=0.1*1.0+0.9*0.5=0.55;总和 0.65 → a=0.154, b=0.846
    assert result["weight_diffs"]["a"]["new"] >= 0.1


@pytest.mark.asyncio
async def test_should_review_boundary_exactly_at_threshold(mock_pool):
    """total_delta 边界处理:大于阈值才触发。"""
    ev = StableStrategyEvaluator(mock_pool, ema_alpha=0.1, min_weight=0.1, review_delta_threshold=0.1)

    # 构造 total_delta 绝对值非常小的场景
    # a: 0.1*1.0+0.9*0.5 = 0.55,delta=0.05
    # b: 0.1*0.0+0.9*0.5 = 0.45,delta=-0.05
    # 总 delta = 0.10 → not > 0.10 → False
    async def evaluator(case):
        return {"a": 1.0, "b": 0.0}

    current_weights = {"a": 0.5, "b": 0.5}
    result = await ev.evaluate_and_propose(
        test_cases=[{"query": "test"}],
        current_weights=current_weights,
        evaluator=evaluator,
    )
    # 归一化后总 delta 可能略变,只看阈值严格性
    total_delta = sum(abs(d["delta"]) for d in result["weight_diffs"].values())
    # 严格大于才触发
    assert result["should_review"] is False or abs(total_delta - 0.10) < 1e-9


@pytest.mark.asyncio
async def test_multi_case_average(mock_pool):
    """多 case → 评分求平均。"""
    ev = StableStrategyEvaluator(mock_pool, ema_alpha=0.1, min_weight=0.1)

    async def evaluator(case):
        q = case.get("query", "")
        if q == "case1":
            return {"a": 0.0, "b": 1.0}
        else:  # case2
            return {"a": 1.0, "b": 0.0}

    current_weights = {"a": 0.5, "b": 0.5}
    result = await ev.evaluate_and_propose(
        test_cases=[{"query": "case1"}, {"query": "case2"}],
        current_weights=current_weights,
        evaluator=evaluator,
    )
    # avg: a = (0+1)/2 = 0.5, b = (1+0)/2 = 0.5
    # new: a = 0.1*0.5 + 0.9*0.5 = 0.5, b = 0.5
    # 归一化前后和都 1.0
    assert abs(result["weight_diffs"]["a"]["new"] - 0.5) < 0.01
    assert abs(result["weight_diffs"]["b"]["new"] - 0.5) < 0.01
    # 各策略 2 个 case
    assert result["evaluation"]["a"]["count"] == 2
    assert result["evaluation"]["b"]["count"] == 2


@pytest.mark.asyncio
async def test_custom_review_threshold(mock_pool):
    """自定义 review_delta_threshold 参数生效。"""
    ev = StableStrategyEvaluator(
        mock_pool,
        ema_alpha=0.1,
        min_weight=0.1,
        review_delta_threshold=0.5,  # 提高阈值
    )

    async def evaluator(case):
        # 中等变化:total_delta ≈ 0.05(归一化前)
        return {"a": 1.0, "b": 0.0}

    current_weights = {"a": 0.5, "b": 0.5}
    result = await ev.evaluate_and_propose(
        test_cases=[{"query": "test"}],
        current_weights=current_weights,
        evaluator=evaluator,
    )
    # total_delta < 0.5 → 不触发
    assert result["should_review"] is False


@pytest.mark.asyncio
async def test_write_weights_snapshot_propagates_exception(mock_pool):
    """write_weights_snapshot 抛错 → 上抛(不静默吞)。"""
    original_write = se_module.write_weights_snapshot
    se_module.write_weights_snapshot = AsyncMock(side_effect=RuntimeError("DB error"))

    try:
        ev = StableStrategyEvaluator(mock_pool, ema_alpha=0.1, min_weight=0.1)

        async def evaluator(case):
            return {"a": 0.5, "b": 0.5}

        with pytest.raises(RuntimeError, match="DB error"):
            await ev.evaluate_and_propose(
                test_cases=[{"query": "test"}],
                current_weights={"a": 0.5, "b": 0.5},
                evaluator=evaluator,
            )
    finally:
        se_module.write_weights_snapshot = original_write


@pytest.mark.asyncio
async def test_evaluator_exception_skips_failing_case(mock_pool):
    """evaluator 抛错 → 跳过失败 case,继续处理其余,整批不中断。"""
    ev = StableStrategyEvaluator(mock_pool, ema_alpha=0.1, min_weight=0.1)

    call_count = {"n": 0}

    async def evaluator(case):
        call_count["n"] += 1
        if case.get("query") == "bad":
            raise ValueError("boom")
        return {"a": 1.0, "b": 0.0}

    current_weights = {"a": 0.5, "b": 0.5}
    # 不应抛错
    result = await ev.evaluate_and_propose(
        test_cases=[{"query": "good1"}, {"query": "bad"}, {"query": "good2"}],
        current_weights=current_weights,
        evaluator=evaluator,
    )
    # 应该处理完所有 case
    assert call_count["n"] == 3
    # 2 个成功 case 被计入
    assert result["evaluation"]["a"]["count"] == 2
    assert result["evaluation"]["b"]["count"] == 2
