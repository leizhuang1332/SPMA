"""FallbackManager 单测 + 1000 并发压测。"""
import asyncio
import pytest

from spma.agents.supervisor.fallback_manager import FallbackManager


@pytest.mark.asyncio
async def test_l1_success_returns_first():
    """L1 成功 → 返回 (result, 'multi_strategy')。"""
    async def l1_strategies_fn(*a, **kw):
        return [("a", "a-result"), ("b", "b-result")]
    async def l2_fn(*a, **kw):
        return "l2"
    def l3_fn(*a, **kw):
        return "l3"

    from spma.agents.supervisor.strategy_orchestrator import StrategyOrchestrator
    orch = StrategyOrchestrator(stage="t", names=["a", "b"])
    # monkey-patch execute_parallel
    orch.execute_parallel = l1_strategies_fn

    fm = FallbackManager(orch, l2_fn, l3_fn)
    result, level = await fm.execute_with_fallback("q", {"a": lambda: None, "b": lambda: None})
    assert result == "a-result"
    assert level == "multi_strategy"


@pytest.mark.asyncio
async def test_l1_empty_falls_to_l2():
    """L1 返回空 → 走 L2。"""
    from spma.agents.supervisor.strategy_orchestrator import StrategyOrchestrator
    orch = StrategyOrchestrator(stage="t", names=["a"])

    async def l1_empty(*a, **kw): return []
    orch.execute_parallel = l1_empty

    async def l2_fn(*a, **kw): return "l2-result"
    def l3_fn(*a, **kw): return "l3"

    fm = FallbackManager(orch, l2_fn, l3_fn)
    result, level = await fm.execute_with_fallback("q", {"a": lambda: None})
    assert result == "l2-result"
    assert level == "primary_backup"


@pytest.mark.asyncio
async def test_l2_fails_falls_to_l3():
    """L1 空 + L2 抛异常 → 走 L3。"""
    from spma.agents.supervisor.strategy_orchestrator import StrategyOrchestrator
    orch = StrategyOrchestrator(stage="t", names=["a"])

    async def l1_empty(*a, **kw): return []
    orch.execute_parallel = l1_empty

    async def l2_fn(*a, **kw): raise RuntimeError("l2 fail")
    def l3_fn(*a, **kw): return "l3-result"

    fm = FallbackManager(orch, l2_fn, l3_fn)
    result, level = await fm.execute_with_fallback("q", {"a": lambda: None})
    assert result == "l3-result"
    assert level == "rule_only"


@pytest.mark.asyncio
async def test_concurrent_state_isolation():
    """1000 并发请求,无状态串扰(主文件 ADR-002 关键测试)。"""
    from spma.agents.supervisor.strategy_orchestrator import StrategyOrchestrator

    # 共享编排器
    orch = StrategyOrchestrator(stage="t", names=["a"])

    # 行为:用户 ID 偶数 → L1 成功,奇数 → L1 失败走 L2
    async def l1_user_specific(*args, **kwargs):
        strategies = kwargs.get("strategies", {})
        user_id = kwargs.get("_user_id", 0)
        if user_id % 2 == 0:
            return [("a", f"l1-{user_id}")]
        return []  # 奇数 L1 失败

    async def l1_dispatcher(*a, **kw):
        return await l1_user_specific(*a, **kw)

    orch.execute_parallel = l1_dispatcher

    async def l2_fn(*a, **kw): return f"l2-{kw.get('_user_id')}"
    def l3_fn(*a, **kw): return f"l3-{kw.get('_user_id')}"

    fm = FallbackManager(orch, l2_fn, l3_fn)

    async def one_request(uid):
        # 每个请求独立 kwargs,模拟 per-request 状态
        return await fm.execute_with_fallback(
            f"q-{uid}", {"a": lambda: None}, _user_id=uid,
        )

    # 1000 并发
    results = await asyncio.gather(*[one_request(i) for i in range(1000)])

    # 验证:偶数用户走 L1,奇数用户走 L2(没有跨请求串扰)
    for uid, (result, level) in enumerate(results):
        if uid % 2 == 0:
            assert result == f"l1-{uid}", f"uid={uid} should be L1, got {result}"
            assert level == "multi_strategy"
        else:
            assert result == f"l2-{uid}", f"uid={uid} should be L2, got {result}"
            assert level == "primary_backup"


@pytest.mark.asyncio
async def test_real_orchestrator_integration():
    """FallbackManager + 真实 StrategyOrchestrator 集成:CircuitBreaker 短路 + 异常隔离。"""
    from spma.agents.supervisor.strategy_orchestrator import StrategyOrchestrator
    from spma.infrastructure.circuit_breaker import reset_all

    reset_all()
    try:
        orch = StrategyOrchestrator(stage="integration_test", names=["good", "bad", "silent"])

        async def good_fn(*a, **kw): return "good-result"
        async def bad_fn(*a, **kw): raise RuntimeError("simulated failure")
        async def silent_fn(*a, **kw): return None  # 策略早退

        async def l2_fn(*a, **kw): return "l2-backup"
        def l3_fn(*a, **kw): return "l3-fallback"

        fm = FallbackManager(orch, l2_fn, l3_fn)

        # 真实 orchestrator 路径:bad 抛异常被隔离,silent 返回 None 被过滤,
        # good 的结果返回 → L1 命中
        result, level = await fm.execute_with_fallback(
            "q",
            {"good": good_fn, "bad": bad_fn, "silent": silent_fn},
        )
        assert result == "good-result", f"expected good-result, got {result}"
        assert level == "multi_strategy"

        # 验证 _level_failures 没增加(L1 成功)
        assert fm._level_failures["multi_strategy"] == 0
    finally:
        reset_all()