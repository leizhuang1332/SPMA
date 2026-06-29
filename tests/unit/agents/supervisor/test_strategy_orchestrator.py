"""StrategyOrchestrator 单测。"""
import asyncio
import pytest

from spma.agents.supervisor.strategy_orchestrator import StrategyOrchestrator
from spma.infrastructure.circuit_breaker import reset_all


@pytest.fixture(autouse=True)
def clear_cbs():
    reset_all()
    yield
    reset_all()


@pytest.mark.asyncio
async def test_execute_parallel_runs_all_strategies():
    """3 个策略全部成功 → 返回 3 个结果。"""
    orch = StrategyOrchestrator(stage="test", names=["a", "b", "c"])

    async def fn_a(x): return f"a:{x}"
    async def fn_b(x): return f"b:{x}"
    async def fn_c(x): return f"c:{x}"

    results = await orch.execute_parallel(
        {"a": fn_a, "b": fn_b, "c": fn_c},
        "input",
    )
    assert len(results) == 3
    result_dict = dict(results)
    assert result_dict["a"] == "a:input"
    assert result_dict["b"] == "b:input"
    assert result_dict["c"] == "c:input"


@pytest.mark.asyncio
async def test_execute_parallel_isolates_exceptions():
    """1 个策略抛异常 → 其他策略结果仍返回。"""
    orch = StrategyOrchestrator(stage="test", names=["a", "b", "c"])

    async def fn_a(x): return "a-ok"
    async def fn_b(x): raise RuntimeError("b failed")
    async def fn_c(x): return "c-ok"

    results = await orch.execute_parallel(
        {"a": fn_a, "b": fn_b, "c": fn_c},
        "input",
    )
    result_dict = dict(results)
    assert result_dict["a"] == "a-ok"
    assert result_dict["c"] == "c-ok"
    assert "b" not in result_dict  # b 被隔离


@pytest.mark.asyncio
async def test_execute_parallel_filters_none():
    """返回 None 的策略不出现在结果中。"""
    orch = StrategyOrchestrator(stage="test", names=["a", "b"])

    async def fn_a(x): return None
    async def fn_b(x): return "b-result"

    results = await orch.execute_parallel({"a": fn_a, "b": fn_b}, "input")
    assert dict(results) == {"b": "b-result"}


@pytest.mark.asyncio
async def test_execute_parallel_actually_concurrent():
    """3 个 sleep 0.1s 策略 → 总耗时 < 0.2s。"""
    orch = StrategyOrchestrator(stage="test", names=["a", "b", "c"])

    async def slow_fn(x):
        await asyncio.sleep(0.1)
        return x

    start = asyncio.get_event_loop().time()
    await orch.execute_parallel({"a": slow_fn, "b": slow_fn, "c": slow_fn}, "x")
    elapsed = asyncio.get_event_loop().time() - start
    assert elapsed < 0.2, f"expected parallel (<0.2s), got {elapsed:.3f}s"


@pytest.mark.asyncio
async def test_circuit_breaker_open_skips_strategy():
    """熔断器打开的策略直接跳过(不参与 gather)。"""
    from spma.infrastructure.circuit_breaker import get_circuit_breaker, CircuitState
    orch = StrategyOrchestrator(stage="test", names=["a", "b"])
    # 强制打开 a 的 CB
    cb_a = get_circuit_breaker("qr_test_a")
    cb_a._state = CircuitState.OPEN
    cb_a._opened_at = 9e18  # 很久以前(不会自动转 half-open)

    async def fn_a(x): return "a-result"  # 即使有结果,也不应被调用
    async def fn_b(x): return "b-result"

    results = await orch.execute_parallel({"a": fn_a, "b": fn_b}, "x")
    assert dict(results) == {"b": "b-result"}
