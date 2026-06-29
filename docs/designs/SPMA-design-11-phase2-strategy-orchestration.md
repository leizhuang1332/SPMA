# Design: Query Rewriter Phase 2 — 编排器 + 降级管理器(基于已有 CircuitBreaker)

> **总览与索引**:[SPMA-design-11-query-rewrite-optimization-v2-final.md](SPMA-design-11-query-rewrite-optimization-v2-final.md) §1.1 中 G3 / G4
>
> **本文档角色**:8 份子 spec 中的第 2 份(Phase 2),gap-driven 结构。
> **上下游依赖**:**上游** [P1 (synonym_map 启用)](SPMA-design-11-phase1-synonym-map-activation.md);**下游** P3 / P4 / P5 多路策略都需注册到编排器。
> **预估工时**:1 周

---

## 0. 元信息

| 字段 | 值 |
|------|---|
| 状态 | 待开始 |
| 负责人 | TBD |
| 优先级 | 🟡 P1 |
| 关联缺陷 | G3 / G4 |
| 关联文件 | `src/spma/agents/supervisor/strategy_orchestrator.py` (新建)、`src/spma/agents/supervisor/fallback_manager.py` (新建)、`src/spma/infrastructure/circuit_breaker.py` (复用) |
| 预估工时 | 1 周 |
| 相关 ADR | 无(基于已有 CB 重新设计) |

---

## 1. 现状核查(实际代码)

### 1.1 CircuitBreaker 已完整实现(无需重写)

**G4 🟢**:`src/spma/infrastructure/circuit_breaker.py:1-272` 提供了**生产级**熔断器:

| 已实现 | 关键 API |
|--------|---------|
| 标准三态(CLOSED / OPEN / HALF_OPEN) | `CircuitState` enum |
| 滑动窗口(基于 `failure_count` + 阈值) | `failure_threshold=5, open_duration=30s` |
| 半开探测(连续 N 次成功) | `half_open_probe_count=3, half_open_success_threshold=2` |
| 全局注册表(同名幂等) | `get_circuit_breaker("name")` |
| 装饰器 API | `@circuit_breaker("llm_sonnet")` |
| 编程式 API + fallback | `await cb.call(coro_factory, fallback=...)` |
| 协程安全 | `asyncio.Lock` |
| 状态变更回调(可注入 metrics) | `on_state_change` callback |
| 手动重置(运维) | `cb.reset()` |

**对比原 P2 spec 设计**:
- ✅ 滑动窗口 → **已有**(`failure_count` 配 `failure_threshold`)
- ✅ 异常隔离 → **已有**(`cb.call(coro_factory, fallback)`)
- ✅ 状态机 → **已有**(完整三态 + 状态变更回调)
- 🆕 装饰器 API → **代码已实现**,原 spec 未提及
- 🆕 全局注册表 → **代码已实现**,原 spec 未提及

**关键差异**:原 P2 spec 设计了"`_window: deque(maxlen=100)` 滑动窗口",实际代码用 **`failure_count + open_duration_seconds`** 模型。两种都是有效实现,无需对齐。

### 1.2 supervisor 模块**未引用** CircuitBreaker

```bash
$ grep -rln "circuit_breaker\|get_circuit_breaker" src/spma/agents/supervisor/
(空)
```

supervisor 下的所有策略调用 LLM 时**没有任何熔断保护** — 全部依赖外部 `infrastructure/circuit_breaker` 装饰器(实际也未使用)。

### 1.3 StrategyOrchestrator / FallbackManager **不存在**

```bash
$ find src -name "strategy_orchestrator.py" -o -name "fallback_manager.py"
(空)
```

当前 `_do_rewrite_pipeline` 是**串行**调用:
```python
# query_rewriter.py:84-130(简化)
async def _do_rewrite_pipeline(...):
    normalized = await _normalize_with_synonyms(query, synonym_map, entities)
    resolved = await _resolve_references(normalized, conversation_history, llm)
    expanded = await _expand_query(resolved, classification, entities, llm)
    sources = classification.get("sources", ["doc"])
    sub_queries = await _decompose_query(expanded, entities, sources, llm)
    ...
```

**G3 🟡 P1**:无并行、无降级、无策略权重。

---

## 2. 差距分析(目标 vs 现实)

| 目标 | 现实 | 差距 |
|------|------|------|
| 策略并行执行 | 串行 `await` 链 | **需 `StrategyOrchestrator.execute_parallel()`** |
| 失败自动降级 | 无降级,任一抛错则整链失败 | **需 `FallbackManager.execute_with_fallback()`** |
| 每个策略有熔断保护 | supervisor 模块下未引用 CB | **需在策略调用处包 `@circuit_breaker("qr_xxx")`** |
| 状态变更驱动 metrics 指标 | CB 有 callback,但 metrics 未订阅 | **P6 引入 metrics_client,本 Phase 接入回调** |
| 滑动窗口统计 | CB 已有(failure_count 模型) | **无差距** |
| 熔断器注册表 | CB 已有 | **无差距** |

**关键洞察**:P2 实际只需 **2 个新文件 + 1 个集成点**:
1. `strategy_orchestrator.py` (新):提供 `execute_parallel(stage, strategies, *args)` API
2. `fallback_manager.py` (新):提供 `execute_with_fallback(query, *args)` 三级降级
3. `query_rewriter._do_rewrite_pipeline` (改):用编排器替换串行 await

**不**需要重写熔断器(已实现)、不需要新建独立 `_window: deque` 滑动窗口(用 CB 已有模型)。

---

## 3. 详细设计

### 3.1 新建 `strategy_orchestrator.py`

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
        """Args:
            stage: 编排阶段名(如 "reference_resolution" / "expansion" / "decomposition")。
            names: 参与编排的策略名(每个 name 对应一个 CircuitBreaker)。
        """
        self._stage = stage
        self._breakers: dict[str, CircuitBreaker] = {
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
        - 全部策略返回 None 或熔断 → 调用方降级(由 FallbackManager 兜底)

        Returns:
            [(strategy_name, result_or_None), ...],已过滤 None。
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
            logger.debug(f"[{self._stage}] strategy {name} circuit-open, skipped ({e.retry_after_seconds:.0f}s left)")
            return None
        except Exception as e:
            logger.warning(
                f"[{self._stage}] strategy {name} failed: {type(e).__name__}: {e}",
                exc_info=False,
            )
            return None
```

**关键设计点**:
- **不**重新发明滑动窗口 — 复用 `get_circuit_breaker()` 全局注册表
- **不**自定义异常 — 用 `CircuitBreakerOpenError`(已有)
- **不**直接管理锁 — 委托给 CB(已用 `asyncio.Lock`)
- `lambda: fn(*args, **kwargs)` 适配 CB 的 `coro_factory` 接口

### 3.2 新建 `fallback_manager.py`

`src/spma/agents/supervisor/fallback_manager.py`:

```python
"""分级降级管理器——L1 multi-strategy → L2 primary_backup → L3 rule_only。"""
import logging
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)


class FallbackManager:
    """三级降级:每次请求独立降级,无跨请求状态串扰(主文件 ADR-002)。"""

    LEVELS = ("multi_strategy", "primary_backup", "rule_only")

    def __init__(self, orchestrator, primary_backup_fn: Callable, rule_only_fn: Callable):
        self._orchestrator = orchestrator
        self._primary_backup_fn = primary_backup_fn
        self._rule_only_fn = rule_only_fn
        # 监控:全局失败计数(不影响降级逻辑)
        self._level_failures: dict[str, int] = {l: 0 for l in self.LEVELS}

    async def execute_with_fallback(
        self,
        query: str,
        strategies: dict[str, Callable[..., Awaitable]],
        *args,
        **kwargs,
    ) -> tuple[str, str]:
        """按 L1→L2→L3 顺序尝试,首个成功的级别返回。

        Returns:
            (result, level_used)
        """
        # L1: 多策略并行
        try:
            results = await self._orchestrator.execute_parallel(strategies, *args, **kwargs)
            if results:
                # 默认取第一个成功的(由调用方负责投票)
                return results[0][1], "multi_strategy"
        except Exception as e:
            self._level_failures["multi_strategy"] += 1
            logger.warning(f"FallbackManager L1 failed: {e}")

        # L2: 主备策略(由调用方注入)
        try:
            result = await self._primary_backup_fn(query, *args, **kwargs)
            if result is not None:
                return result, "primary_backup"
        except Exception as e:
            self._level_failures["primary_backup"] += 1
            logger.warning(f"FallbackManager L2 failed: {e}")

        # L3: 纯规则兜底
        try:
            return self._rule_only_fn(query, *args, **kwargs), "rule_only"
        except Exception as e:
            self._level_failures["rule_only"] += 1
            logger.error(f"FallbackManager L3 failed: {e}")
            return query, "rule_only_failed"  # 最差:返回原 query
```

**关键设计点**:
- **不**保留实例变量表示"当前 level" — 主文件 ADR-002 警示会跨请求串扰
- `_level_failures` 全局仅用于监控(由 P6 暴露 metrics)
- 返回 `(result, level_used)` 让调用方知道走了哪条路径(P6 上报)

### 3.3 集成到 `query_rewriter._do_rewrite_pipeline`

修改 `src/spma/agents/supervisor/query_rewriter.py:84-130` 流程,**最小侵入**:

**当前**(串行):
```python
async def _do_rewrite_pipeline(query, classification, entities, llm, synonym_map, conversation_history):
    result = {"original": query}
    normalized = await _normalize_with_synonyms(query, synonym_map, entities)
    result["normalized"] = normalized
    resolved = await _resolve_references(normalized, conversation_history, llm)
    result["resolved"] = resolved
    expanded = await _expand_query(resolved, classification, entities, llm)
    result["expanded"] = expanded
    sources = classification.get("sources", ["doc"])
    sub_queries = await _decompose_query(expanded, entities, sources, llm)
    result["sub_queries"] = sub_queries
    return result
```

**目标**(编排器 + 降级):
```python
async def _do_rewrite_pipeline(query, classification, entities, llm, synonym_map, conversation_history,
                               *, strategy_orchestrator=None, fallback_manager=None):
    """如果 orchestrator/fallback_manager 注入则用多路模式,否则走原串行(向后兼容)。"""
    result = {"original": query}

    # Phase 1: 同义词标准化(单策略,无需编排)
    normalized = await _normalize_with_synonyms(query, synonym_map, entities)
    result["normalized"] = normalized

    if strategy_orchestrator and fallback_manager:
        # 多路模式(默认开启,若编排器已注入)
        # P3-5 的策略由对应 PR 注册
        # 当前 PR(P2)只提供编排器骨架,具体策略在 P3-5 替换
        resolved, _ = await fallback_manager.execute_with_fallback(
            query=normalized,
            strategies={
                "rule_based": _resolve_references_rule_based,
                "entity_based": _resolve_references_entity_based,
                "llm_semantic": lambda q, h, l: _resolve_references(q, h, l),
            },
            conversation_history, llm, entities,
        )
        result["resolved"] = resolved
        # ... P4/P5 同理,留待 P3-5 PR
    else:
        # 向后兼容(无编排器时)
        resolved = await _resolve_references(normalized, conversation_history, llm)
        result["resolved"] = resolved
        expanded = await _expand_query(resolved, classification, entities, llm)
        result["expanded"] = expanded
        sources = classification.get("sources", ["doc"])
        sub_queries = await _decompose_query(expanded, entities, sources, llm)
        result["sub_queries"] = sub_queries

    return result
```

**关键设计点**:
- **P2 不**改 P3-5 已有函数(只加 1 个 `_resolve_references_rule_based` / `_entity_based` 备选)
- `strategy_orchestrator` / `fallback_manager` 通过参数注入,**不**在 `_do_rewrite_pipeline` 内部创建(便于测试)
- 向后兼容:不注入时走原串行路径(回归保护)

### 3.4 graph.py 注入编排器

修改 `src/spma/agents/supervisor/graph.py`,在 `rewrite_node` 注入编排器:

```python
from spma.agents.supervisor.strategy_orchestrator import StrategyOrchestrator
from spma.agents.supervisor.fallback_manager import FallbackManager

# 模块级单例(或在 build_graph 时创建)
_orchestrator = StrategyOrchestrator(
    stage="rewrite",
    names=["rule_based", "entity_based", "llm_semantic", "intent_aware", "synonym_based",
           "entity_injection", "context_aware", "template_based", "llm_based", "entity_guided"],
)
_fallback = FallbackManager(
    orchestrator=_orchestrator,
    primary_backup_fn=lambda q, *a, **kw: _resolve_references(q, *a, **kw),  # 临时 fallback
    rule_only_fn=lambda q, *a, **kw: q,
)

async def rewrite_node(state):
    ...
    rewritten = await rewrite_queries(
        ...,
        strategy_orchestrator=_orchestrator,    # NEW
        fallback_manager=_fallback,             # NEW
    )
```

### 3.5 状态变更驱动 metrics

```python
# 启动时
from spma.infrastructure.circuit_breaker import set_default_state_change_callback
from spma.observability.qr_metrics import build_qr_metrics

metrics = build_qr_metrics()

async def on_state_change(name, old_state, new_state):
    # P6 接入,本 Phase 留 TODO
    metrics.fallback_total.labels(level=new_state.value, stage="qr").inc()
    logger.info(f"CB {name}: {old_state.value} → {new_state.value}")

set_default_state_change_callback(on_state_change)
```

---

## 4. 与上游/下游 spec 的接口契约

### 4.1 新增/修改文件

| 文件 | 类型 | 改动 |
|------|------|------|
| `src/spma/agents/supervisor/strategy_orchestrator.py` | **新增** | `StrategyOrchestrator` 类 |
| `src/spma/agents/supervisor/fallback_manager.py` | **新增** | `FallbackManager` 类 |
| `src/spma/agents/supervisor/query_rewriter.py` | 修改 | `_do_rewrite_pipeline` 接受 `strategy_orchestrator` / `fallback_manager` 参数 |
| `src/spma/agents/supervisor/graph.py` | 修改 | `rewrite_node` 注入编排器 |

### 4.2 不需要做的事

- **不**新建 `circuit_breaker.py`(在 `infrastructure/` 已实现)
- **不**实现策略本身(由 P3/P4/P5 实现)
- **不**实现 metrics 端到端(由 P6 引入 `metrics_client`)

### 4.3 下游契约

[P3](SPMA-design-11-phase3-multi-strategy-resolution.md) / [P4](SPMA-design-11-phase4-multi-strategy-expansion.md) / [P5](SPMA-design-11-phase5-multi-strategy-decomposition.md) 实现的每个多路策略都需:
- 实现 `async def execute(*args, **kwargs) -> str` 接口
- 通过 `orchestrator.execute_parallel(strategies_dict, *args, **kwargs)` 被调用
- 异常被 `_safe_invoke` 隔离(返回 None,不抛出)

### 4.4 配置 Key

| Key | 默认 | 说明 |
|-----|------|------|
| `QR_CB_FAILURE_THRESHOLD` | 5 | CB 失败阈值(对应已有 `CircuitBreakerConfig.failure_threshold`) |
| `QR_CB_OPEN_DURATION` | 30 | CB open 持续秒数 |
| `QR_CB_HALF_OPEN_PROBE` | 3 | 半开探测次数 |
| `QR_CB_HALF_OPEN_SUCCESS` | 2 | 半开恢复所需成功数 |

---

## 5. 验收标准

| ID | 指标 | 当前 | 验收 | 测量 |
|----|------|------|------|------|
| V1 | 3 个 P2 新文件存在 | ❌ | ✅ | `find src/spma/agents/supervisor -name "*.py" \| grep -E "strategy_orchestrator\|fallback_manager"` |
| V2 | `_do_rewrite_pipeline` 接受新参数 | ❌ | ✅ | 代码 review |
| V3 | graph.py 注入编排器 | ❌ | ✅ | `grep "strategy_orchestrator" graph.py` |
| V4 | 现有 13 个 supervisor 单测全过(无回归) | 13/13 | 13/13 | pytest |
| V5 | 新增 ≥ 10 单测(编排器 + 降级 + 并发压测) | 0 | ≥ 10 | pytest |
| V6 | 1000 并发下 FallbackManager 状态不串扰 | N/A | ✅ | 压测脚本 |

---

## 6. 风险与降级

| 风险 | 触发 | 影响 | 缓解 |
|------|------|------|------|
| **R1**:CB 全 OPEN(上游全挂) | LLM 长时间故障 | 全部策略被跳过 | FallbackManager 走 L2/L3 |
| **R2**:编排器抛异常 | bug 导致 `gather` 失败 | 整条 rewrite 链失败 | FallbackManager L1 失败 → L2 → L3 兜底 |
| **R3**:多实例下 CB 不共享状态 | 多 worker 部署 | 各实例熔断独立 | **可接受**(每个实例独立熔断,反而更稳);后续 P8 接入 Redis 共享 |
| **R4**:策略超时 | 某个策略死锁 | 整条链 hang | 加 timeout 装饰器(P7 CostController 引入) |
| **R5**:L1→L2 跳变频繁 | L1 部分失败 | 抖动 | 引入"连续 N 次失败才降级"窗口(本 Phase 不实现,P6 优化) |

---

## 7. 实施步骤

### 7.1 PR 切分(3 个 PR)

**PR #1**:`StrategyOrchestrator` 骨架
- 新增 `src/spma/agents/supervisor/strategy_orchestrator.py`
- 单测:mock 3 个策略,1 个成功/1 个异常/1 个被熔断,验证返回 1 个结果
- 合并标准:单测全过 + 验证 asyncio.gather 行为

**PR #2**:`FallbackManager` 骨架
- 新增 `src/spma/agents/supervisor/fallback_manager.py`
- 单测:三级降级顺序、并发安全(1000 并发下无状态串扰)
- 合并标准:V6 压测通过

**PR #3**:`query_rewriter` 集成 + `graph.py` 注入
- 修改 `_do_rewrite_pipeline` 接受新参数(向后兼容)
- 修改 `graph.py` 注入编排器(创建单例)
- 合并标准:现有 13 单测无回归 + V2/V3 通过

### 7.2 时间表

| 工作日 | 任务 | 产出 |
|--------|------|------|
| D1 | `StrategyOrchestrator` + 单测 | PR #1 ready |
| D2 | Review PR #1 + 合并 | - |
| D2-D3 | `FallbackManager` + 并发压测 | PR #2 ready |
| D4 | Review PR #2 + 合并 | - |
| D4 | query_rewriter 集成 + graph.py 注入 | PR #3 ready |
| D5 | Review PR #3 + 合并 | - |

### 7.3 上线 checklist

- [ ] PR #1-3 合并
- [ ] 现有 13 单测无回归
- [ ] 10+ 新单测全过
- [ ] 1000 并发压测无状态串扰
- [ ] 监控:`qr_fallback_total` 指标名(代码已存在,验证注册到 Prometheus)

---

## 8. 变更日志

| 日期 | 版本 | 变更 |
|------|------|------|
| 2026-06-29 | 1.0 | **gap-driven 重写**:复用 `src/spma/infrastructure/circuit_breaker.py`(已实现),不重新发明;新增 2 个文件(`strategy_orchestrator.py` + `fallback_manager.py`) |
| 2026-06-29 | 0.9 | (回退)初次拆分,假设 CB 未实现,重新设计了带 `_window: deque` 的 CB(已回退) |
