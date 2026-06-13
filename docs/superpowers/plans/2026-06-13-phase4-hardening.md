# Phase 4 生产加固 — 降级体系 + 熔断器 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现六级降级体系（L0-L5）+ 标准三态熔断器，使系统在外部依赖故障时自动降级并能在恢复后自动切回。

**Architecture:** 降级系统（策略模式，每级独立 DegradationAction）+ 熔断器（@circuit_breaker 装饰器注入调用路径），两者分层协作：熔断器做单点快速失败保护，降级系统做全局策略编排。FeatureFlagService 提供秒级 Agent 回退能力。审计日志 stdout+PG 双写，metrics 暴露 Prometheus gauge 格式。

**Tech Stack:** Python 3.12+, asyncio, FastAPI, pytest, Redis (async), PostgreSQL

---

### Task 1: 降级事件类型定义

**Files:**
- Create: `src/spma/infrastructure/degradation/__init__.py`
- Create: `src/spma/infrastructure/degradation/events.py`
- Create: `src/spma/infrastructure/degradation/actions/__init__.py`
- Modify: `src/spma/infrastructure/degradation.py`

- [ ] **Step 1: 创建 degradation 子包目录结构**

```bash
mkdir -p src/spma/infrastructure/degradation/actions
```

- [ ] **Step 2: 编写 events.py — 降级事件数据类**

```python
"""降级系统事件定义。"""

from dataclasses import dataclass, field
from typing import Literal
import time

DegradationLevel = Literal["L0", "L1", "L2", "L3", "L4", "L5"]


@dataclass
class DegradationEvent:
    """降级/恢复事件。"""
    event_type: Literal["degradation.triggered", "degradation.recovered", "degradation.manual"]
    level: DegradationLevel
    reason: str
    timestamp: float = field(default_factory=time.time)
    previous_level: DegradationLevel | None = None
    triggered_by: Literal["auto", "manual"] = "auto"
    operator: str | None = None  # 手动触发时的操作人


@dataclass
class RecoveryEvent:
    """自动恢复事件。"""
    from_level: DegradationLevel
    to_level: DegradationLevel
    reason: str
    timestamp: float = field(default_factory=time.time)
    checks_passed: int = 0  # 连续健康检查通过次数
```

- [ ] **Step 3: 编写 degradation/__init__.py**

```python
"""六级降级管理体系。

导出: DegradationManager, DegradationLevel, DegradationEvent, RecoveryEvent
"""

from spma.infrastructure.degradation.events import (
    DegradationEvent,
    DegradationLevel,
    RecoveryEvent,
)

__all__ = [
    "DegradationLevel",
    "DegradationEvent",
    "RecoveryEvent",
]
```

- [ ] **Step 4: 编写 actions/__init__.py**

```python
"""降级动作策略实现。"""
```

- [ ] **Step 5: 迁移 degradation.py — 保留 DegradationLevel，移除旧配置和旧类**

将 `src/spma/infrastructure/degradation.py` 更新为 re-export 形式：

```python
"""六级降级管理器——从 degradation/ 子包 re-export。

为兼容已有 import，所有实现已迁移到 degradation/ 子包。
"""

from spma.infrastructure.degradation.events import DegradationLevel

__all__ = ["DegradationLevel"]
```

- [ ] **Step 6: 验证 import 路径可用**

```bash
python -c "from spma.infrastructure.degradation.events import DegradationLevel, DegradationEvent; print('OK')"
```

Expected: `OK`

- [ ] **Step 7: Commit**

```bash
git add src/spma/infrastructure/degradation/ src/spma/infrastructure/degradation.py
git commit -m "feat(degradation): add event types and subpackage structure"
```

---

### Task 2: 熔断器 — 状态机核心

**Files:**
- Create: `tests/test_circuit_breaker.py`
- Modify: `src/spma/infrastructure/circuit_breaker.py`

- [ ] **Step 1: 编写熔断器状态机测试**

```python
"""熔断器状态机测试。"""
import asyncio
import pytest
from spma.infrastructure.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitState,
    CircuitBreakerOpenError,
)


class TestCircuitBreakerStateMachine:
    """测试 CLOSED → OPEN → HALF_OPEN → CLOSED 全路径。"""

    async def test_closed_passes_calls(self):
        """CLOSED 状态正常通过调用。"""
        cb = CircuitBreaker("test")
        result = await cb.call(lambda: asyncio.sleep(0) or "ok")
        assert result == "ok"
        assert cb.state == CircuitState.CLOSED

    async def test_opens_after_failure_threshold(self):
        """连续失败 >= 阈值 → OPEN。"""
        cb = CircuitBreaker("test", CircuitBreakerConfig(failure_threshold=3))
        for _ in range(3):
            with pytest.raises(ValueError):
                await cb.call(lambda: (_ for _ in ()).throw(ValueError("fail")))
        assert cb.state == CircuitState.OPEN

    async def test_open_rejects_calls(self):
        """OPEN 状态拒绝请求，抛 CircuitBreakerOpenError。"""
        cb = CircuitBreaker("test", CircuitBreakerConfig(
            failure_threshold=1, open_duration_seconds=30.0
        ))
        with pytest.raises(ValueError):
            await cb.call(lambda: (_ for _ in ()).throw(ValueError("fail")))
        assert cb.state == CircuitState.OPEN
        with pytest.raises(CircuitBreakerOpenError):
            await cb.call(lambda: asyncio.sleep(0) or "ok")

    async def test_transitions_to_half_open_after_duration(self):
        """OPEN 持续时间到达 → HALF_OPEN。"""
        cb = CircuitBreaker("test", CircuitBreakerConfig(
            failure_threshold=1, open_duration_seconds=0.01
        ))
        with pytest.raises(ValueError):
            await cb.call(lambda: (_ for _ in ()).throw(ValueError("fail")))
        assert cb.state == CircuitState.OPEN
        await asyncio.sleep(0.02)
        # 下一次调用进入 HALF_OPEN（允许探测）
        result = await cb.call(lambda: asyncio.sleep(0) or "ok")
        assert cb.state == CircuitState.CLOSED  # 成功探测后恢复

    async def test_half_open_failure_returns_to_open(self):
        """HALF_OPEN 失败 < 阈值 → 重新 OPEN。"""
        cb = CircuitBreaker("test", CircuitBreakerConfig(
            failure_threshold=1, open_duration_seconds=0.01,
            half_open_probe_count=3, half_open_success_threshold=2
        ))
        with pytest.raises(ValueError):
            await cb.call(lambda: (_ for _ in ()).throw(ValueError("fail")))
        await asyncio.sleep(0.02)
        # HALF_OPEN: 第1次成功
        await cb.call(lambda: asyncio.sleep(0) or "ok")
        # 第2次失败
        with pytest.raises(ValueError):
            await cb.call(lambda: (_ for _ in ()).throw(ValueError("fail")))
        # 还需累计失败直到探测数耗尽，重新 OPEN
        with pytest.raises(ValueError):
            await cb.call(lambda: (_ for _ in ()).throw(ValueError("fail")))
        assert cb.state == CircuitState.OPEN

    async def test_manual_reset(self):
        """手动 reset 恢复 CLOSED。"""
        cb = CircuitBreaker("test", CircuitBreakerConfig(failure_threshold=1))
        with pytest.raises(ValueError):
            await cb.call(lambda: (_ for _ in ()).throw(ValueError("fail")))
        assert cb.state == CircuitState.OPEN
        await cb.reset()
        assert cb.state == CircuitState.CLOSED
        assert cb.stats.failure_count == 0

    async def test_fallback_on_open(self):
        """OPEN 时提供 fallback → 执行 fallback 而非抛异常。"""
        cb = CircuitBreaker("test", CircuitBreakerConfig(failure_threshold=1))
        with pytest.raises(ValueError):
            await cb.call(lambda: (_ for _ in ()).throw(ValueError("fail")))
        result = await cb.call(
            lambda: asyncio.sleep(0) or "primary",
            fallback=lambda: asyncio.sleep(0) or "fallback"
        )
        assert result == "fallback"

    async def test_success_resets_failure_count_in_closed(self):
        """CLOSED 状态下，成功调用重置连续失败计数。"""
        cb = CircuitBreaker("test", CircuitBreakerConfig(failure_threshold=5))
        for _ in range(2):
            with pytest.raises(ValueError):
                await cb.call(lambda: (_ for _ in ()).throw(ValueError("fail")))
        assert cb.stats.failure_count == 2
        await cb.call(lambda: asyncio.sleep(0) or "ok")
        assert cb.stats.failure_count == 0
        assert cb.state == CircuitState.CLOSED

    async def test_state_change_callback(self):
        """状态变更时调用回调。"""
        events = []
        async def on_change(name, old, new):
            events.append((name, old, new))

        cb = CircuitBreaker("test", CircuitBreakerConfig(failure_threshold=1),
                           on_state_change=on_change)
        with pytest.raises(ValueError):
            await cb.call(lambda: (_ for _ in ()).throw(ValueError("fail")))
        assert ("test", CircuitState.CLOSED, CircuitState.OPEN) in events
```

- [ ] **Step 2: 运行测试确认失败**

```bash
python -m pytest tests/test_circuit_breaker.py -v
```

Expected: 全部 FAIL（CircuitBreaker 类不存在或未实现）

- [ ] **Step 3: 实现熔断器核心**

将 `src/spma/infrastructure/circuit_breaker.py` 替换为：

```python
"""熔断器——标准三态模型 (CLOSED/OPEN/HALF_OPEN)。

装饰器 API: @circuit_breaker("llm_sonnet")
编程式 API: await cb.call(coro_factory, fallback=...)

设计依据: SPMA-design-06 §6 熔断器设计 + Phase 4 hardening design spec §4
"""

from dataclasses import dataclass, field
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
            success_count=0,
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
                if self._opened_at and (
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
                    raise CircuitBreakerOpenError(
                        self.name, max(0, retry_after)
                    )

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
                if (
                    self._half_open_probe_count >= self.config.half_open_probe_count
                ):
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
                    await self._transition_to(CircuitState.OPEN)
                    self._opened_at = time.time()

    async def _transition_to(self, new_state: CircuitState) -> None:
        old_state = self._state
        self._state = new_state
        logger.info(
            f"Circuit breaker '{self.name}': {old_state.value} → {new_state.value}"
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
```

- [ ] **Step 4: 运行测试确认通过**

```bash
python -m pytest tests/test_circuit_breaker.py -v
```

Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add src/spma/infrastructure/circuit_breaker.py tests/test_circuit_breaker.py
git commit -m "feat(circuit-breaker): implement three-state circuit breaker with decorator API"
```

---

### Task 3: FeatureFlagService 实现

**Files:**
- Create: `tests/test_feature_flags.py`
- Create: `config/feature_flags.yaml`
- Modify: `src/spma/infrastructure/feature_flags.py`

- [ ] **Step 1: 编写 FeatureFlag 测试**

```python
"""Feature Flag 服务测试。"""
import pytest
from spma.infrastructure.feature_flags import FeatureFlagService, FeatureFlagUpdate


class TestFeatureFlagService:
    """测试 FeatureFlagService 核心功能。"""

    def test_is_enabled_returns_default(self):
        ff = FeatureFlagService(defaults={"doc_agentic": True, "sql_agentic": False})
        assert ff.is_enabled("doc_agentic") is True
        assert ff.is_enabled("sql_agentic") is False

    def test_is_enabled_unknown_flag_returns_false(self):
        ff = FeatureFlagService()
        assert ff.is_enabled("nonexistent") is False

    @pytest.mark.asyncio
    async def test_update_flag_immediate_effect(self):
        """update_flag 后 is_enabled 立即返回新值（秒级生效）。"""
        ff = FeatureFlagService(defaults={"doc_agentic": True})
        await ff.update_flag("doc_agentic", False, "test rollback", "tester")
        assert ff.is_enabled("doc_agentic") is False

    @pytest.mark.asyncio
    async def test_update_flag_records_change_log(self):
        ff = FeatureFlagService(defaults={"doc_agentic": True})
        await ff.update_flag("doc_agentic", False, "latency spike", "ops")
        history = ff.get_change_history()
        assert len(history) == 1
        assert history[0].flag_name == "doc_agentic"
        assert history[0].value is False
        assert history[0].reason == "latency spike"
        assert history[0].updated_by == "ops"

    def test_get_all_flags_returns_copy(self):
        ff = FeatureFlagService(defaults={"a": True, "b": False})
        flags = ff.get_all_flags()
        flags["a"] = False  # 不应影响内部状态
        assert ff.is_enabled("a") is True

    def test_from_yaml_loads_defaults(self, tmp_path):
        import yaml
        config = {"agents": {"doc_agentic": True, "sql_agentic": False,
                              "code_agentic": True, "supervisor_agentic": False,
                              "synth_agentic": False}}
        yaml_path = tmp_path / "flags.yaml"
        yaml_path.write_text(yaml.dump(config))
        ff = FeatureFlagService.from_yaml(str(yaml_path))
        assert ff.is_enabled("doc_agentic") is True
        assert ff.is_enabled("sql_agentic") is False

    @pytest.mark.asyncio
    async def test_change_history_truncated(self):
        ff = FeatureFlagService()
        for i in range(60):
            await ff.update_flag(f"flag_{i}", True, "test", "tester")
        history = ff.get_change_history(limit=50)
        assert len(history) == 50
```

- [ ] **Step 2: 编写 feature_flags.yaml 默认配置**

```yaml
# Feature Flags 默认配置
# 启动时加载到 FeatureFlagService，运行时修改秒级生效

agents:
  # Agentic 模式开关
  sql_agentic: false
  doc_agentic: false
  code_agentic: false
  supervisor_agentic: false
  synth_agentic: false

  # 查询改写功能开关
  query_normalization: true
  query_expansion: true
  query_decomposition: false
  query_hyde: false
  query_step_back: false
  query_context_aware: false

  # 检索增强
  hybrid_search_weighted: false
  code_fallback: true
  sql_user_confirmation: true
  cross_reranker: false

  # 降级
  degradation_auto_recovery: true
```

- [ ] **Step 3: 运行测试确认失败**

```bash
python -m pytest tests/test_feature_flags.py -v
```

Expected: FAIL

- [ ] **Step 4: 实现 FeatureFlagService**

将 `src/spma/infrastructure/feature_flags.py` 替换为：

```python
"""Feature Flag 服务——每个 Agent 独立开关，秒级生效。

存储: 内存 dict + 可选 Redis 同步
回滚触发: 虚假信心率>15% OR P99延迟恶化>30% OR Token成本恶化>50%

设计依据: SPMA-design-06 §5 Agent回滚机制 + API-06 §3 Feature Flags
"""

from dataclasses import dataclass, field
from typing import Any
import logging

logger = logging.getLogger(__name__)


@dataclass
class FeatureFlagUpdate:
    flag_name: str
    value: bool
    reason: str
    updated_by: str


class FeatureFlagService:
    """Feature Flag 服务——秒级生效 + 变更审计。"""

    def __init__(
        self,
        defaults: dict[str, bool] | None = None,
        redis_client=None,
    ):
        self._flags: dict[str, bool] = dict(defaults or {})
        self._redis = redis_client
        self._change_log: list[FeatureFlagUpdate] = []

    def is_enabled(self, flag_name: str, context: dict | None = None) -> bool:
        """O(1) 读取，无 I/O。"""
        return self._flags.get(flag_name, False)

    def get_all_flags(self) -> dict[str, bool]:
        """获取所有 flag 的当前状态（返回副本）。"""
        return dict(self._flags)

    async def update_flag(
        self,
        flag_name: str,
        value: bool,
        reason: str,
        updated_by: str,
    ) -> bool:
        """写内存立即生效 → 异步写 Redis → 记录变更日志。"""
        old = self._flags.get(flag_name)
        self._flags[flag_name] = value
        update = FeatureFlagUpdate(
            flag_name=flag_name,
            value=value,
            reason=reason,
            updated_by=updated_by,
        )
        self._change_log.append(update)
        logger.info(
            f"Feature flag '{flag_name}': {old} → {value} "
            f"({reason}, by {updated_by})"
        )
        if self._redis:
            try:
                await self._redis.set(
                    f"ff:{flag_name}", "true" if value else "false"
                )
            except Exception:
                pass  # Redis 写失败不影响主路径
        return True

    def get_change_history(self, limit: int = 50) -> list[FeatureFlagUpdate]:
        """返回最近的 flag 变更记录。"""
        return self._change_log[-limit:]

    @classmethod
    def from_yaml(cls, yaml_path: str) -> "FeatureFlagService":
        """从 YAML 配置文件加载默认值。

        YAML 格式:
            agents:
              doc_agentic: false
              sql_agentic: true
              ...
        """
        import yaml

        with open(yaml_path) as f:
            config = yaml.safe_load(f)

        defaults: dict[str, bool] = {}
        if config and "agents" in config:
            for key, value in config["agents"].items():
                if isinstance(value, bool):
                    defaults[key] = value

        logger.info(
            f"Loaded {len(defaults)} feature flags from {yaml_path}"
        )
        return cls(defaults=defaults)
```

- [ ] **Step 5: 运行测试确认通过**

```bash
python -m pytest tests/test_feature_flags.py -v
```

Expected: 全部 PASS

- [ ] **Step 6: Commit**

```bash
git add src/spma/infrastructure/feature_flags.py tests/test_feature_flags.py config/feature_flags.yaml
git commit -m "feat(feature-flags): implement FeatureFlagService with YAML loading and change log"
```

---

### Task 4: 审计日志实现

**Files:**
- Create: `tests/test_audit.py`
- Modify: `src/spma/infrastructure/audit.py`

- [ ] **Step 1: 编写 AuditLogger 测试**

```python
"""审计日志测试。"""
import asyncio
import json
import pytest
from spma.infrastructure.audit import AuditLogger, AuditEvent


class TestAuditLogger:
    """测试 AuditLogger 核心功能。"""

    @pytest.mark.asyncio
    async def test_log_writes_to_stdout(self, capsys):
        """log() 同步写 stdout（结构化 JSON）。"""
        al = AuditLogger()
        await al.log(AuditEvent(
            event_type="degradation.triggered",
            level="L1",
            details={"reason": "LLM timeout > 10%"},
        ))
        captured = capsys.readouterr()
        # stdout 应包含 JSON 格式的审计记录
        assert "degradation.triggered" in captured.err or "degradation.triggered" in captured.out

    @pytest.mark.asyncio
    async def test_log_enqueues_for_db(self):
        """log() 将事件入队待批量写 PG。"""
        al = AuditLogger()
        await al.log(AuditEvent(
            event_type="circuit_breaker.open",
            details={"breaker_name": "llm_sonnet"},
        ))
        assert len(al._queue) == 1

    @pytest.mark.asyncio
    async def test_log_is_non_blocking(self):
        """log() 在无 DB 时也不抛异常（非阻塞）。"""
        al = AuditLogger()  # db_pool=None
        # 不应抛异常
        await al.log(AuditEvent(event_type="degradation.recovered", level="L0"))

    @pytest.mark.asyncio
    async def test_flush_clears_queue(self):
        """flush 清空队列。"""
        al = AuditLogger(batch_size=2)
        await al.log(AuditEvent(event_type="feature_flag.changed"))
        await al.log(AuditEvent(event_type="feature_flag.changed"))
        assert len(al._queue) == 2
        await al._flush()
        assert len(al._queue) == 0

    @pytest.mark.asyncio
    async def test_event_serialization(self):
        """AuditEvent 可 JSON 序列化。"""
        event = AuditEvent(
            event_type="degradation.manual",
            level="L3",
            details={"reason": "scheduled maintenance"},
            operator="admin",
        )
        d = json.dumps(event.__dict__, default=str)
        assert "degradation.manual" in d
        assert "L3" in d
        assert "admin" in d
```

- [ ] **Step 2: 运行测试确认失败**

```bash
python -m pytest tests/test_audit.py -v
```

Expected: FAIL

- [ ] **Step 3: 实现 AuditLogger**

将 `src/spma/infrastructure/audit.py` 替换为：

```python
"""审计日志——结构化 JSON → stdout + 批量异步写 PostgreSQL。

记录: 降级/恢复事件、熔断器状态变更、Feature Flag 变更。
stdout 确保即使 PG 不可用也不丢日志。

设计依据: API-00 §6 审计日志结构 + Phase 4 hardening design spec §6
"""

from dataclasses import dataclass, field, asdict
from typing import Literal
import asyncio
import json
import logging
import time

logger = logging.getLogger(__name__)

AuditEventType = Literal[
    "degradation.triggered",
    "degradation.recovered",
    "degradation.manual",
    "circuit_breaker.open",
    "circuit_breaker.close",
    "circuit_breaker.half_open",
    "feature_flag.changed",
]


@dataclass
class AuditEvent:
    event_type: AuditEventType
    timestamp: float = field(default_factory=time.time)
    level: str | None = None
    details: dict | None = None
    operator: str | None = None


class AuditLogger:
    """审计日志——异步批量写入，不阻塞主路径。"""

    def __init__(self, db_pool=None, batch_size: int = 10,
                 flush_interval: float = 5.0):
        self._db = db_pool
        self._queue: list[AuditEvent] = []
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._lock = asyncio.Lock()
        self._flush_task: asyncio.Task | None = None

    async def log(self, event: AuditEvent) -> None:
        """记录事件（非阻塞）。先写 stdout，再入队。"""
        # 1. 结构化 JSON 到 stdout（日志采集器可抓取）
        logger.info(
            json.dumps(asdict(event), ensure_ascii=False, default=str)
        )
        # 2. 入队批量写 PG
        async with self._lock:
            self._queue.append(event)

    async def _flush(self) -> None:
        """批量写入队列中的事件到 PG。"""
        async with self._lock:
            if not self._queue:
                return
            batch = self._queue[:]
            self._queue = []

        if self._db:
            try:
                async with self._db.acquire() as conn:
                    values = [
                        (
                            e.event_type,
                            e.timestamp,
                            e.level,
                            json.dumps(e.details or {}, ensure_ascii=False),
                            e.operator,
                        )
                        for e in batch
                    ]
                    await conn.executemany(
                        """INSERT INTO audit_logs
                           (event_type, timestamp, level, details, operator)
                           VALUES ($1, to_timestamp($2), $3, $4::jsonb, $5)""",
                        values,
                    )
            except Exception:
                logger.exception("Failed to flush audit events to PostgreSQL")

    async def _flush_loop(self) -> None:
        """后台循环：每 flush_interval 秒或满 batch_size 条时批量写。"""
        last_flush = time.time()
        while True:
            await asyncio.sleep(1)
            should_flush = (
                len(self._queue) >= self._batch_size
                or (self._queue and time.time() - last_flush >= self._flush_interval)
            )
            if should_flush:
                await self._flush()
                last_flush = time.time()

    async def start(self) -> None:
        """启动后台 flush 循环。"""
        if self._db and self._flush_task is None:
            self._flush_task = asyncio.create_task(self._flush_loop())

    async def stop(self) -> None:
        """停止后台循环并 flush 剩余事件。"""
        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
            self._flush_task = None
        await self._flush()


# 全局单例
_audit_logger: AuditLogger | None = None


def get_audit_logger() -> AuditLogger:
    global _audit_logger
    if _audit_logger is None:
        _audit_logger = AuditLogger()
    return _audit_logger
```

- [ ] **Step 4: 运行测试确认通过**

```bash
python -m pytest tests/test_audit.py -v
```

Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add src/spma/infrastructure/audit.py tests/test_audit.py
git commit -m "feat(audit): implement AuditLogger with stdout+PG dual write"
```

---

### Task 5: Metrics 暴露

**Files:**
- Modify: `src/spma/infrastructure/metrics.py`

- [ ] **Step 1: 实现 metrics 数据类**

将 `src/spma/infrastructure/metrics.py` 替换为：

```python
"""基础设施指标——不依赖 Prometheus SDK，暴露 gauge/getter 格式。

后续集成 Prometheus 时直接调用 as_prometheus_gauges() 即可。
"""

from dataclasses import dataclass, field
from typing import Literal
import time

CircuitStateLabel = Literal["closed", "open", "half_open"]
DegradationLevelLabel = Literal["L0", "L1", "L2", "L3", "L4", "L5"]

_LEVEL_TO_INT: dict[DegradationLevelLabel, int] = {
    "L0": 0, "L1": 1, "L2": 2, "L3": 3, "L4": 4, "L5": 5,
}


@dataclass
class DegradationMetrics:
    """降级系统指标。"""
    current_level: DegradationLevelLabel = "L0"
    degradation_count_total: int = 0
    last_degradation_at: float | None = None
    last_recovery_at: float | None = None
    time_in_current_level_seconds: float = 0.0

    def record_degradation(self, level: DegradationLevelLabel) -> None:
        self.current_level = level
        self.degradation_count_total += 1
        self.last_degradation_at = time.time()

    def record_recovery(self, level: DegradationLevelLabel) -> None:
        self.current_level = level
        self.last_recovery_at = time.time()

    def as_prometheus_gauges(self) -> dict[str, float]:
        """返回 {metric_name: value}，供 Prometheus exporter 使用。"""
        return {
            "spma_degradation_level": _LEVEL_TO_INT[self.current_level],
            "spma_degradation_count_total": float(self.degradation_count_total),
        }


@dataclass
class AgentMetrics:
    """Agent 专用指标——各 Agent 的延迟、成本、信心率。"""
    agent_rounds_p99: float = 0.0
    agent_false_confidence_rate: float = 0.0
    agent_early_stop_rate: float = 0.0
    agent_degradation_rate: float = 0.0
    supervisor_reschedule_rate: float = 0.0
    supervisor_timeout_rate: float = 0.0

    def as_prometheus_gauges(self) -> dict[str, float]:
        return {
            "spma_agent_rounds_p99": self.agent_rounds_p99,
            "spma_agent_false_confidence_rate": self.agent_false_confidence_rate,
            "spma_agent_degradation_rate": self.agent_degradation_rate,
            "spma_supervisor_reschedule_rate": self.supervisor_reschedule_rate,
            "spma_supervisor_timeout_rate": self.supervisor_timeout_rate,
        }


# 全局单例
degradation_metrics = DegradationMetrics()
agent_metrics = AgentMetrics()
```

- [ ] **Step 2: 验证 import**

```bash
python -c "from spma.infrastructure.metrics import degradation_metrics; print(degradation_metrics.current_level)"
```

Expected: `L0`

- [ ] **Step 3: Commit**

```bash
git add src/spma/infrastructure/metrics.py
git commit -m "feat(metrics): expose degradation and agent metrics as Prometheus-compatible gauges"
```

---

### Task 6: DegradationAction 基类 + L5 静态兜底

**Files:**
- Create: `src/spma/infrastructure/degradation/actions/base.py`
- Create: `src/spma/infrastructure/degradation/actions/l5_static.py`
- Create: `tests/test_degradation_actions.py`

- [ ] **Step 1: 编写 actions 测试**

```python
"""降级动作策略测试。"""
import pytest
from spma.infrastructure.degradation.actions.base import DegradationAction
from spma.infrastructure.degradation.actions.l5_static import L5StaticFallback
from spma.infrastructure.degradation.events import DegradationLevel


class TestDegradationActionBase:
    """测试基类契约。"""

    def test_subclass_must_define_level(self):
        """子类必须定义 level 类属性。"""
        class Incomplete(DegradationAction):
            pass
        with pytest.raises(TypeError):
            Incomplete()  # 抽象类不可实例化

    def test_concrete_action_has_level(self):
        """具体子类可实例化且 level 正确。"""
        action = L5StaticFallback(faq_json={"questions": []})
        assert action.level == "L5"


class TestL5StaticFallback:
    """测试 L5 静态 FAQ 兜底。"""

    def test_level_is_l5(self):
        action = L5StaticFallback(faq_json={"faq": []})
        assert action.level == "L5"

    @pytest.mark.asyncio
    async def test_execute_sets_active(self):
        action = L5StaticFallback(faq_json={"faq": [{"q": "test", "a": "answer"}]})
        assert action.is_active is False
        await action.execute("all services down")
        assert action.is_active is True

    @pytest.mark.asyncio
    async def test_execute_is_idempotent(self):
        action = L5StaticFallback(faq_json={"faq": []})
        await action.execute("first")
        await action.execute("second")
        assert action.is_active is True  # 重复执行安全

    @pytest.mark.asyncio
    async def test_recover_deactivates(self):
        action = L5StaticFallback(faq_json={"faq": []})
        await action.execute("down")
        result = await action.recover()
        assert result is True
        assert action.is_active is False

    @pytest.mark.asyncio
    async def test_health_check_returns_false_when_active(self):
        """L5 激活时 health_check 返回 False（系统仍不可用）。"""
        action = L5StaticFallback(faq_json={"faq": []})
        await action.execute("all down")
        assert await action.health_check() is False

    def test_recovery_conditions_met_when_inactive(self):
        """正常情况下，恢复条件可以满足（返回 True 意味着不需要处于 L5）。"""
        action = L5StaticFallback(faq_json={"faq": []})
        # L5 未激活，无需恢复
        assert action.recovery_conditions_met() is True

    def test_recovery_check_interval(self):
        action = L5StaticFallback(faq_json={"faq": []})
        assert action.recovery_check_interval_seconds == 60

    def test_get_faq_returns_faq_data(self):
        faq = {"faq": [{"q": "What is SPMA?", "a": "A RAG system."}]}
        action = L5StaticFallback(faq_json=faq)
        assert action.get_faq() == faq
```

- [ ] **Step 2: 运行测试确认失败**

```bash
python -m pytest tests/test_degradation_actions.py -v
```

Expected: FAIL

- [ ] **Step 3: 实现 DegradationAction 抽象基类**

```python
"""降级动作抽象基类。"""

from abc import ABC, abstractmethod
from spma.infrastructure.degradation.events import DegradationLevel


class DegradationAction(ABC):
    """单个降级级别的策略基类。每个具体类实现一级降级。

    约束:
    - execute() 必须幂等
    - recover() 只能从当前级别恢复
    - health_check() 返回 True=正常, False=异常
    """

    level: DegradationLevel

    @abstractmethod
    async def health_check(self) -> bool:
        """检查该级别依赖是否健康。返回 True=正常。"""
        ...

    @abstractmethod
    async def execute(self, reason: str) -> None:
        """执行降级动作。幂等——重复调用安全。"""
        ...

    @abstractmethod
    async def recover(self) -> bool:
        """尝试恢复。返回 True=恢复成功。"""
        ...

    @abstractmethod
    def recovery_conditions_met(self) -> bool:
        """检查自动恢复条件是否满足（同步，无副作用）。"""
        ...

    @property
    @abstractmethod
    def recovery_check_interval_seconds(self) -> int:
        """恢复检查间隔（秒）。"""
        ...
```

- [ ] **Step 4: 实现 L5StaticFallback**

```python
"""L5: 所有动态服务不可用 → 返回预定义 FAQ + 提示联系管理员。"""

from spma.infrastructure.degradation.actions.base import DegradationAction
from spma.infrastructure.degradation.events import DegradationLevel
import logging

logger = logging.getLogger(__name__)

DEFAULT_FAQ = {
    "faq": [
        {
            "q": "系统暂时不可用怎么办？",
            "a": "当前系统正在维护中，请稍后重试。如紧急需要，请联系管理员。",
        },
        {
            "q": "如何联系管理员？",
            "a": "请发送邮件至 admin@company.com 或在企业微信中搜索「SPMA 运维」。",
        },
    ],
    "message": "系统当前不可用，以下为常见问题解答。如需帮助，请联系管理员。",
}


class L5StaticFallback(DegradationAction):
    """L5 静态兜底：返回预定义 FAQ。"""

    level: DegradationLevel = "L5"

    def __init__(self, faq_json: dict | None = None):
        self._faq = faq_json or DEFAULT_FAQ
        self.is_active = False

    async def health_check(self) -> bool:
        """L5 激活时，系统仍不可用。"""
        return not self.is_active

    async def execute(self, reason: str) -> None:
        if not self.is_active:
            logger.critical(f"L5 静态兜底激活: {reason}")
            self.is_active = True

    async def recover(self) -> bool:
        if self.is_active:
            logger.info("L5 静态兜底恢复")
            self.is_active = False
        return True

    def recovery_conditions_met(self) -> bool:
        return not self.is_active

    @property
    def recovery_check_interval_seconds(self) -> int:
        return 60

    def get_faq(self) -> dict:
        return self._faq
```

- [ ] **Step 5: 运行测试确认通过**

```bash
python -m pytest tests/test_degradation_actions.py -v
```

Expected: 全部 PASS

- [ ] **Step 6: Commit**

```bash
git add src/spma/infrastructure/degradation/actions/base.py src/spma/infrastructure/degradation/actions/l5_static.py tests/test_degradation_actions.py
git commit -m "feat(degradation): add DegradationAction base class and L5 static fallback"
```

---

### Task 7: L1-L4 降级动作实现

**Files:**
- Create: `src/spma/infrastructure/degradation/actions/l1_llm.py`
- Create: `src/spma/infrastructure/degradation/actions/l2_agent.py`
- Create: `src/spma/infrastructure/degradation/actions/l3_retrieval.py`
- Create: `src/spma/infrastructure/degradation/actions/l4_cache.py`
- Modify: `tests/test_degradation_actions.py`

- [ ] **Step 1: 追加 L1-L4 测试**

在 `tests/test_degradation_actions.py` 末尾添加：

```python

class MockLLMClient:
    """Mock LLM 客户端，用于 L1 测试。"""
    def __init__(self, healthy=True):
        self.healthy = healthy
        self.model = "claude-sonnet"
        self.ping_count = 0
        self.consecutive_pings = 0

    async def ping(self) -> bool:
        self.ping_count += 1
        if self.healthy:
            self.consecutive_pings += 1
        else:
            self.consecutive_pings = 0
        return self.healthy

    def set_model(self, model: str) -> None:
        self.model = model


class MockFeatureFlagService:
    """Mock FeatureFlag 服务，用于 L2 测试。"""
    def __init__(self, flags=None):
        self._flags = dict(flags or {})
        self.updates = []

    def is_enabled(self, name, context=None):
        return self._flags.get(name, False)

    async def update_flag(self, name, value, reason, updated_by):
        self._flags[name] = value
        self.updates.append((name, value, reason))


class MockRetrievalRouter:
    """Mock 检索路由，用于 L3 测试。"""
    def __init__(self):
        self.vector_enabled = True
        self.es_client = MockESClient()


class MockESClient:
    def __init__(self, healthy=True):
        self.healthy = healthy

    async def health_check(self):
        return self.healthy


class MockCacheService:
    """Mock 缓存服务，用于 L4 测试。"""
    def __init__(self):
        self.fallback_enabled = False
        self.cached_qa = [{"q": "test", "a": "cached answer"}]

    def enable_fallback(self):
        self.fallback_enabled = True

    def disable_fallback(self):
        self.fallback_enabled = False

    def get_cached_qa_count(self):
        return len(self.cached_qa)


class TestL1LLMDegradation:
    """L1: LLM 切换 Sonnet→Qwen3-8B。"""

    def test_level_is_l1(self):
        from spma.infrastructure.degradation.actions.l1_llm import L1LLMDegradation
        action = L1LLMDegradation(MockLLMClient())
        assert action.level == "L1"

    @pytest.mark.asyncio
    async def test_health_check_requires_consecutive_pings(self):
        from spma.infrastructure.degradation.actions.l1_llm import L1LLMDegradation
        client = MockLLMClient(healthy=True)
        action = L1LLMDegradation(client)
        # 第一次 ping 成功
        assert await action.health_check() is True
        # 但连续 ping 次数 < 3（需在 recovery_conditions_met 中体现）
        # 健康检查只看单次
        client.healthy = False
        assert await action.health_check() is False

    @pytest.mark.asyncio
    async def test_execute_switches_model(self):
        from spma.infrastructure.degradation.actions.l1_llm import L1LLMDegradation
        client = MockLLMClient()
        action = L1LLMDegradation(client)
        await action.execute("LLM timeout > 10%")
        assert client.model == "qwen3-8b-local"
        assert action.is_active is True

    @pytest.mark.asyncio
    async def test_execute_is_idempotent(self):
        from spma.infrastructure.degradation.actions.l1_llm import L1LLMDegradation
        client = MockLLMClient()
        action = L1LLMDegradation(client)
        await action.execute("first")
        await action.execute("second")
        assert client.model == "qwen3-8b-local"

    @pytest.mark.asyncio
    async def test_recover_switches_back(self):
        from spma.infrastructure.degradation.actions.l1_llm import L1LLMDegradation
        client = MockLLMClient()
        action = L1LLMDegradation(client)
        await action.execute("timeout")
        result = await action.recover()
        assert result is True
        assert client.model == "claude-sonnet"

    def test_recovery_conditions_met_requires_3_consecutive_pings(self):
        from spma.infrastructure.degradation.actions.l1_llm import L1LLMDegradation
        client = MockLLMClient(healthy=False)
        action = L1LLMDegradation(client)
        assert action.recovery_conditions_met() is False


class TestL2AgentDegradation:
    """L2: Agent→pipeline 模式。"""

    def test_level_is_l2(self):
        from spma.infrastructure.degradation.actions.l2_agent import L2AgentDegradation
        action = L2AgentDegradation(MockFeatureFlagService())
        assert action.level == "L2"

    @pytest.mark.asyncio
    async def test_execute_rolls_back_all_agents(self):
        from spma.infrastructure.degradation.actions.l2_agent import L2AgentDegradation
        ff = MockFeatureFlagService({
            "doc_agentic": True, "code_agentic": True,
            "sql_agentic": True, "supervisor_agentic": True,
            "synth_agentic": True,
        })
        action = L2AgentDegradation(ff)
        await action.execute("P99 latency spike")
        assert len(ff.updates) == 5
        for name, value, _ in ff.updates:
            assert value is False

    @pytest.mark.asyncio
    async def test_recover_restores_all(self):
        from spma.infrastructure.degradation.actions.l2_agent import L2AgentDegradation
        ff = MockFeatureFlagService({
            "doc_agentic": True, "code_agentic": False,
        })
        action = L2AgentDegradation(ff)
        await action.execute("degraded")
        await action.recover()
        # 恢复被回退的 agent
        restored = [u for u in ff.updates if u[1] is True]
        assert len(restored) >= 2


class TestL3RetrievalDegradation:
    """L3: 向量检索→纯BM25。"""

    def test_level_is_l3(self):
        from spma.infrastructure.degradation.actions.l3_retrieval import L3RetrievalDegradation
        action = L3RetrievalDegradation(MockRetrievalRouter())
        assert action.level == "L3"

    @pytest.mark.asyncio
    async def test_execute_disables_vector_search(self):
        from spma.infrastructure.degradation.actions.l3_retrieval import L3RetrievalDegradation
        router = MockRetrievalRouter()
        action = L3RetrievalDegradation(router)
        await action.execute("PGVector down")
        assert router.vector_enabled is False

    @pytest.mark.asyncio
    async def test_recover_enables_vector_search(self):
        from spma.infrastructure.degradation.actions.l3_retrieval import L3RetrievalDegradation
        router = MockRetrievalRouter()
        action = L3RetrievalDegradation(router)
        await action.execute("down")
        result = await action.recover()
        assert result is True
        assert router.vector_enabled is True


class TestL4CacheDegradation:
    """L4: Redis 缓存热点问答兜底。"""

    def test_level_is_l4(self):
        from spma.infrastructure.degradation.actions.l4_cache import L4CacheDegradation
        action = L4CacheDegradation(MockCacheService())
        assert action.level == "L4"

    @pytest.mark.asyncio
    async def test_execute_enables_fallback(self):
        from spma.infrastructure.degradation.actions.l4_cache import L4CacheDegradation
        cache = MockCacheService()
        action = L4CacheDegradation(cache)
        await action.execute("retrieval failure")
        assert cache.fallback_enabled is True

    @pytest.mark.asyncio
    async def test_requires_minimum_cached_qa(self):
        from spma.infrastructure.degradation.actions.l4_cache import L4CacheDegradation
        cache = MockCacheService()
        action = L4CacheDegradation(cache, min_cached_qa=50)
        # cache 只有 1 条，未达最低要求
        assert action._has_sufficient_cache() is False

    @pytest.mark.asyncio
    async def test_recover_disables_fallback(self):
        from spma.infrastructure.degradation.actions.l4_cache import L4CacheDegradation
        cache = MockCacheService()
        action = L4CacheDegradation(cache)
        await action.execute("failure")
        result = await action.recover()
        assert result is True
        assert cache.fallback_enabled is False
```

- [ ] **Step 2: 运行测试确认失败**

```bash
python -m pytest tests/test_degradation_actions.py -v
```

Expected: L1-L4 tests FAIL（类不存在）

- [ ] **Step 3: 实现 L1LLMDegradation**

```python
"""L1: 主 LLM 不可用 → 切换到本地 Qwen3-8B + 完备度判断降级为确定性条件。"""

import logging
from spma.infrastructure.degradation.actions.base import DegradationAction
from spma.infrastructure.degradation.events import DegradationLevel

logger = logging.getLogger(__name__)

PRIMARY_MODEL = "claude-sonnet"
FALLBACK_MODEL = "qwen3-8b-local"


class L1LLMDegradation(DegradationAction):
    """L1 LLM 降级：主模型→本地模型。"""

    level: DegradationLevel = "L1"

    def __init__(self, llm_client, required_consecutive_pings: int = 3,
                 min_recovery_interval_seconds: float = 60.0):
        self._client = llm_client
        self.is_active = False
        self._required_pings = required_consecutive_pings
        self._min_interval = min_recovery_interval_seconds
        self._consecutive_ok = 0
        self._last_check_time = 0.0

    async def health_check(self) -> bool:
        """检查主 LLM 是否可用。"""
        import time
        self._last_check_time = time.time()
        try:
            ok = await self._client.ping()
            if ok:
                self._consecutive_ok += 1
            else:
                self._consecutive_ok = 0
            return ok
        except Exception:
            self._consecutive_ok = 0
            return False

    async def execute(self, reason: str) -> None:
        if self.is_active:
            return
        logger.warning(f"L1 降级触发: {reason}，切换到 {FALLBACK_MODEL}")
        self._client.set_model(FALLBACK_MODEL)
        self.is_active = True

    async def recover(self) -> bool:
        if not self.is_active:
            return True
        logger.info(f"L1 恢复: 切换回 {PRIMARY_MODEL}")
        self._client.set_model(PRIMARY_MODEL)
        self.is_active = False
        return True

    def recovery_conditions_met(self) -> bool:
        import time
        return (
            self._consecutive_ok >= self._required_pings
            and (time.time() - self._last_check_time) >= self._min_interval
        )

    @property
    def recovery_check_interval_seconds(self) -> int:
        return 30
```

- [ ] **Step 4: 实现 L2AgentDegradation**

```python
"""L2: Agent 延迟恶化/Token成本爆炸 → 通过 Feature Flag 回退到 pipeline 模式。"""

import logging
from spma.infrastructure.degradation.actions.base import DegradationAction
from spma.infrastructure.degradation.events import DegradationLevel

logger = logging.getLogger(__name__)

AGENT_FLAGS = [
    "doc_agentic", "code_agentic", "sql_agentic",
    "supervisor_agentic", "synth_agentic",
]


class L2AgentDegradation(DegradationAction):
    """L2 Agent 降级：Agent→单轮 pipeline。"""

    level: DegradationLevel = "L2"

    def __init__(self, feature_flag_service):
        self._ff = feature_flag_service
        self.is_active = False
        self._rolled_back: set[str] = set()

    async def health_check(self) -> bool:
        """检查 Agent 指标是否正常。
        
        v1: 简化实现——如果没有任何 agent 处于回退状态，认为健康。
        v2: 接入 Prometheus 指标做精确判断。
        """
        return not self.is_active

    async def execute(self, reason: str) -> None:
        if self.is_active:
            return
        logger.warning(f"L2 降级触发: {reason}，回退所有 Agent 到 pipeline 模式")
        for flag in AGENT_FLAGS:
            if self._ff.is_enabled(flag):
                await self._ff.update_flag(flag, False, reason, "degradation_system")
                self._rolled_back.add(flag)
        self.is_active = True

    async def recover(self) -> bool:
        if not self.is_active:
            return True
        logger.info("L2 恢复: 恢复所有 Agent 的 agentic 模式")
        for flag in list(self._rolled_back):
            await self._ff.update_flag(flag, True, "auto_recovery", "degradation_system")
        self._rolled_back.clear()
        self.is_active = False
        return True

    def recovery_conditions_met(self) -> bool:
        """Agent 指标恢复正常。v1: 简化——认为总是满足（在 health_check 中验证）。"""
        return True

    @property
    def recovery_check_interval_seconds(self) -> int:
        return 60
```

- [ ] **Step 5: 实现 L3RetrievalDegradation**

```python
"""L3: 向量数据库不可用 → 切换纯 BM25 检索。"""

import logging
from spma.infrastructure.degradation.actions.base import DegradationAction
from spma.infrastructure.degradation.events import DegradationLevel

logger = logging.getLogger(__name__)


class L3RetrievalDegradation(DegradationAction):
    """L3 检索降级：向量检索→纯 BM25 关键词检索。"""

    level: DegradationLevel = "L3"

    def __init__(self, retrieval_router):
        self._router = retrieval_router
        self.is_active = False

    async def health_check(self) -> bool:
        """检查 PGVector 是否可用。"""
        try:
            # 通过检索路由检查向量存储健康状态
            return self._router.vector_enabled
        except Exception:
            return False

    async def execute(self, reason: str) -> None:
        if self.is_active:
            return
        logger.warning(f"L3 降级触发: {reason}，切换到纯 BM25 检索")
        self._router.vector_enabled = False
        self.is_active = True

    async def recover(self) -> bool:
        if not self.is_active:
            return True
        logger.info("L3 恢复: 重新启用向量检索")
        self._router.vector_enabled = True
        self.is_active = False
        return True

    def recovery_conditions_met(self) -> bool:
        """PGVector 恢复可用。"""
        try:
            return self._router.vector_enabled
        except Exception:
            return False

    @property
    def recovery_check_interval_seconds(self) -> int:
        return 30
```

- [ ] **Step 6: 实现 L4CacheDegradation**

```python
"""L4: 后端检索大面积故障 → Redis 缓存热点问答兜底。"""

import logging
from spma.infrastructure.degradation.actions.base import DegradationAction
from spma.infrastructure.degradation.events import DegradationLevel

logger = logging.getLogger(__name__)


class L4CacheDegradation(DegradationAction):
    """L4 缓存兜底：热点问答缓存作为主读取路径。"""

    level: DegradationLevel = "L4"

    def __init__(self, cache_service, min_cached_qa: int = 50):
        self._cache = cache_service
        self._min_cached_qa = min_cached_qa
        self.is_active = False

    async def health_check(self) -> bool:
        """检查后端检索是否可用。"""
        try:
            return not self.is_active
        except Exception:
            return False

    def _has_sufficient_cache(self) -> bool:
        """检查缓存是否有足够的热点问答。"""
        return self._cache.get_cached_qa_count() >= self._min_cached_qa

    async def execute(self, reason: str) -> None:
        if self.is_active:
            return
        if not self._has_sufficient_cache():
            logger.error(
                f"L4 降级缓存不足 ({self._cache.get_cached_qa_count()} < "
                f"{self._min_cached_qa})，跳过 L4 直接到 L5"
            )
            return
        logger.warning(f"L4 降级触发: {reason}，启用缓存兜底")
        self._cache.enable_fallback()
        self.is_active = True

    async def recover(self) -> bool:
        if not self.is_active:
            return True
        logger.info("L4 恢复: 恢复后端检索")
        self._cache.disable_fallback()
        self.is_active = False
        return True

    def recovery_conditions_met(self) -> bool:
        """后端检索恢复。v1: 简化实现。"""
        return True

    @property
    def recovery_check_interval_seconds(self) -> int:
        return 30
```

- [ ] **Step 7: 运行测试确认通过**

```bash
python -m pytest tests/test_degradation_actions.py -v
```

Expected: 全部 PASS

- [ ] **Step 8: Commit**

```bash
git add src/spma/infrastructure/degradation/actions/l1_llm.py \
        src/spma/infrastructure/degradation/actions/l2_agent.py \
        src/spma/infrastructure/degradation/actions/l3_retrieval.py \
        src/spma/infrastructure/degradation/actions/l4_cache.py \
        tests/test_degradation_actions.py
git commit -m "feat(degradation): implement L1-L4 degradation actions"
```

---

### Task 8: 降级触发器 + 恢复检测

**Files:**
- Create: `src/spma/infrastructure/degradation/trigger.py`
- Create: `src/spma/infrastructure/degradation/recovery.py`
- Create: `tests/test_degradation_trigger_recovery.py`

- [ ] **Step 1: 编写触发器和恢复测试**

```python
"""降级触发器 + 自动恢复测试。"""
import asyncio
import pytest
from spma.infrastructure.degradation.trigger import DegradationTrigger
from spma.infrastructure.degradation.recovery import DegradationRecovery
from spma.infrastructure.degradation.events import DegradationLevel


class MockDegradationAction:
    """Mock 降级动作，可控制 health_check 返回值。"""
    def __init__(self, level: DegradationLevel, healthy: bool = True,
                 recovery_interval: int = 30):
        self.level = level
        self._healthy = healthy
        self._executed = False
        self._recovered = False
        self.recovery_check_interval_seconds = recovery_interval
        self.health_check_call_count = 0

    def set_unhealthy(self):
        self._healthy = False

    def set_healthy(self):
        self._healthy = True

    async def health_check(self) -> bool:
        self.health_check_call_count += 1
        return self._healthy

    async def execute(self, reason: str) -> None:
        self._executed = True

    async def recover(self) -> bool:
        self._recovered = True
        return True

    def recovery_conditions_met(self) -> bool:
        return self._healthy


class TestDegradationTrigger:
    """测试降级触发器。"""

    @pytest.mark.asyncio
    async def test_trigger_calls_back_on_unhealthy(self):
        """health_check 返回 False → 触发降级回调。"""
        action = MockDegradationAction("L1", healthy=False)
        triggered = []
        async def callback(level, reason):
            triggered.append((level, reason))

        trigger = DegradationTrigger([action], callback)
        await trigger._check_once()
        assert len(triggered) == 1
        assert triggered[0][0] == "L1"

    @pytest.mark.asyncio
    async def test_trigger_skips_healthy(self):
        """health_check 返回 True → 不触发回调。"""
        action = MockDegradationAction("L1", healthy=True)
        triggered = []
        async def callback(level, reason):
            triggered.append((level, reason))

        trigger = DegradationTrigger([action], callback)
        await trigger._check_once()
        assert len(triggered) == 0

    @pytest.mark.asyncio
    async def test_handle_webhook_parses_alert(self):
        """webhook 解析 Prometheus alert 格式。"""
        action = MockDegradationAction("L1", healthy=False)
        triggered = []
        async def callback(level, reason):
            triggered.append((level, reason))

        trigger = DegradationTrigger([action], callback)
        alert = {
            "alerts": [{
                "labels": {"degradation_level": "L1", "severity": "critical"},
                "annotations": {"summary": "LLM timeout rate > 10%"},
            }]
        }
        await trigger.handle_webhook(alert)
        assert len(triggered) >= 1

    @pytest.mark.asyncio
    async def test_start_stop_loop(self):
        """启动和停止轮询循环。"""
        action = MockDegradationAction("L1", healthy=True)
        triggered = []
        async def callback(level, reason):
            triggered.append((level, reason))

        trigger = DegradationTrigger([action], callback)
        task = asyncio.create_task(trigger.run_loop())
        await asyncio.sleep(0.1)
        await trigger.stop()
        await task
        # 不应该有触发（都健康）
        assert len(triggered) == 0


class TestDegradationRecovery:
    """测试自动恢复检测。"""

    @pytest.mark.asyncio
    async def test_recovery_calls_back_when_conditions_met(self):
        """恢复条件满足 → 触发恢复回调。"""
        action = MockDegradationAction("L1", healthy=True)
        recovered = []
        async def callback(from_level, to_level, reason):
            recovered.append((from_level, to_level))

        recovery = DegradationRecovery([action], callback)
        await recovery._check_once(current_level="L1")
        assert len(recovered) == 1
        assert recovered[0][0] == "L1"  # from L1
        assert recovered[0][1] in ("L0", None)  # to L0 or determined by manager

    @pytest.mark.asyncio
    async def test_skip_if_not_active(self):
        """当前状态是 L0（未降级）时不触发恢复。"""
        action = MockDegradationAction("L1", healthy=True)
        recovered = []
        async def callback(from_level, to_level, reason):
            recovered.append((from_level, to_level))

        recovery = DegradationRecovery([action], callback)
        await recovery._check_once(current_level="L0")
        assert len(recovered) == 0
```

- [ ] **Step 2: 运行测试确认失败**

```bash
python -m pytest tests/test_degradation_trigger_recovery.py -v
```

Expected: FAIL

- [ ] **Step 3: 实现 DegradationTrigger**

```python
"""降级触发器——双入口：内建健康检查循环 + Prometheus webhook 预留。"""

import asyncio
import logging
from typing import Awaitable, Callable
from spma.infrastructure.degradation.actions.base import DegradationAction
from spma.infrastructure.degradation.events import DegradationLevel

logger = logging.getLogger(__name__)


class DegradationTrigger:
    """降级触发器——内建轮询 + Prometheus webhook。"""

    def __init__(
        self,
        actions: list[DegradationAction],
        on_degrade: Callable[[DegradationLevel, str], Awaitable[None]],
        poll_interval: float = 30.0,
    ):
        self._actions = {a.level: a for a in actions}
        self._on_degrade = on_degrade
        self._poll_interval = poll_interval
        self._running = False
        self._task: asyncio.Task | None = None

    async def run_loop(self) -> None:
        """入口1：内建健康检查循环。每 poll_interval 秒轮询所有 action。"""
        self._running = True
        logger.info(
            f"降级触发器启动，间隔={self._poll_interval}s，"
            f"监控级别={list(self._actions.keys())}"
        )
        while self._running:
            try:
                await self._check_once()
            except Exception:
                logger.exception("降级触发器轮询异常")
            await asyncio.sleep(self._poll_interval)

    async def _check_once(self) -> None:
        """执行一次全量健康检查。"""
        for level, action in self._actions.items():
            if level == "L0":
                continue
            is_healthy = await action.health_check()
            if not is_healthy:
                logger.warning(
                    f"级别 {level} 健康检查失败，触发降级"
                )
                await self._on_degrade(level, f"健康检查失败: {level}")

    async def handle_webhook(self, alert: dict) -> None:
        """入口2：Prometheus AlertManager webhook（预留）。

        接收格式兼容 AlertManager webhook v4:
        {
          "alerts": [
            {
              "labels": {"degradation_level": "L1", ...},
              "annotations": {"summary": "..."}
            }
          ]
        }
        """
        alerts = alert.get("alerts", [])
        for a in alerts:
            labels = a.get("labels", {})
            level = labels.get("degradation_level")
            if level and level in self._actions:
                summary = a.get("annotations", {}).get("summary", "Prometheus alert")
                await self._on_degrade(level, summary)

    async def stop(self) -> None:
        """停止轮询循环。"""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("降级触发器已停止")
```

- [ ] **Step 4: 实现 DegradationRecovery**

```python
"""自动恢复检测——定时检查恢复条件 + 逐级恢复。"""

import asyncio
import logging
from typing import Awaitable, Callable
from spma.infrastructure.degradation.actions.base import DegradationAction
from spma.infrastructure.degradation.events import DegradationLevel

logger = logging.getLogger(__name__)


class DegradationRecovery:
    """自动恢复检测——检查恢复条件，逐级恢复。"""

    def __init__(
        self,
        actions: list[DegradationAction],
        on_recover: Callable[[DegradationLevel, DegradationLevel, str], Awaitable[None]],
    ):
        self._actions = {a.level: a for a in actions}
        self._on_recover = on_recover
        self._running = False
        self._task: asyncio.Task | None = None

    async def run_loop(self, get_current_level) -> None:
        """定期检查恢复条件。

        Args:
            get_current_level: 返回当前降级级别的回调函数。
        """
        self._running = True
        logger.info("自动恢复检测启动")
        while self._running:
            try:
                current = get_current_level()
                await self._check_once(current)
            except Exception:
                logger.exception("自动恢复检测异常")
            # 用当前级别的恢复检查间隔
            current = get_current_level()
            action = self._actions.get(current)
            interval = action.recovery_check_interval_seconds if action else 30
            await asyncio.sleep(interval)

    async def _check_once(self, current_level: DegradationLevel) -> None:
        """检查当前级别是否可以恢复到上一级。"""
        if current_level == "L0":
            return

        action = self._actions.get(current_level)
        if action and action.recovery_conditions_met():
            # 确定恢复目标级别（向上一级）
            levels = ["L0", "L1", "L2", "L3", "L4", "L5"]
            idx = levels.index(current_level)
            target = levels[idx - 1] if idx > 0 else "L0"
            logger.info(f"恢复条件满足: {current_level} → {target}")
            await self._on_recover(current_level, target, "自动恢复条件满足")

    async def stop(self) -> None:
        """停止恢复检测循环。"""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("自动恢复检测已停止")
```

- [ ] **Step 5: 运行测试确认通过**

```bash
python -m pytest tests/test_degradation_trigger_recovery.py -v
```

Expected: 全部 PASS

- [ ] **Step 6: Commit**

```bash
git add src/spma/infrastructure/degradation/trigger.py \
        src/spma/infrastructure/degradation/recovery.py \
        tests/test_degradation_trigger_recovery.py
git commit -m "feat(degradation): implement trigger (health check loop + webhook) and recovery"
```

---

### Task 9: DegradationManager 编排层

**Files:**
- Create: `src/spma/infrastructure/degradation/manager.py`
- Create: `tests/test_degradation_manager.py`
- Modify: `src/spma/infrastructure/degradation/__init__.py`

- [ ] **Step 1: 编写 DegradationManager 测试**

```python
"""DegradationManager 编排层测试。"""
import pytest
from spma.infrastructure.degradation.manager import DegradationManager
from spma.infrastructure.degradation.events import DegradationLevel


class MockDegradationAction:
    def __init__(self, level: DegradationLevel, healthy=True,
                 recovery_interval=30):
        self.level = level
        self._healthy = healthy
        self.execute_calls = []
        self.recover_calls = []
        self.health_check_calls = 0
        self.recovery_check_interval_seconds = recovery_interval

    async def health_check(self) -> bool:
        self.health_check_calls += 1
        return self._healthy

    async def execute(self, reason: str) -> None:
        self.execute_calls.append(reason)

    async def recover(self) -> bool:
        self.recover_calls.append(True)
        return True

    def recovery_conditions_met(self) -> bool:
        return self._healthy

    def set_unhealthy(self):
        self._healthy = False

    def set_healthy(self):
        self._healthy = True


@pytest.fixture
def l1_action():
    return MockDegradationAction("L1")

@pytest.fixture
def l2_action():
    return MockDegradationAction("L2")

@pytest.fixture
def l3_action():
    return MockDegradationAction("L3")

@pytest.fixture
def l4_action():
    return MockDegradationAction("L4")

@pytest.fixture
def l5_action():
    return MockDegradationAction("L5")

@pytest.fixture
def manager(l1_action, l2_action, l3_action, l4_action, l5_action):
    return DegradationManager([
        l1_action, l2_action, l3_action, l4_action, l5_action,
    ])


class TestDegradationManager:
    """测试 DegradationManager 核心功能。"""

    def test_initial_level_is_l0(self, manager):
        assert manager.current_level == "L0"

    @pytest.mark.asyncio
    async def test_manual_degrade_skips_levels(self, manager, l1_action, l3_action):
        """手动降级支持跨级（L0→L3）。"""
        await manager.manual_degrade("L3", "test skip", "admin")
        assert manager.current_level == "L3"
        # L3 降级时叠加 L1+L2+L3 的动作
        assert len(l1_action.execute_calls) == 1
        assert len(l2_action.execute_calls) == 1
        assert len(l3_action.execute_calls) == 1

    @pytest.mark.asyncio
    async def test_manual_degrade_to_l0_is_noop(self, manager, l1_action):
        """降级到 L0 不执行任何动作。"""
        await manager.manual_degrade("L0", "recover test", "admin")
        assert manager.current_level == "L0"
        assert len(l1_action.execute_calls) == 0

    @pytest.mark.asyncio
    async def test_manual_recover_full(self, manager, l1_action):
        """手动恢复逐级恢复到 L0。"""
        await manager.manual_degrade("L3", "degrade", "admin")
        assert manager.current_level == "L3"
        await manager.manual_recover()
        assert manager.current_level == "L0"
        assert len(l1_action.recover_calls) == 1
        assert len(l3_action.recover_calls) == 1

    @pytest.mark.asyncio
    async def test_manual_degrade_records_history(self, manager):
        """降级事件记录到历史。"""
        await manager.manual_degrade("L1", "test reason", "admin")
        history = manager.get_history()
        assert len(history) >= 1
        assert history[0].level == "L1"
        assert history[0].triggered_by == "manual"
        assert history[0].operator == "admin"

    def test_get_status(self, manager):
        """get_status 返回完整状态信息。"""
        status = manager.get_status()
        assert status["current_level"] == "L0"
        assert "degraded_components" in status
        assert "auto_recovery_enabled" in status

    def test_get_history_returns_limited(self, manager):
        """get_history(limit) 截断返回。"""
        history = manager.get_history(limit=10)
        assert len(history) <= 10

    @pytest.mark.asyncio
    async def test_degrade_emits_event(self, manager):
        """降级触发事件（通过回调）。"""
        events = []
        manager.on_event = lambda e: events.append(e)
        await manager.manual_degrade("L2", "event test", "admin")
        assert len(events) >= 1
```

- [ ] **Step 2: 运行测试确认失败**

```bash
python -m pytest tests/test_degradation_manager.py -v
```

Expected: FAIL

- [ ] **Step 3: 实现 DegradationManager**

```python
"""降级管理器——状态机编排层。

管理 L0↔L5 六级别切换，协调 actions/trigger/recovery。
支持手动降级（可跨级）、自动降级（逐级）、自动恢复（逐级）。

设计依据: Phase 4 hardening design spec §3
"""

import asyncio
import logging
from typing import Callable, Awaitable
from spma.infrastructure.degradation.events import (
    DegradationLevel,
    DegradationEvent,
    RecoveryEvent,
)
from spma.infrastructure.degradation.actions.base import DegradationAction
from spma.infrastructure.degradation.trigger import DegradationTrigger
from spma.infrastructure.degradation.recovery import DegradationRecovery

logger = logging.getLogger(__name__)

LEVEL_ORDER: list[DegradationLevel] = ["L0", "L1", "L2", "L3", "L4", "L5"]


class DegradationManager:
    """降级状态机：管理 L0↔L5 切换，协调 actions、trigger、recovery。"""

    def __init__(
        self,
        actions: list[DegradationAction],
        auto_recovery_enabled: bool = True,
    ):
        self._state: DegradationLevel = "L0"
        self._actions: dict[DegradationLevel, DegradationAction] = {
            a.level: a for a in actions
        }
        self._history: list[DegradationEvent | RecoveryEvent] = []
        self._auto_recovery_enabled = auto_recovery_enabled
        self._running = False

        self._trigger = DegradationTrigger(actions, self._handle_auto_degrade)
        self._recovery = DegradationRecovery(actions, self._handle_auto_recover)

        # 事件回调（供外部注册，如 AuditLogger）
        self.on_event: Callable | None = None

    @property
    def current_level(self) -> DegradationLevel:
        return self._state

    @property
    def auto_recovery_enabled(self) -> bool:
        return self._auto_recovery_enabled

    async def start(self) -> None:
        """启动后台检查循环（trigger + recovery）。"""
        if self._running:
            return
        self._running = True
        logger.info("DegradationManager 启动")
        self._trigger._task = asyncio.create_task(self._trigger.run_loop())
        self._recovery._task = asyncio.create_task(
            self._recovery.run_loop(lambda: self._state)
        )

    async def stop(self) -> None:
        """优雅停止。"""
        self._running = False
        await self._trigger.stop()
        await self._recovery.stop()
        logger.info("DegradationManager 停止")

    async def manual_degrade(
        self, level: DegradationLevel, reason: str, operator: str = "admin"
    ) -> None:
        """手动触发降级，支持跨级。"""
        if level == "L0":
            await self.manual_recover()
            return

        logger.warning(f"手动降级: {self._state} → {level} ({reason}, by {operator})")
        await self._execute_actions_up_to(level, reason)

        event = DegradationEvent(
            event_type="degradation.manual",
            level=level,
            reason=reason,
            previous_level=self._state,
            triggered_by="manual",
            operator=operator,
        )
        self._state = level
        self._history.append(event)
        self._emit(event)

    async def manual_recover(self) -> None:
        """手动恢复——逐级恢复到 L0。"""
        logger.info(f"手动恢复: {self._state} → L0")
        await self._recover_all()
        event = DegradationEvent(
            event_type="degradation.manual",
            level="L0",
            reason="手动恢复",
            previous_level=self._state,
            triggered_by="manual",
            operator="admin",
        )
        self._state = "L0"
        self._history.append(event)
        self._emit(event)

    def get_status(self) -> dict:
        """返回当前降级状态（管理 API 数据源）。"""
        active = []
        current_idx = LEVEL_ORDER.index(self._state)
        for level in LEVEL_ORDER[1:current_idx + 1]:
            active.append({"level": level, "trigger": "见 history"})

        return {
            "current_level": self._state,
            "degraded_components": [
                f"level_{level}" for level in LEVEL_ORDER[1:current_idx + 1]
            ],
            "active_degradations": active,
            "last_degradation_at": self._last_event_time("degradation"),
            "last_recovery_at": self._last_event_time("recovery"),
            "auto_recovery_enabled": self._auto_recovery_enabled,
        }

    def get_history(self, limit: int = 50) -> list:
        """返回最近的降级/恢复事件。"""
        return self._history[-limit:]

    async def _handle_auto_degrade(self, level: DegradationLevel, reason: str) -> None:
        """自动降级回调（从 trigger 调用）。"""
        if LEVEL_ORDER.index(level) <= LEVEL_ORDER.index(self._state):
            return  # 当前已在同级或更高级别

        logger.warning(f"自动降级: {self._state} → {level} ({reason})")
        await self._execute_actions_up_to(level, reason)

        event = DegradationEvent(
            event_type="degradation.triggered",
            level=level,
            reason=reason,
            previous_level=self._state,
            triggered_by="auto",
        )
        self._state = level
        self._history.append(event)
        self._emit(event)

    async def _handle_auto_recover(
        self, from_level: DegradationLevel, to_level: DegradationLevel, reason: str
    ) -> None:
        """自动恢复回调（从 recovery 调用）。"""
        action = self._actions.get(from_level)
        if action:
            success = await action.recover()
            if not success:
                logger.warning(f"恢复 {from_level} 失败")
                return

        event = RecoveryEvent(
            from_level=from_level,
            to_level=to_level,
            reason=reason,
        )
        self._state = to_level
        self._history.append(event)
        self._emit(event)
        logger.info(f"自动恢复: {from_level} → {to_level}")

    async def _execute_actions_up_to(
        self, target_level: DegradationLevel, reason: str
    ) -> None:
        """执行从当前级别到目标级别之间的所有降级动作（叠加）。"""
        target_idx = LEVEL_ORDER.index(target_level)
        for i in range(1, target_idx + 1):
            level = LEVEL_ORDER[i]
            action = self._actions.get(level)
            if action:
                await action.execute(reason)

    async def _recover_all(self) -> None:
        """执行所有活跃级别的恢复动作（从高到低）。"""
        current_idx = LEVEL_ORDER.index(self._state)
        for i in range(current_idx, 0, -1):
            level = LEVEL_ORDER[i]
            action = self._actions.get(level)
            if action:
                await action.recover()

    def _emit(self, event) -> None:
        """发送事件到外部监听器。"""
        if self.on_event:
            try:
                self.on_event(event)
            except Exception:
                logger.exception("事件回调失败")

    def _last_event_time(self, event_type_hint: str) -> float | None:
        """获取指定类型最近事件的时间。"""
        for event in reversed(self._history):
            if hasattr(event, "event_type"):
                if event_type_hint == "degradation":
                    if "triggered" in event.event_type or "manual" in event.event_type:
                        if event.level != "L0":
                            return event.timestamp
                    if event.event_type == "degradation.manual" and event.level == "L0":
                        return event.timestamp
                elif event_type_hint == "recovery":
                    if isinstance(event, RecoveryEvent):
                        return event.timestamp
        return None
```

- [ ] **Step 4: 运行测试确认通过**

```bash
python -m pytest tests/test_degradation_manager.py -v
```

Expected: 全部 PASS

- [ ] **Step 5: 更新 degradation/__init__.py 导出**

```python
"""六级降级管理体系。

导出: DegradationManager, DegradationLevel, DegradationEvent, RecoveryEvent
"""

from spma.infrastructure.degradation.events import (
    DegradationEvent,
    DegradationLevel,
    RecoveryEvent,
)
from spma.infrastructure.degradation.manager import DegradationManager

__all__ = [
    "DegradationManager",
    "DegradationLevel",
    "DegradationEvent",
    "RecoveryEvent",
]
```

- [ ] **Step 6: Commit**

```bash
git add src/spma/infrastructure/degradation/manager.py \
        src/spma/infrastructure/degradation/__init__.py \
        tests/test_degradation_manager.py
git commit -m "feat(degradation): implement DegradationManager orchestrator"
```

---

### Task 10: 热点问答缓存实现（L4 依赖）

**Files:**
- Modify: `src/spma/infrastructure/cache.py`

- [ ] **Step 1: 实现 CacheService**

将 `src/spma/infrastructure/cache.py` 替换为：

```python
"""Redis 缓存——热点问答(TTL=1h) + 查询结果(TTL=5min) + LLM翻译(TTL=24h)。

L4 降级时，热点问答缓存作为兜底读取路径。

设计依据: API-06 §2 缓存契约
"""

import json
import hashlib
import logging
from typing import Any

logger = logging.getLogger(__name__)


class CacheService:
    """Redis 缓存服务。

    L4 降级时 fallback_enabled=True，主读取路径切换为缓存。
    """

    def __init__(self, redis_client=None):
        self._redis = redis_client
        self.fallback_enabled = False
        self._cached_qa: list[dict] = []  # 进程内副本（Redis 不可用时兜底）

    # === 热点问答缓存 ===

    async def cache_qa(self, question: str, answer: str, ttl: int = 3600) -> None:
        """缓存热点问答。"""
        key = self._qa_key(question)
        value = json.dumps({"q": question, "a": answer}, ensure_ascii=False)
        if self._redis:
            try:
                await self._redis.setex(key, ttl, value.encode("utf-8"))
            except Exception:
                logger.warning("Redis QA 缓存写入失败")
        # 进程内副本
        self._cached_qa.append({"q": question, "a": answer})

    async def get_cached_qa(self, question: str) -> dict | None:
        """查询缓存的问答。"""
        key = self._qa_key(question)
        if self._redis:
            try:
                raw = await self._redis.get(key)
                if raw:
                    return json.loads(raw)
            except Exception:
                pass
        # 进程内查找
        for qa in self._cached_qa:
            if qa["q"] == question:
                return qa
        return None

    async def get_all_cached_qa(self) -> list[dict]:
        """获取所有缓存的问答（L4 兜底用）。"""
        if self._redis:
            try:
                keys = await self._redis.keys("cache:qa:*")
                if keys:
                    values = await self._redis.mget(keys)
                    results = []
                    for v in values:
                        if v:
                            try:
                                results.append(json.loads(v))
                            except json.JSONDecodeError:
                                pass
                    if results:
                        return results
            except Exception:
                logger.warning("Redis QA 全量读取失败")
        return list(self._cached_qa)

    def get_cached_qa_count(self) -> int:
        """缓存问答数量（L4 前置检查用）。"""
        return len(self._cached_qa)

    # === 降级兜底 ===

    def enable_fallback(self) -> None:
        """启用缓存兜底（L4 触发）。"""
        self.fallback_enabled = True
        logger.warning("缓存兜底启用")

    def disable_fallback(self) -> None:
        """禁用缓存兜底（L4 恢复）。"""
        self.fallback_enabled = False
        logger.info("缓存兜底关闭")

    def is_fallback_active(self) -> bool:
        return self.fallback_enabled

    # === 查询结果缓存 ===

    async def cache_result(self, query_id: str, result: dict,
                          ttl: int = 300) -> None:
        """缓存查询结果。"""
        key = f"cache:result:{query_id}"
        if self._redis:
            try:
                await self._redis.setex(
                    key, ttl, json.dumps(result, ensure_ascii=False).encode("utf-8")
                )
            except Exception:
                pass

    async def get_cached_result(self, query_id: str) -> dict | None:
        """获取缓存的查询结果。"""
        key = f"cache:result:{query_id}"
        if self._redis:
            try:
                raw = await self._redis.get(key)
                if raw:
                    return json.loads(raw)
            except Exception:
                pass
        return None

    # === LLM 翻译缓存 ===

    async def cache_translation(self, zh_term: str, en_term: str,
                               ttl: int = 86400) -> None:
        """缓存中英文翻译。"""
        key = f"cache:llm_trans:{zh_term}"
        if self._redis:
            try:
                await self._redis.setex(key, ttl, en_term.encode("utf-8"))
            except Exception:
                pass

    # === helpers ===

    @staticmethod
    def _qa_key(question: str) -> str:
        h = hashlib.md5(question.encode()).hexdigest()[:12]
        return f"cache:qa:{h}"


# 全局单例
_cache_service: CacheService | None = None


def get_cache_service() -> CacheService:
    global _cache_service
    if _cache_service is None:
        _cache_service = CacheService()
    return _cache_service
```

- [ ] **Step 2: 验证 import**

```bash
python -c "from spma.infrastructure.cache import CacheService; cs = CacheService(); print(cs.get_cached_qa_count())"
```

Expected: `0`

- [ ] **Step 3: Commit**

```bash
git add src/spma/infrastructure/cache.py
git commit -m "feat(cache): implement CacheService with hot QA cache and L4 fallback support"
```

---

### Task 11: 管理 API 端点

**Files:**
- Modify: `src/spma/api/app.py`
- Modify: `src/spma/api/dependencies.py`

- [ ] **Step 1: 扩展 dependencies.py** — 提供全局实例访问

```python
"""FastAPI 依赖注入。

通过 Depends() 注入: 降级管理器、熔断器注册表、Feature Flag 服务、缓存等。
"""

from spma.infrastructure.degradation import DegradationManager, DegradationLevel
from spma.infrastructure.circuit_breaker import get_all_stats, get_circuit_breaker
from spma.infrastructure.feature_flags import FeatureFlagService
from spma.infrastructure.cache import get_cache_service

# 全局实例（app 启动时初始化）
_degradation_manager: DegradationManager | None = None
_feature_flag_service: FeatureFlagService | None = None


def get_degradation_manager() -> DegradationManager:
    global _degradation_manager
    if _degradation_manager is None:
        raise RuntimeError("DegradationManager not initialized")
    return _degradation_manager


def get_feature_flag_service() -> FeatureFlagService:
    global _feature_flag_service
    if _feature_flag_service is None:
        raise RuntimeError("FeatureFlagService not initialized")
    return _feature_flag_service


def set_degradation_manager(manager: DegradationManager) -> None:
    global _degradation_manager
    _degradation_manager = manager


def set_feature_flag_service(service: FeatureFlagService) -> None:
    global _feature_flag_service
    _feature_flag_service = service
```

- [ ] **Step 2: 扩展 app.py** — 添加管理 API 端点

```python
"""FastAPI 应用工厂。

create_app() → 注册所有路由、中间件、生命周期事件。

设计依据: API-01 端点总览 + Phase 4 hardening design spec §6.3
"""

from fastapi import FastAPI, HTTPException, Depends, Query
from pydantic import BaseModel

from spma.infrastructure.degradation import DegradationManager
from spma.infrastructure.circuit_breaker import get_all_stats, get_circuit_breaker
from spma.api.dependencies import get_degradation_manager


# --- Request/Response Models ---

class DegradationTriggerRequest(BaseModel):
    level: str  # L0-L5
    reason: str = "manual trigger"
    operator: str = "admin"


class DegradationStatusResponse(BaseModel):
    current_level: str
    degraded_components: list[str]
    active_degradations: list[dict]
    last_degradation_at: float | None
    last_recovery_at: float | None
    auto_recovery_enabled: bool


# --- Route Handlers ---

async def get_degradation_status(
    manager: DegradationManager = Depends(get_degradation_manager),
):
    """GET /api/v1/admin/degradation/status — 当前降级状态。"""
    return manager.get_status()


async def trigger_degradation(
    body: DegradationTriggerRequest,
    manager: DegradationManager = Depends(get_degradation_manager),
):
    """POST /api/v1/admin/degradation/trigger — 手动触发降级。"""
    valid_levels = ["L0", "L1", "L2", "L3", "L4", "L5"]
    if body.level not in valid_levels:
        raise HTTPException(400, f"Invalid level: {body.level}. Must be one of {valid_levels}")
    await manager.manual_degrade(body.level, body.reason, body.operator)
    return {"status": "ok", "current_level": manager.current_level}


async def recover_degradation(
    manager: DegradationManager = Depends(get_degradation_manager),
):
    """POST /api/v1/admin/degradation/recover — 手动恢复。"""
    await manager.manual_recover()
    return {"status": "ok", "current_level": manager.current_level}


async def get_degradation_history(
    limit: int = Query(50, ge=1, le=200),
    manager: DegradationManager = Depends(get_degradation_manager),
):
    """GET /api/v1/admin/degradation/history — 降级历史。"""
    history = manager.get_history(limit=limit)
    return [h.__dict__ if hasattr(h, '__dict__') else h for h in history]


async def list_circuit_breakers():
    """GET /api/v1/admin/circuit-breakers — 所有熔断器状态。"""
    stats = get_all_stats()
    return [
        {
            "name": s.name,
            "state": s.state.value,
            "failure_count": s.failure_count,
            "total_failures": s.total_failures,
            "total_successes": s.total_successes,
            "opened_at": s.opened_at,
        }
        for s in stats
    ]


async def reset_circuit_breaker(name: str):
    """POST /api/v1/admin/circuit-breakers/{name}/reset — 手动重置熔断器。"""
    cb = get_circuit_breaker(name)
    if cb is None:
        raise HTTPException(404, f"Circuit breaker '{name}' not found")
    await cb.reset()
    return {"status": "ok", "name": name, "state": cb.state.value}


def create_app() -> FastAPI:
    """创建并配置 FastAPI 应用实例。"""
    app = FastAPI(
        title="SPMA",
        version="0.2.0",
        description="企业级多源RAG智能问答系统",
    )

    # 健康检查
    @app.get("/health")
    async def health_check():
        return {"status": "ok", "version": "0.2.0"}

    # 管理 API — 降级
    app.add_api_route(
        "/api/v1/admin/degradation/status",
        get_degradation_status, methods=["GET"],
    )
    app.add_api_route(
        "/api/v1/admin/degradation/trigger",
        trigger_degradation, methods=["POST"],
    )
    app.add_api_route(
        "/api/v1/admin/degradation/recover",
        recover_degradation, methods=["POST"],
    )
    app.add_api_route(
        "/api/v1/admin/degradation/history",
        get_degradation_history, methods=["GET"],
    )

    # 管理 API — 熔断器
    app.add_api_route(
        "/api/v1/admin/circuit-breakers",
        list_circuit_breakers, methods=["GET"],
    )
    app.add_api_route(
        "/api/v1/admin/circuit-breakers/{name}/reset",
        reset_circuit_breaker, methods=["POST"],
    )

    return app


def main():
    """uvicorn 入口: uv run spma-api"""
    import uvicorn
    uvicorn.run("spma.api.app:create_app", host="0.0.0.0", port=8000, factory=True)
```

- [ ] **Step 3: 验证 app 启动**

```bash
python -c "from spma.api.app import create_app; app = create_app(); print(len(app.routes))"
```

Expected: 路由数量 >= 7（/health + 6 个管理端点）

- [ ] **Step 4: Commit**

```bash
git add src/spma/api/app.py src/spma/api/dependencies.py
git commit -m "feat(api): add degradation and circuit breaker management endpoints"
```

---

### Task 12: 现有代码集成 — 添加 @circuit_breaker 装饰器

**Files:**
- Modify: `src/spma/llm/clients.py`
- Modify: `src/spma/retrieval/vector_store.py`
- Modify: `src/spma/retrieval/es_client.py`

- [ ] **Step 1: 在 llm/clients.py 中添加熔断器装饰**

`src/spma/llm/clients.py` 当前是空骨架。在实现 LLM 客户端时，核心调用方法需要包裹熔断器：

```python
"""LLM 客户端——Haiku/Sonnet API + Qwen3-8B vLLM 本地。

统一接口: chat(messages, model, **kwargs) → str
动态模型选择: 运行时按 state 自动切换 Haiku/Sonnet
指数退避重试: tenacity, 429→重试3次, multiplier=0.5s, max_wait=2s
降级: 非 429 错误直接降级到 Qwen3-8B
"""

from spma.infrastructure.circuit_breaker import circuit_breaker

# 核心 LLM 调用方法将由后续开发完善，此处确保熔断器集成点就绪
```

- [ ] **Step 2: 在 ES client 的 search 方法上添加熔断器**

修改 `src/spma/retrieval/es_client.py` 的 `ESClient.search()`：

```python
from spma.infrastructure.circuit_breaker import circuit_breaker

class ESClient:
    # ... 保留现有 __init__, index_chunks, delete_by_source, get_chunks
    # ... create_index, delete_index, health_check, close 不变

    @circuit_breaker("elasticsearch")
    async def search(self, query, top_k=20, filters=None):
        # ... 保留现有实现不变
```

- [ ] **Step 3: 在 vector_store.py 的检索方法上添加熔断器**

`src/spma/retrieval/vector_store.py` 当前是骨架。在向量检索方法上添加装饰器：

```python
"""PGVector 向量存储客户端。"""
from spma.infrastructure.circuit_breaker import circuit_breaker

# 向量检索方法由后续开发完善，添加熔断器保护
@circuit_breaker("pgvector")
async def vector_search(self, query_vector, top_k=20, filters=None):
    ...
```

- [ ] **Step 4: 验证所有 import 可用**

```bash
python -c "
from spma.infrastructure.circuit_breaker import circuit_breaker, get_all_stats
from spma.retrieval.es_client import ESClient
print('All imports OK')
"
```

Expected: `All imports OK`

- [ ] **Step 5: Commit**

```bash
git add src/spma/llm/clients.py src/spma/retrieval/vector_store.py src/spma/retrieval/es_client.py
git commit -m "feat(integration): add @circuit_breaker decorators to LLM, ES, and vector store"
```

---

### Task 13: 应用启动引导 + 端到端验证

**Files:**
- Create: `src/spma/bootstrap.py`

- [ ] **Step 1: 编写引导模块 — 组装所有组件**

```python
"""应用启动引导——初始化降级系统、熔断器、Feature Flags、审计日志。

在 FastAPI app startup 事件中调用 init_infrastructure()。
"""

import logging
from spma.infrastructure.degradation.manager import DegradationManager
from spma.infrastructure.degradation.actions.l1_llm import L1LLMDegradation
from spma.infrastructure.degradation.actions.l2_agent import L2AgentDegradation
from spma.infrastructure.degradation.actions.l3_retrieval import L3RetrievalDegradation
from spma.infrastructure.degradation.actions.l4_cache import L4CacheDegradation
from spma.infrastructure.degradation.actions.l5_static import L5StaticFallback
from spma.infrastructure.feature_flags import FeatureFlagService
from spma.infrastructure.cache import CacheService, get_cache_service
from spma.infrastructure.audit import AuditLogger, get_audit_logger
from spma.infrastructure.metrics import degradation_metrics
from spma.api.dependencies import (
    set_degradation_manager,
    set_feature_flag_service,
)

logger = logging.getLogger(__name__)


async def init_infrastructure(
    llm_client=None,
    retrieval_router=None,
    redis_client=None,
    db_pool=None,
) -> DegradationManager:
    """初始化基础设施层：降级系统 + Feature Flags + 缓存 + 审计。

    返回 DegradationManager 供 app 生命周期管理。
    """

    # 1. Feature Flag 服务
    ff_service = FeatureFlagService.from_yaml("config/feature_flags.yaml")
    set_feature_flag_service(ff_service)

    # 2. 缓存服务
    cache_service = CacheService(redis_client=redis_client)

    # 3. 审计日志
    audit_logger = AuditLogger(db_pool=db_pool)
    await audit_logger.start()

    # 4. 降级动作
    l1 = L1LLMDegradation(llm_client) if llm_client else None
    l2 = L2AgentDegradation(ff_service)
    l3 = L3RetrievalDegradation(retrieval_router) if retrieval_router else None
    l4 = L4CacheDegradation(cache_service, min_cached_qa=50)
    l5 = L5StaticFallback()

    actions = [a for a in [l1, l2, l3, l4, l5] if a is not None]

    # 5. 降级管理器
    manager = DegradationManager(actions, auto_recovery_enabled=True)

    # 6. 事件 → 审计日志
    async def on_degradation_event(event):
        from spma.infrastructure.audit import AuditEvent
        await audit_logger.log(AuditEvent(
            event_type=event.event_type if hasattr(event, 'event_type') else "degradation.recovered",
            level=event.level if hasattr(event, 'level') else None,
            details={
                "reason": getattr(event, "reason", ""),
                "previous_level": getattr(event, "previous_level", None),
                "triggered_by": getattr(event, "triggered_by", "auto"),
                "operator": getattr(event, "operator", None),
            },
        ))

    manager.on_event = on_degradation_event
    set_degradation_manager(manager)

    await manager.start()
    logger.info("基础设施层初始化完成")
    return manager


async def shutdown_infrastructure(manager: DegradationManager) -> None:
    """优雅关闭基础设施。"""
    await manager.stop()
    audit = get_audit_logger()
    await audit.stop()
    logger.info("基础设施层已关闭")
```

- [ ] **Step 2: 验证组件可导入**

```bash
python -c "
from spma.infrastructure.degradation.manager import DegradationManager
from spma.infrastructure.degradation.actions.l1_llm import L1LLMDegradation
from spma.infrastructure.degradation.actions.l2_agent import L2AgentDegradation
from spma.infrastructure.degradation.actions.l3_retrieval import L3RetrievalDegradation
from spma.infrastructure.degradation.actions.l4_cache import L4CacheDegradation
from spma.infrastructure.degradation.actions.l5_static import L5StaticFallback
from spma.infrastructure.feature_flags import FeatureFlagService
from spma.infrastructure.cache import CacheService
from spma.infrastructure.audit import AuditLogger
print('All imports OK')
"
```

Expected: `All imports OK`

- [ ] **Step 3: 运行全部新增测试**

```bash
python -m pytest tests/test_circuit_breaker.py tests/test_feature_flags.py tests/test_audit.py tests/test_degradation_actions.py tests/test_degradation_trigger_recovery.py tests/test_degradation_manager.py -v
```

Expected: 全部 PASS（约 35-40 个测试）

- [ ] **Step 4: Commit**

```bash
git add src/spma/bootstrap.py
git commit -m "feat(bootstrap): add infrastructure initialization and shutdown lifecycle"
```

---

## 验收检查清单

实施完成后逐项验证：

### 降级体系
- [ ] L1-L5 降级在触发条件满足后 < 30s 自动执行
- [ ] 自动恢复条件满足后 < 60s 自动恢复
- [ ] 降级/恢复事件完整记录到审计日志（stdout + PG）
- [ ] 降级管理 API 正常工作（status/trigger/recover/history）
- [ ] L4 缓存兜底至少有 50 条热点问答
- [ ] 手动降级支持跨级（L0→L3 直接跳）
- [ ] 高级别降级时保留低级别降级动作（叠加执行）

### 熔断器
- [ ] 连续 5 次失败 → OPEN → 30s → HALF_OPEN → ≥2/3 成功 → CLOSED
- [ ] HALF_OPEN 下 < 2/3 成功 → 重新 OPEN
- [ ] OPEN 时抛 `CircuitBreakerOpenError`，调用方捕获后可走降级
- [ ] `@circuit_breaker` 装饰器在 LLM/PGVector/Redis/ES 调用路径生效
- [ ] 熔断器统计 + 管理 API 正常

### 集成
- [ ] `CircuitBreaker` 状态变更事件被 `AuditLogger` 记录
- [ ] `DegradationManager` 的健康检查独立于熔断器运行
- [ ] L2 降级通过 `FeatureFlagService` 实现秒级 Agent 回退
- [ ] 系统在无降级时（L0）行为与改造前完全一致
