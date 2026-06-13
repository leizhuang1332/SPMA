"""熔断器——标准三态模型 (CLOSED/OPEN/HALF_OPEN)。

装饰器 API: @circuit_breaker("llm_sonnet")
编程式 API: await cb.call(coro_factory, fallback=...)

设计依据: SPMA-design-06 §6 熔断器设计 + Phase 4 hardening design spec §4
"""

from dataclasses import dataclass
from enum import Enum
from typing import Awaitable, Callable
import asyncio
import functools
import logging
import time

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreakerConfig:
    failure_threshold: int = 5
    open_duration_seconds: float = 30.0
    half_open_probe_count: int = 3
    half_open_success_threshold: int = 2


@dataclass
class CircuitBreakerStats:
    name: str
    state: CircuitState
    failure_count: int
    success_count: int
    last_failure_time: float | None
    last_success_time: float | None
    opened_at: float | None
    total_failures: int = 0
    total_successes: int = 0


class CircuitBreakerOpenError(Exception):
    """熔断器 OPEN 时抛出，调用方捕获后走降级路径。"""

    def __init__(self, name: str, retry_after_seconds: float):
        self.name = name
        self.retry_after_seconds = retry_after_seconds
        super().__init__(
            f"Circuit breaker '{name}' is OPEN. "
            f"Retry after {retry_after_seconds:.0f}s"
        )


class CircuitBreaker:
    """单个熔断器实例。协程安全（asyncio.Lock）。"""

    def __init__(
        self,
        name: str,
        config: CircuitBreakerConfig | None = None,
        on_state_change: Callable[..., Awaitable[None]] | None = None,
    ):
        self.name = name
        self.config = config or CircuitBreakerConfig()
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._half_open_probe_count = 0
        self._half_open_success_count = 0
        self._opened_at: float | None = None
        self._last_failure_time: float | None = None
        self._last_success_time: float | None = None
        self._total_failures = 0
        self._total_successes = 0
        self._lock = asyncio.Lock()
        self._on_state_change = on_state_change

    @property
    def state(self) -> CircuitState:
        return self._state

    @property
    def stats(self) -> CircuitBreakerStats:
        return CircuitBreakerStats(
            name=self.name,
            state=self._state,
            failure_count=self._failure_count,
            success_count=self._total_successes,
            last_failure_time=self._last_failure_time,
            last_success_time=self._last_success_time,
            opened_at=self._opened_at,
            total_failures=self._total_failures,
            total_successes=self._total_successes,
        )

    async def call(
        self,
        coro_factory: Callable[[], Awaitable],
        fallback: Callable[[], Awaitable] | None = None,
    ):
        """核心调用方法。

        CLOSED/HALF_OPEN: 执行 coro_factory，记录成功/失败。
        OPEN: 执行 fallback（如有），否则抛 CircuitBreakerOpenError。
        """
        async with self._lock:
            if self._state == CircuitState.OPEN:
                if self._opened_at is not None and (
                    time.time() - self._opened_at >= self.config.open_duration_seconds
                ):
                    await self._transition_to(CircuitState.HALF_OPEN)
                    self._half_open_probe_count = 0
                    self._half_open_success_count = 0
                else:
                    retry_after = (
                        self.config.open_duration_seconds
                        - (time.time() - (self._opened_at or time.time()))
                    )
                    if fallback:
                        return await fallback()
                    raise CircuitBreakerOpenError(self.name, max(0.0, retry_after))

        # 释放锁执行实际调用（避免长时间持锁）
        try:
            result = await coro_factory()
        except Exception:
            await self._on_failure()
            raise

        await self._on_success()
        return result

    async def _on_success(self) -> None:
        async with self._lock:
            self._last_success_time = time.time()
            self._total_successes += 1
            if self._state == CircuitState.CLOSED:
                self._failure_count = 0
            elif self._state == CircuitState.HALF_OPEN:
                self._half_open_probe_count += 1
                self._half_open_success_count += 1
                if self._half_open_probe_count >= self.config.half_open_probe_count:
                    if (
                        self._half_open_success_count
                        >= self.config.half_open_success_threshold
                    ):
                        await self._transition_to(CircuitState.CLOSED)
                        self._failure_count = 0
                    else:
                        await self._transition_to(CircuitState.OPEN)
                        self._opened_at = time.time()

    async def _on_failure(self) -> None:
        async with self._lock:
            self._failure_count += 1
            self._total_failures += 1
            self._last_failure_time = time.time()
            if self._state == CircuitState.CLOSED:
                if self._failure_count >= self.config.failure_threshold:
                    await self._transition_to(CircuitState.OPEN)
                    self._opened_at = time.time()
            elif self._state == CircuitState.HALF_OPEN:
                self._half_open_probe_count += 1
                if self._half_open_probe_count >= self.config.half_open_probe_count:
                    if self._half_open_success_count >= self.config.half_open_success_threshold:
                        await self._transition_to(CircuitState.CLOSED)
                        self._failure_count = 0
                    else:
                        await self._transition_to(CircuitState.OPEN)
                        self._opened_at = time.time()

    async def _transition_to(self, new_state: CircuitState) -> None:
        old_state = self._state
        self._state = new_state
        logger.info(
            f"Circuit breaker '{self.name}': {old_state.value} -> {new_state.value}"
        )
        if self._on_state_change:
            try:
                await self._on_state_change(self.name, old_state, new_state)
            except Exception:
                logger.exception(
                    f"Circuit breaker '{self.name}' state change callback failed"
                )

    async def reset(self) -> None:
        """手动重置到 CLOSED（运维操作）。"""
        async with self._lock:
            self._failure_count = 0
            self._half_open_probe_count = 0
            self._half_open_success_count = 0
            self._opened_at = None
            await self._transition_to(CircuitState.CLOSED)


# 全局注册表
_registry: dict[str, CircuitBreaker] = {}


def get_circuit_breaker(
    name: str,
    config: CircuitBreakerConfig | None = None,
    on_state_change: Callable[..., Awaitable[None]] | None = None,
) -> CircuitBreaker:
    """获取或创建熔断器实例。幂等——同名返回同一实例。"""
    if name not in _registry:
        _registry[name] = CircuitBreaker(name, config, on_state_change)
    return _registry[name]


def circuit_breaker(
    name: str, config: CircuitBreakerConfig | None = None
):
    """装饰器：为异步函数包裹熔断保护。

    Usage:
        @circuit_breaker("llm_sonnet")
        async def call_sonnet(prompt: str) -> str: ...
    """
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            cb = get_circuit_breaker(name, config)

            async def call():
                return await func(*args, **kwargs)

            return await cb.call(call)

        return wrapper

    return decorator


def get_all_stats() -> list[CircuitBreakerStats]:
    """获取所有熔断器状态（用于管理 API + metrics）。"""
    return [cb.stats for cb in _registry.values()]


def reset_all() -> None:
    """清空注册表（测试用）。"""
    _registry.clear()
