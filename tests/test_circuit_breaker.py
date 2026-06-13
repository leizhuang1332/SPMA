"""熔断器状态机测试。"""
import asyncio
import pytest
from spma.infrastructure.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitState,
    CircuitBreakerOpenError,
)


async def _raise(exc: Exception):
    """辅助函数：创建一个 awaitable 的异常抛出。"""
    raise exc


async def _ok(value="ok"):
    """辅助函数：创建一个返回值的 awaitable。"""
    return value


class TestCircuitBreakerStateMachine:
    """测试 CLOSED → OPEN → HALF_OPEN → CLOSED 全路径。"""

    async def test_closed_passes_calls(self):
        """CLOSED 状态正常通过调用。"""
        cb = CircuitBreaker("test")
        result = await cb.call(lambda: _ok("ok"))
        assert result == "ok"
        assert cb.state == CircuitState.CLOSED

    async def test_opens_after_failure_threshold(self):
        """连续失败 >= 阈值 → OPEN。"""
        cb = CircuitBreaker("test", CircuitBreakerConfig(failure_threshold=3))
        for _ in range(3):
            with pytest.raises(ValueError):
                await cb.call(lambda: _raise(ValueError("fail")))
        assert cb.state == CircuitState.OPEN

    async def test_open_rejects_calls(self):
        """OPEN 状态拒绝请求，抛 CircuitBreakerOpenError。"""
        cb = CircuitBreaker(
            "test",
            CircuitBreakerConfig(failure_threshold=1, open_duration_seconds=30.0),
        )
        with pytest.raises(ValueError):
            await cb.call(lambda: _raise(ValueError("fail")))
        assert cb.state == CircuitState.OPEN
        with pytest.raises(CircuitBreakerOpenError):
            await cb.call(lambda: _ok("ok"))

    async def test_transitions_to_half_open_after_duration(self):
        """OPEN 持续时间到达 → HALF_OPEN。"""
        cb = CircuitBreaker(
            "test",
            CircuitBreakerConfig(failure_threshold=1, open_duration_seconds=0.01),
        )
        with pytest.raises(ValueError):
            await cb.call(lambda: _raise(ValueError("fail")))
        assert cb.state == CircuitState.OPEN
        await asyncio.sleep(0.02)
        # 下一次调用进入 HALF_OPEN（允许探测）
        result = await cb.call(lambda: _ok("ok"))
        assert cb.state == CircuitState.CLOSED  # 成功探测后恢复

    async def test_half_open_failure_returns_to_open(self):
        """HALF_OPEN 失败 < 阈值 → 重新 OPEN。"""
        cb = CircuitBreaker(
            "test",
            CircuitBreakerConfig(
                failure_threshold=1,
                open_duration_seconds=0.01,
                half_open_probe_count=3,
                half_open_success_threshold=2,
            ),
        )
        with pytest.raises(ValueError):
            await cb.call(lambda: _raise(ValueError("fail")))
        await asyncio.sleep(0.02)
        # HALF_OPEN: 第1次成功
        await cb.call(lambda: _ok("ok"))
        # 第2次失败
        with pytest.raises(ValueError):
            await cb.call(lambda: _raise(ValueError("fail")))
        # 还需累计失败直到探测数耗尽，重新 OPEN
        with pytest.raises(ValueError):
            await cb.call(lambda: _raise(ValueError("fail")))
        assert cb.state == CircuitState.OPEN

    async def test_manual_reset(self):
        """手动 reset 恢复 CLOSED。"""
        cb = CircuitBreaker("test", CircuitBreakerConfig(failure_threshold=1))
        with pytest.raises(ValueError):
            await cb.call(lambda: _raise(ValueError("fail")))
        assert cb.state == CircuitState.OPEN
        await cb.reset()
        assert cb.state == CircuitState.CLOSED
        assert cb.stats.failure_count == 0

    async def test_fallback_on_open(self):
        """OPEN 时提供 fallback → 执行 fallback 而非抛异常。"""
        cb = CircuitBreaker("test", CircuitBreakerConfig(failure_threshold=1))
        with pytest.raises(ValueError):
            await cb.call(lambda: _raise(ValueError("fail")))
        result = await cb.call(
            lambda: _ok("primary"),
            fallback=lambda: _ok("fallback"),
        )
        assert result == "fallback"

    async def test_success_resets_failure_count_in_closed(self):
        """CLOSED 状态下，成功调用重置连续失败计数。"""
        cb = CircuitBreaker("test", CircuitBreakerConfig(failure_threshold=5))
        for _ in range(2):
            with pytest.raises(ValueError):
                await cb.call(lambda: _raise(ValueError("fail")))
        assert cb.stats.failure_count == 2
        await cb.call(lambda: _ok("ok"))
        assert cb.stats.failure_count == 0
        assert cb.state == CircuitState.CLOSED

    async def test_state_change_callback(self):
        """状态变更时调用回调。"""
        events = []

        async def on_change(name, old, new):
            events.append((name, old, new))

        cb = CircuitBreaker(
            "test", CircuitBreakerConfig(failure_threshold=1), on_state_change=on_change
        )
        with pytest.raises(ValueError):
            await cb.call(lambda: _raise(ValueError("fail")))
        assert ("test", CircuitState.CLOSED, CircuitState.OPEN) in events
