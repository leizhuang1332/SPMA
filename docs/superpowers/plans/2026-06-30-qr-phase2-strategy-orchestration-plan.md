# Query Rewriter Phase 2 — 编排器 + 降级管理器 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复主文件 §1.1 中 G3 (`StrategyOrchestrator` / `FallbackManager` 不存在) + G4 (supervisor 模块未引用已有 `CircuitBreaker`),让 P3-P5 的多路策略能被并行调度 + 异常隔离 + 降级。

**Architecture:**
- 新建 `StrategyOrchestrator`:基于已有 `src/spma/infrastructure/circuit_breaker.py` 的 `get_circuit_breaker()` 工厂,为每个 strategy 名分配独立 CB;用 `asyncio.gather` 并行调用
- 新建 `FallbackManager`:L1 multi_strategy → L2 primary_backup → L3 rule_only 三级降级;`_current_level` 是 per-request 局部变量(主文件 ADR-002)
- 修改 `query_rewriter._do_rewrite_pipeline` 接受可选 `strategy_orchestrator` / `fallback_manager` 参数,向后兼容
- 修改 `graph.py` 创建单例并注入

**Tech Stack:** asyncio / pytest + pytest-asyncio / 已有 `spma.infrastructure.circuit_breaker`

**依赖:** [Phase 1 (synonym_map)](2026-06-30-qr-phase1-synonym-map-plan.md)
**被依赖:** P3 / P4 / P5 多路策略注册到编排器

**Spec:** [SPMA-design-11-phase2-strategy-orchestration.md](../../designs/SPMA-design-11-phase2-strategy-orchestration.md)

---

## 文件结构

| 文件 | 类型 | 职责 |
|------|------|------|
| `src/spma/agents/supervisor/strategy_orchestrator.py` | 新建 | `StrategyOrchestrator` 类 |
| `src/spma/agents/supervisor/fallback_manager.py` | 新建 | `FallbackManager` 类 |
| `src/spma/agents/supervisor/query_rewriter.py` | 修改 | `_do_rewrite_pipeline` 接受新参数(向后兼容) |
| `src/spma/agents/supervisor/graph.py` | 修改 | 创建并注入编排器/降级单例 |
| `tests/unit/agents/supervisor/test_strategy_orchestrator.py` | 新建 | 编排器单测 |
| `tests/unit/agents/supervisor/test_fallback_manager.py` | 新建 | 降级管理器单测(含并发压测) |

---

## Task 1: `StrategyOrchestrator` 骨架 + 单元测试

**Files:**
- Create: `src/spma/agents/supervisor/strategy_orchestrator.py`
- Test: `tests/unit/agents/supervisor/test_strategy_orchestrator.py`

### Step 1.1: 写失败的测试

`tests/unit/agents/supervisor/test_strategy_orchestrator.py`:

```python
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
    assert "b" not in result_dict  # b 被隔离,不出现在结果中


@pytest.mark.asyncio
async def test_execute_parallel_filters_none():
    """返回 None 的策略不出现在结果中(便于下游判断)。"""
    orch = StrategyOrchestrator(stage="test", names=["a", "b"])

    async def fn_a(x): return None  # 策略早退(本策略无能为力)
    async def fn_b(x): return "b-result"

    results = await orch.execute_parallel({"a": fn_a, "b": fn_b}, "input")
    assert dict(results) == {"b": "b-result"}


@pytest.mark.asyncio
async def test_execute_parallel_actually_concurrent():
    """3 个 sleep 0.1s 策略 → 总耗时 < 0.2s(并行而非串行)。"""
    orch = StrategyOrchestrator(stage="test", names=["a", "b", "c"])

    async def slow_fn(x):
        await asyncio.sleep(0.1)
        return x

    start = asyncio.get_event_loop().time()
    await orch.execute_parallel({"a": slow_fn, "b": slow_fn, "c": slow_fn}, "x")
    elapsed = asyncio.get_event_loop().time() - start
    assert elapsed < 0.2, f"expected parallel (<0.2s), got {elapsed:.3f}s"
```

### Step 1.2: 运行测试,确认失败

Run:
```bash
cd /Users/Ray/TraeProjects/SPMA
pytest tests/unit/agents/supervisor/test_strategy_orchestrator.py -v
```

Expected: ModuleNotFoundError 或 ImportError(`strategy_orchestrator` 不存在)

### Step 1.3: 写 `StrategyOrchestrator` 实现

`src/spma/agents/supervisor/strategy_orchestrator.py`:

```python
"""策略编排器——多路并行 + 异常隔离 + 熔断保护。

基于已有 src/spma.infrastructure.circuit_breaker,不重新发明轮子。
"""
import asyncio
import logging
from typing import Awaitable, Callable

from spma.infrastructure.circuit_breaker import (
    CircuitBreakerOpenError,
    get_circuit_breaker,
)

logger = logging.getLogger(__name__)


class StrategyOrchestrator:
    """策略编排器:统一管理多路策略的生命周期、并行调度、熔断集成。"""

    def __init__(self, stage: str, names: list[str]):
        self._stage = stage
        # 每个 strategy 分配独立 CB(全局注册表内通过 name 区分)
        self._breakers: dict[str, "CircuitBreaker"] = {
            name: get_circuit_breaker(f"qr_{stage}_{name}") for name in names
        }

    async def execute_parallel(
        self,
        strategies: dict[str, Callable[..., Awaitable]],
        *args,
        **kwargs,
    ) -> list[tuple[str, object]]:
        """并行执行所有策略,收集 (name, result) 元组。

        行为:
        - 任一策略被熔断 → 跳过,不参与本次调用
        - 任一策略抛异常 → 记录警告 + 返回 None(不影响其他策略)
        - 全部策略返回 None 或熔断 → 返回空列表(由 FallbackManager 兜底)

        Returns:
            [(strategy_name, result), ...],已过滤 None。
        """
        coros = [
            self._safe_invoke(name, fn, *args, **kwargs)
            for name, fn in strategies.items()
        ]
        # 关键(对应主文件 ADR-003):并行,不串行
        results = await asyncio.gather(*coros)
        return [
            (name, result)
            for (name, _), result in zip(strategies.items(), results)
            if result is not None
        ]

    async def _safe_invoke(self, name, fn, *args, **kwargs):
        """熔断保护 + 异常隔离。"""
        cb = self._breakers[name]
        try:
            return await cb.call(lambda: fn(*args, **kwargs))
        except CircuitBreakerOpenError as e:
            logger.debug(
                f"[{self._stage}] strategy {name} circuit-open, "
                f"skipped ({e.retry_after_seconds:.0f}s left)"
            )
            return None
        except Exception as e:
            logger.warning(
                f"[{self._stage}] strategy {name} failed: "
                f"{type(e).__name__}: {e}",
                exc_info=False,
            )
            return None
```

### Step 1.4: 重新运行测试,确认通过

Run:
```bash
cd /Users/Ray/TraeProjects/SPMA
pytest tests/unit/agents/supervisor/test_strategy_orchestrator.py -v
```

Expected: 4 passed

### Step 1.5: 提交

```bash
cd /Users/Ray/TraeProjects/SPMA
git add src/spma/agents/supervisor/strategy_orchestrator.py tests/unit/agents/supervisor/test_strategy_orchestrator.py
git commit -m "feat(qr): StrategyOrchestrator — 并行多路 + CB 集成 (G3 部分)

- 复用 src/spma/infrastructure/circuit_breaker.py(已有,272 行)
- 每策略独立 CB(全局注册表)
- 异常隔离 + None 过滤
- asyncio.gather 并行(主文件 ADR-003)

Refs: SPMA-design-11-phase2 §3.1"
```

---

## Task 2: `FallbackManager` 骨架 + 单元测试(含并发安全)

**Files:**
- Create: `src/spma/agents/supervisor/fallback_manager.py`
- Test: `tests/unit/agents/supervisor/test_fallback_manager.py`

### Step 2.1: 写失败的测试

`tests/unit/agents/supervisor/test_fallback_manager.py`:

```python
"""FallbackManager 单测 + 1000 并发压测。"""
import asyncio
import pytest

from spma.agents.supervisor.fallback_manager import FallbackManager
from spma.infrastructure.circuit_breaker import reset_all


@pytest.fixture(autouse=True)
def clear_cbs():
    reset_all()
    yield
    reset_all()


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
```

### Step 2.2: 运行测试,确认失败

Run:
```bash
cd /Users/Ray/TraeProjects/SPMA
pytest tests/unit/agents/supervisor/test_fallback_manager.py -v
```

Expected: ImportError(`fallback_manager` 不存在)

### Step 2.3: 写 `FallbackManager` 实现

`src/spma/agents/supervisor/fallback_manager.py`:

```python
"""分级降级管理器——L1 multi_strategy → L2 primary_backup → L3 rule_only。

主文件 ADR-002:每次请求独立降级,无跨请求状态串扰。
"""
import logging
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)


class FallbackManager:
    """三级降级:每次请求独立降级,无跨请求状态串扰。"""

    LEVELS = ("multi_strategy", "primary_backup", "rule_only")

    def __init__(
        self,
        orchestrator,
        primary_backup_fn: Callable,
        rule_only_fn: Callable,
    ):
        self._orchestrator = orchestrator
        self._primary_backup_fn = primary_backup_fn
        self._rule_only_fn = rule_only_fn
        # 监控:全局失败计数(仅用于 P6 metrics)
        self._level_failures: dict[str, int] = {l: 0 for l in self.LEVELS}

    async def execute_with_fallback(
        self,
        query: str,
        strategies: dict,
        *args,
        **kwargs,
    ) -> tuple[str, str]:
        """按 L1→L2→L3 顺序尝试,首个成功的级别返回。

        Returns:
            (result, level_used)
        """
        # L1: 多策略并行
        try:
            results = await self._orchestrator.execute_parallel(
                strategies, *args, **kwargs,
            )
            if results:
                # 默认取第一个成功的(P3 voter 会替换为投票逻辑)
                return results[0][1], "multi_strategy"
        except Exception as e:
            self._level_failures["multi_strategy"] += 1
            logger.warning(f"FallbackManager L1 failed: {type(e).__name__}: {e}")

        # L2: 主备策略(由调用方注入)
        try:
            result = await self._primary_backup_fn(query, *args, **kwargs)
            if result is not None:
                return result, "primary_backup"
        except Exception as e:
            self._level_failures["primary_backup"] += 1
            logger.warning(f"FallbackManager L2 failed: {type(e).__name__}: {e}")

        # L3: 纯规则兜底
        try:
            return self._rule_only_fn(query, *args, **kwargs), "rule_only"
        except Exception as e:
            self._level_failures["rule_only"] += 1
            logger.error(f"FallbackManager L3 failed: {type(e).__name__}: {e}")
            return query, "rule_only_failed"  # 最差:返回原 query
```

### Step 2.4: 重新运行测试

Run:
```bash
cd /Users/Ray/TraeProjects/SPMA
pytest tests/unit/agents/supervisor/test_fallback_manager.py -v
```

Expected: 4 passed(含 1000 并发压测)

### Step 2.5: 提交

```bash
cd /Users/Ray/TraeProjects/SPMA
git add src/spma/agents/supervisor/fallback_manager.py tests/unit/agents/supervisor/test_fallback_manager.py
git commit -m "feat(qr): FallbackManager — L1→L2→L3 三级降级 (G3 部分)

- per-request 局部状态,无实例变量跨请求串扰(主文件 ADR-002)
- 1000 并发压测覆盖状态隔离
- _level_failures 全局计数(供 P6 监控消费)

Refs: SPMA-design-11-phase2 §3.2"
```

---

## Task 3: `_do_rewrite_pipeline` 集成(向后兼容)

**Files:**
- Modify: `src/spma/agents/supervisor/query_rewriter.py:84-130`
- Test: `tests/unit/agents/supervisor/test_query_rewriter_orchestrator.py`

### Step 3.1: 写失败的测试(验证新参数被接受 + 向后兼容)

`tests/unit/agents/supervisor/test_query_rewriter_orchestrator.py`:

```python
"""验证 _do_rewrite_pipeline 接受 strategy_orchestrator / fallback_manager 参数。"""
import inspect
import pytest

from spma.agents.supervisor import query_rewriter


def test_do_rewrite_pipeline_signature_accepts_orchestrator():
    """新参数必须存在于签名。"""
    sig = inspect.signature(query_rewriter._do_rewrite_pipeline)
    assert "strategy_orchestrator" in sig.parameters
    assert "fallback_manager" in sig.parameters
    # 默认 None(向后兼容)
    assert sig.parameters["strategy_orchestrator"].default is None
    assert sig.parameters["fallback_manager"].default is None


@pytest.mark.asyncio
async def test_do_rewrite_pipeline_works_without_orchestrator():
    """不注入编排器时,走原串行路径(向后兼容)。"""
    result = await query_rewriter._do_rewrite_pipeline(
        query="测试",
        classification={"query_type": "search", "sources": ["doc"]},
        entities={},
        llm=None,  # 无 LLM,各步直接 return
        synonym_map=None,
        conversation_history="",
    )
    assert "original" in result
    assert "normalized" in result
    assert "resolved" in result
    assert "expanded" in result
    assert "sub_queries" in result
```

### Step 3.2: 运行测试,确认失败

Run:
```bash
cd /Users/Ray/TraeProjects/SPMA
pytest tests/unit/agents/supervisor/test_query_rewriter_orchestrator.py -v
```

Expected: FAIL(签名不包含新参数)

### Step 3.3: 修改 `_do_rewrite_pipeline` 签名

修改 `src/spma/agents/supervisor/query_rewriter.py:84-86`:

**修改前**:
```python
async def _do_rewrite_pipeline(
    query, classification, entities, llm, synonym_map, conversation_history,
):
    """原 rewrite_queries 主体(去掉外层 cache wrap)."""
```

**修改后**:
```python
async def _do_rewrite_pipeline(
    query, classification, entities, llm, synonym_map, conversation_history,
    *,
    strategy_orchestrator=None,
    fallback_manager=None,
):
    """原 rewrite_queries 主体(去掉外层 cache wrap)。

    P2 扩展:接受可选 strategy_orchestrator / fallback_manager。
    - None 时:走原串行(向后兼容)
    - 注入时:P3-5 多路策略将用编排器替换对应阶段
    """
```

(本 Task **只改签名**,不实际替换内部逻辑;后续 P3-5 PR 替换各阶段)

### Step 3.4: 重新运行测试

Run:
```bash
cd /Users/Ray/TraeProjects/SPMA
pytest tests/unit/agents/supervisor/test_query_rewriter_orchestrator.py -v
```

Expected: 2 passed

### Step 3.5: 运行所有 supervisor 单测,确保无回归

Run:
```bash
cd /Users/Ray/TraeProjects/SPMA
pytest tests/unit/agents/supervisor/ -v
```

Expected: 13+ 个原单测全过

### Step 3.6: 提交

```bash
cd /Users/Ray/TraeProjects/SPMA
git add src/spma/agents/supervisor/query_rewriter.py tests/unit/agents/supervisor/test_query_rewriter_orchestrator.py
git commit -m "feat(qr): _do_rewrite_pipeline 接受 orchestrator/fallback 参数(向后兼容)

签名扩展(关键字参数,默认 None),不修改内部逻辑。
P3-5 后续 PR 替换各阶段为多路。

Refs: SPMA-design-11-phase2 §3.3"
```

---

## Task 4: `graph.py` 注入编排器/降级单例

**Files:**
- Modify: `src/spma/agents/supervisor/graph.py`

### Step 4.1: 在 `graph.py` 顶部创建单例

修改 `src/spma/agents/supervisor/graph.py`,在 imports 之后、`build_graph` 之前加:

```python
# P2: 编排器 + 降级单例(模块级,所有 build_graph 调用共享)
_orchestrator = StrategyOrchestrator(
    stage="rewrite",
    names=[
        # P3 指代消解
        "rule_based", "entity_based", "llm_semantic",
        # P4 扩展
        "intent_aware", "synonym_based", "entity_injection", "context_aware",
        # P5 分解
        "template_based", "llm_based", "entity_guided",
    ],
)
_fallback = FallbackManager(
    orchestrator=_orchestrator,
    # 临时 fallback:任意一个 LLM 策略;P3 替换为多路语义
    primary_backup_fn=lambda q, *a, **kw: None,
    rule_only_fn=lambda q, *a, **kw: q,  # 返回原 query
)
```

并在文件顶部 imports 段添加:

```python
from spma.agents.supervisor.strategy_orchestrator import StrategyOrchestrator
from spma.agents.supervisor.fallback_manager import FallbackManager
```

### Step 4.2: 把单例注入到 `build_graph`

修改 `build_graph` 签名,接受 `strategy_orchestrator` 和 `fallback_manager` 参数(默认用模块级单例):

```python
def build_graph(
    primary_llm,
    fallback_llm=None,
    *,
    quality_threshold: float = 0.6,
    reschedule_max: int = 2,
    qr_cache=None,
    qr_audit_buffer=None,
    qr_state_lookup=None,
    strategy_orchestrator=None,    # NEW
    fallback_manager=None,         # NEW
) -> StateGraph:
    # 默认用模块级单例
    strategy_orchestrator = strategy_orchestrator or _orchestrator
    fallback_manager = fallback_manager or _fallback
    ...
```

### Step 4.3: 找到 `rewrite_queries` 调用点,传入新参数

在 `rewrite_node` 函数体内,找到:

```python
rewritten = await rewrite_queries(
    query=state["original_query"],
    ...
)
```

修改为(关键是 `strategy_orchestrator=` 和 `fallback_manager=` 关键字参数):

**注意**:`rewrite_queries` 本身需要修改以转发给 `_do_rewrite_pipeline`。先看 `rewrite_queries` 当前实现,如果它已转发 `**kwargs` 到 `_do_rewrite_pipeline`,则无需改 `rewrite_queries`;否则需修改。

```python
# 简化示例(具体见实际代码)
rewritten = await rewrite_queries(
    ...,
    strategy_orchestrator=strategy_orchestrator,
    fallback_manager=fallback_manager,
)
```

如 `rewrite_queries` 签名未接收这两个参数,**先**修改 `rewrite_queries` 签名 + 转发,**后**调用处传入。这是 PR 内的两步小改动。

### Step 4.4: 运行所有 supervisor 单测,确保无回归

Run:
```bash
cd /Users/Ray/TraeProjects/SPMA
pytest tests/unit/agents/supervisor/ -v
```

Expected: 13+ 原单测 + 5 个新单测(4 orchestrator + 1 集成)全过

### Step 4.5: 提交

```bash
cd /Users/Ray/TraeProjects/SPMA
git add src/spma/agents/supervisor/graph.py
git commit -m "feat(qr): graph.py 注入编排器/降级单例(G3 完成)

模块级单例 StrategyOrchestrator(stage='rewrite', 10 个策略名) +
FallbackManager。
build_graph 接受可选参数,默认用单例。
P3-5 后续接入具体策略。

Refs: SPMA-design-11-phase2 §3.4"
```

---

## Task 5: 24h 灰度观察

### Step 5.1: 部署到生产

按项目 deploy 流程(3 个 PR 顺序合并,每个 PR 后观察 5 分钟):
- PR #1: StrategyOrchestrator
- PR #2: FallbackManager
- PR #3: `_do_rewrite_pipeline` 签名 + graph.py 注入

### Step 5.2: 监控 24h

| 监控项 | 期望 | 不期望 |
|--------|------|--------|
| 现有 13 个 supervisor 单测 + 集成测试 | 全过 | 任何回归 |
| 1000 并发压测 | 无状态串扰 | 用户间串扰 |
| `qr_fallback_total` 指标 | = 0(默认全 L1 命中) | 持续 > 0(说明编排器抛错) |
| 日志中 `StrategyOrchestrator.* failed` | 0 条 | 频繁 |

### Step 5.3: 关闭 P2

更新主文件 §1.1:

```markdown
| ~~G3~~ | ~~P2~~ | ~~StrategyOrchestrator/FallbackManager 不存在~~ | ✅ 已修复 | - |
| ~~G4~~ | ~~P2~~ | ~~CircuitBreaker 未在 supervisor 引用~~ | ✅ 已修复(复用已有 CB) | - |
```

并 commit:

```bash
cd /Users/Ray/TraeProjects/SPMA
git add docs/designs/SPMA-design-11-query-rewrite-optimization-v2-final.md
git commit -m "docs(qr): G3/G4 标记为已修复(P2 完成)"
```

---

## 验收 checklist

- [ ] Task 1:`test_strategy_orchestrator.py` 4 case 通过
- [ ] Task 2:`test_fallback_manager.py` 4 case 通过(含 1000 并发)
- [ ] Task 3:`test_query_rewriter_orchestrator.py` 2 case 通过 + 13 原单测无回归
- [ ] Task 4:`graph.py` 注入,集成测试通过
- [ ] Task 5:24h 灰度无 P0 故障
- [ ] 主文件 §1.1 G3/G4 标记为已修复
- [ ] 后续 P3-5 可以 `register_strategy("rewrite", name, fn, config)` 注册具体策略

---

## 失败回滚

```bash
git revert <commit_hash_of_task_N>
# 部署旧版本
# 模块级单例在重启时重建,无副作用
```
