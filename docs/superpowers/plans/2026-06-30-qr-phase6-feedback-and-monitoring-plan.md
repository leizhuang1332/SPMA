# Query Rewriter Phase 6 — 反馈闭环 + 监控 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复主文件 §1.1 G8(离线评估/EMA)/ G9(MMD 漂移)/ G10(人工审核),利用已有 `qr_metrics` 8 项 + `qr_state` 权重管理,新增 4 个决策组件。

**Architecture:**
- 新增 `qr_metrics_bridge`:把已有 `set_default_state_change_callback` 接到 `qr_fallback_total` 指标
- 新增 `StableStrategyEvaluator`:EMA 平滑 + min_weight 边界(主文件 §3.5),写入 `qr_weights_history`(复用已有)
- 新增 `DistributionShiftDetector`:MMD 漂移(主文件 ADR-005)
- 新增 `HumanInTheLoopValidator`:完整工单流程(主文件 ADR-006)+ 超时自动拒绝

**Tech Stack:** asyncpg / numpy / asyncio / 已有 `qr_metrics` / 已有 `qr_state` / 已有 `qr_weights_history` 表

**依赖:** [P3](2026-06-30-qr-phase3-multi-strategy-resolution-plan.md) / [P4](2026-06-30-qr-phase4-multi-strategy-expansion-plan.md) / [P5](2026-06-30-qr-phase5-multi-strategy-decomposition-plan.md) 多路策略提供评估数据
**被依赖:** P8 消费 `human_validator.approve()` 触发的 `bump_weights_version` 信号

**Spec:** [SPMA-design-11-phase6-feedback-and-monitoring.md](../../designs/SPMA-design-11-phase6-feedback-and-monitoring.md)

---

## 文件结构

| 文件 | 类型 | 职责 |
|------|------|------|
| `src/spma/agents/supervisor/qr_metrics_bridge.py` | 新建 | CB 状态变更 → metrics |
| `src/spma/agents/supervisor/strategy_evaluator.py` | 新建 | `StableStrategyEvaluator` |
| `src/spma/agents/supervisor/shift_detector.py` | 新建 | `DistributionShiftDetector` (MMD) |
| `src/spma/agents/supervisor/human_validator.py` | 新建 | `HumanInTheLoopValidator` |
| `tests/unit/agents/supervisor/test_qr_metrics_bridge.py` | 新建 | bridge 单测 |
| `tests/unit/agents/supervisor/test_strategy_evaluator.py` | 新建 | evaluator 单测 |
| `tests/unit/agents/supervisor/test_shift_detector.py` | 新建 | shift_detector 单测 |
| `tests/unit/agents/supervisor/test_human_validator.py` | 新建 | human_validator 单测 |

---

## Task 1: `qr_metrics_bridge` 把 CB 状态变更接到 metrics

**Files:**
- Create: `src/spma/agents/supervisor/qr_metrics_bridge.py`
- Test: `tests/unit/agents/supervisor/test_qr_metrics_bridge.py`

### Step 1.1: 写失败的测试

`tests/unit/agents/supervisor/test_qr_metrics_bridge.py`:

```python
"""qr_metrics_bridge 单测。"""
import pytest

from spma.agents.supervisor.qr_metrics_bridge import install_qr_metrics_bridge
from spma.infrastructure.circuit_breaker import (
    get_circuit_breaker, set_default_state_change_callback, reset_all,
)
from spma.observability.qr_metrics import build_qr_metrics


@pytest.fixture(autouse=True)
def clear_cbs():
    reset_all()
    set_default_state_change_callback(None)
    yield
    reset_all()
    set_default_state_change_callback(None)


@pytest.mark.asyncio
async def test_bridge_installs_callback_that_increments_metric():
    """安装 bridge 后,CB 状态变更触发 qr_fallback_total 计数。"""
    qr_metrics = build_qr_metrics()
    install_qr_metrics_bridge(qr_metrics, stage="test")

    # 触发 CB 状态变更
    cb = get_circuit_breaker("test_strategy_a")
    # 模拟连续 5 次失败触发 OPEN
    async def fail():
        raise RuntimeError("fail")
    for _ in range(5):
        try:
            await cb.call(fail)
        except RuntimeError:
            pass

    # 验证指标被 inc
    val = qr_metrics.fallback_total.labels(level="open", stage="test")._value.get()
    assert val >= 1, f"expected fallback_total > 0, got {val}"
```

### Step 1.2: 运行测试,确认失败

Run:
```bash
cd /Users/Ray/TraeProjects/SPMA
pytest tests/unit/agents/supervisor/test_qr_metrics_bridge.py -v
```

Expected: ImportError(`qr_metrics_bridge` 不存在)

### Step 1.3: 写 `qr_metrics_bridge` 实现

`src/spma/agents/supervisor/qr_metrics_bridge.py`:

```python
"""桥接 CB 状态变更 → Prometheus 指标(主文件 ADR-009)。"""
import logging

from spma.infrastructure.circuit_breaker import set_default_state_change_callback

logger = logging.getLogger(__name__)


def install_qr_metrics_bridge(qr_metrics, stage: str = "qr"):
    """把 qr_metrics 接入 CB 全局回调。

    调用一次,所有 CB 状态变更自动触发 qr_fallback_total{level=state} +1。
    """
    async def on_state_change(name: str, old_state, new_state):
        try:
            qr_metrics.fallback_total.labels(
                level=new_state.value, stage=stage,
            ).inc()
            logger.info(f"CB {name}: {old_state.value} → {new_state.value}")
        except Exception as e:
            logger.exception(f"CB→metrics bridge failed: {e}")

    set_default_state_change_callback(on_state_change)
    logger.info(f"CB→metrics bridge installed (stage={stage})")
```

### Step 1.4: 重新运行测试

Run:
```bash
cd /Users/Ray/TraeProjects/SPMA
pytest tests/unit/agents/supervisor/test_qr_metrics_bridge.py -v
```

Expected: 1 passed

### Step 1.5: 提交

```bash
cd /Users/Ray/TraeProjects/SPMA
git add src/spma/agents/supervisor/qr_metrics_bridge.py tests/unit/agents/supervisor/test_qr_metrics_bridge.py
git commit -m "feat(qr): qr_metrics_bridge — CB 状态变更驱动指标(G8 准备)

调用 install_qr_metrics_bridge(qr_metrics) 一次,所有 CB 状态变更
自动触发 qr_fallback_total{level=state, stage} 计数。

Refs: SPMA-design-11-phase6 §3.1"
```

---

## Task 2: `StableStrategyEvaluator` + 单测

**Files:**
- Create: `src/spma/agents/supervisor/strategy_evaluator.py`
- Test: `tests/unit/agents/supervisor/test_strategy_evaluator.py`

### Step 2.1: 写失败的测试

`tests/unit/agents/supervisor/test_strategy_evaluator.py`:

```python
"""StableStrategyEvaluator 单测。"""
import pytest
from unittest.mock import AsyncMock, MagicMock

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
    """EMA:新权重 = 0.1*新评分 + 0.9*旧权重(平滑)。"""
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
    assert abs(result["weight_diffs"]["a"]["new"] - 0.37) < 0.01
    assert abs(result["weight_diffs"]["b"]["new"] - 0.63) < 0.01


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
```

### Step 2.2: 运行测试,确认失败

Run:
```bash
cd /Users/Ray/TraeProjects/SPMA
pytest tests/unit/agents/supervisor/test_strategy_evaluator.py -v
```

Expected: ImportError(`strategy_evaluator` 不存在)

### Step 2.3: 写 `StableStrategyEvaluator` 实现

`src/spma/agents/supervisor/strategy_evaluator.py`:

```python
"""离线策略评估 + EMA 权重进化(主文件 §3.5 + ADR-005/006)。

复用已有 write_weights_snapshot 写入 qr_weights_history。
"""
import logging
from typing import Awaitable, Callable

from spma.agents.supervisor.qr_state import write_weights_snapshot

logger = logging.getLogger(__name__)


class StableStrategyEvaluator:
    """带 EMA + min_weight 边界约束的离线评估器。"""

    def __init__(self, db_pool, ema_alpha: float = 0.1, min_weight: float = 0.1):
        self._pool = db_pool
        self._ema_alpha = ema_alpha
        self._min_weight = min_weight

    async def evaluate_and_propose(
        self,
        test_cases: list[dict],
        current_weights: dict[str, float],
        evaluator: Callable[[dict], Awaitable[dict[str, float]]],
    ) -> dict:
        """评估 + 提出新权重(写入 qr_weights_history 但不应用)。

        Returns:
            {
                "evaluation": {strategy_name: {"avg_score": ..., "count": ...}, ...},
                "weight_diffs": {strategy_name: {"old": x, "new": y, "delta": z}, ...},
                "snapshot_id": int,
                "should_review": bool,
            }
        """
        # 1) 评估
        results: dict[str, list[float]] = {n: [] for n in current_weights}
        for case in test_cases:
            scores = await evaluator(case)
            for n, s in scores.items():
                if n in results:
                    results[n].append(float(s))

        eval_summary = {
            n: {"avg_score": sum(s) / len(s) if s else 0.0, "count": len(s)}
            for n, s in results.items()
        }

        # 2) EMA + min_weight
        diffs = {}
        for n, current_w in current_weights.items():
            target_w = eval_summary[n]["avg_score"]
            new_w = (1 - self._ema_alpha) * current_w + self._ema_alpha * target_w
            new_w = max(self._min_weight, new_w)
            diffs[n] = {"old": current_w, "new": new_w, "delta": new_w - current_w}

        # 归一化
        total = sum(d["new"] for d in diffs.values())
        if total > 0:
            for d in diffs.values():
                d["new"] /= total

        # 3) 写入 qr_weights_history(不应用)
        snapshot_id = await write_weights_snapshot(
            self._pool,
            payload={"weights": {n: d["new"] for n, d in diffs.items()}},
            source="ema",
            applied_at=None,
        )

        # 4) 审核触发
        total_delta = sum(abs(d["delta"]) for d in diffs.values())
        should_review = total_delta > 0.1

        return {
            "evaluation": eval_summary,
            "weight_diffs": diffs,
            "snapshot_id": snapshot_id,
            "should_review": should_review,
        }
```

### Step 2.4: 重新运行测试

Run:
```bash
cd /Users/Ray/TraeProjects/SPMA
pytest tests/unit/agents/supervisor/test_strategy_evaluator.py -v
```

Expected: 4 passed

### Step 2.5: 提交

```bash
cd /Users/Ray/TraeProjects/SPMA
git add src/spma/agents/supervisor/strategy_evaluator.py tests/unit/agents/supervisor/test_strategy_evaluator.py
git commit -m "feat(qr): StableStrategyEvaluator — EMA + min_weight 约束(G8 部分)

主文件 §3.5:EMA(0.1)+ min_weight(0.1) + 归一化。
复用已有 write_weights_snapshot 写 qr_weights_history。
total_delta > 0.1 触发 should_review。

Refs: SPMA-design-11-phase6 §3.2"
```

---

## Task 3: `DistributionShiftDetector` (MMD) + 单测

**Files:**
- Create: `src/spma/agents/supervisor/shift_detector.py`
- Test: `tests/unit/agents/supervisor/test_shift_detector.py`

### Step 3.1: 写失败的测试

`tests/unit/agents/supervisor/test_shift_detector.py`:

```python
"""DistributionShiftDetector MMD 单测(主文件 ADR-005)。"""
import numpy as np
import pytest

from spma.agents.supervisor.shift_detector import DistributionShiftDetector


class FakeEmbedder:
    """生成可控 embedding:文本 → 二进制向量(可控偏移)。"""
    def __init__(self, shift: int = 0):
        self.shift = shift

    async def embed_query(self, text):
        # 简化为基于文本 hash 的 8 维向量
        h = hash(text) + self.shift
        return [(h >> i) & 1 for i in range(8)]

    async def embed_documents(self, texts):
        return [await self.embed_query(t) for t in texts]


@pytest.mark.asyncio
async def test_no_baseline_returns_no_shift():
    """无 baseline → is_shifted=False。"""
    det = DistributionShiftDetector(FakeEmbedder())
    result = await det.detect_shift(["test1", "test2"])
    assert result["is_shifted"] is False
    assert result["reason"] == "no_baseline"


@pytest.mark.asyncio
async def test_same_distribution_no_shift():
    """相同分布 → is_shifted=False。"""
    det = DistributionShiftDetector(FakeEmbedder())
    queries = [f"q{i}" for i in range(50)]
    await det.fit_baseline(queries)
    result = await det.detect_shift(queries)
    assert result["is_shifted"] is False


@pytest.mark.asyncio
async def test_shifted_distribution_detected():
    """大幅偏移的分布 → is_shifted=True。"""
    det = DistributionShiftDetector(FakeEmbedder(shift=0))
    baseline = [f"q{i}" for i in range(50)]
    await det.fit_baseline(baseline)
    # 10000 偏移 → embedding 完全改变
    det_shifted = DistributionShiftDetector(FakeEmbedder(shift=10000))
    det_shifted._baseline = det._baseline
    result = await det_shifted.detect_shift([f"q{i}" for i in range(50)])
    assert result["is_shifted"] is True


@pytest.mark.asyncio
async def test_fit_baseline_samples_large_input():
    """> sample_size 时,fit_baseline 随机采样(不爆内存)。"""
    det = DistributionShiftDetector(FakeEmbedder(), sample_size=10)
    # 输入 1000 条,只采样 10 条
    queries = [f"q{i}" for i in range(1000)]
    await det.fit_baseline(queries)
    assert det._baseline is not None
    assert len(det._baseline) == 10
```

### Step 3.2: 运行测试,确认失败

Run:
```bash
cd /Users/Ray/TraeProjects/SPMA
pytest tests/unit/agents/supervisor/test_shift_detector.py -v
```

Expected: ImportError(`shift_detector` 不存在)

### Step 3.3: 写 `DistributionShiftDetector` 实现

`src/spma/agents/supervisor/shift_detector.py`:

```python
"""分布漂移检测——MMD + 采样相似度分布(主文件 ADR-005)。

替代 v3.0 错误的"embedding 均值差"。
"""
import asyncio
import logging
import random

import numpy as np

logger = logging.getLogger(__name__)


class DistributionShiftDetector:
    """MMD 漂移检测。"""

    def __init__(self, embedder, mmd_threshold: float = 0.05, sample_size: int = 200):
        self._embedder = embedder
        self._mmd_threshold = mmd_threshold
        self._sample_size = sample_size
        self._baseline: np.ndarray | None = None

    async def fit_baseline(self, queries: list[str]):
        if len(queries) > self._sample_size:
            queries = random.sample(queries, self._sample_size)
        embs = await self._embedder.embed_documents(queries)
        self._baseline = np.array(embs)
        logger.info(f"DistributionShiftDetector: baseline fitted (n={len(embs)})")

    async def detect_shift(self, queries: list[str]) -> dict:
        if self._baseline is None:
            return {"is_shifted": False, "reason": "no_baseline"}

        if len(queries) > self._sample_size:
            queries = random.sample(queries, self._sample_size)
        current = np.array(await self._embedder.embed_documents(queries))

        mmd = self._compute_mmd(self._baseline, current)
        baseline_sim = self._pairwise_sim(self._baseline)
        current_sim = self._pairwise_sim(current)
        sim_shift = abs(float(np.mean(baseline_sim) - np.mean(current_sim)))

        is_shifted = mmd > self._mmd_threshold or sim_shift > 0.1
        return {
            "is_shifted": is_shifted,
            "mmd_score": float(mmd),
            "sim_dist_shift": sim_shift,
            "recommendation": "re_evaluate_weights" if is_shifted else "keep_current",
        }

    def _compute_mmd(self, X, Y, sigma: float = 1.0) -> float:
        def gaussian(A, B):
            d = np.sum(A ** 2, axis=1)[:, None] + np.sum(B ** 2, axis=1)[None, :] - 2 * A @ B.T
            return np.exp(-d / (2 * sigma ** 2))
        Kxx = gaussian(X, X)
        Kyy = gaussian(Y, Y)
        Kxy = gaussian(X, Y)
        np.fill_diagonal(Kxx, 0)
        np.fill_diagonal(Kyy, 0)
        n, m = len(X), len(Y)
        return float(
            Kxx.sum() / (n * (n - 1)) + Kyy.sum() / (m * (m - 1)) - 2 * Kxy.mean()
        )

    def _pairwise_sim(self, embs: np.ndarray) -> np.ndarray:
        n = min(len(embs), 100)
        idx = np.random.choice(len(embs), n, replace=False)
        sampled = embs[idx]
        normed = sampled / (np.linalg.norm(sampled, axis=1, keepdims=True) + 1e-10)
        sims = normed @ normed.T
        return sims[np.triu_indices(n, k=1)]
```

### Step 3.4: 重新运行测试

Run:
```bash
cd /Users/Ray/TraeProjects/SPMA
pytest tests/unit/agents/supervisor/test_shift_detector.py -v
```

Expected: 4 passed

### Step 3.5: 提交

```bash
cd /Users/Ray/TraeProjects/SPMA
git add src/spma/agents/supervisor/shift_detector.py tests/unit/agents/supervisor/test_shift_detector.py
git commit -m "feat(qr): DistributionShiftDetector — MMD + 采样相似度(G9)

主文件 ADR-005:替代 v3.0 错误的'均值差'。
高斯核 MMD + 1m 滚动相似度分布双指标。
评估在非热路径(~200ms),可接受。

Refs: SPMA-design-11-phase6 §3.3"
```

---

## Task 4: `HumanInTheLoopValidator` + 单测

**Files:**
- Create: `src/spma/agents/supervisor/human_validator.py`
- Test: `tests/unit/agents/supervisor/test_human_validator.py`

### Step 4.1: 写失败的测试

`tests/unit/agents/supervisor/test_human_validator.py`:

```python
"""HumanInTheLoopValidator 单测(主文件 ADR-006)。"""
import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock

from spma.agents.supervisor.human_validator import HumanInTheLoopValidator


@pytest.fixture
def mock_pool():
    pool = MagicMock()
    conn = MagicMock()
    conn.execute = AsyncMock(return_value=None)
    conn.fetchval = AsyncMock(return_value=2)
    acquire_ctx = MagicMock()
    acquire_ctx.__aenter__ = AsyncMock(return_value=conn)
    acquire_ctx.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=acquire_ctx)
    transaction_ctx = MagicMock()
    transaction_ctx.__aenter__ = AsyncMock(return_value=None)
    transaction_ctx.__aexit__ = AsyncMock(return_value=None)
    conn.transaction = MagicMock(return_value=transaction_ctx)
    return pool


def test_should_review_true_for_large_delta():
    hv = HumanInTheLoopValidator(mock_pool, timeout_seconds=86400)
    diffs = {"a": {"delta": 0.15}, "b": {"delta": -0.05}}
    assert hv.should_review(diffs) is True  # total = 0.20 > 0.1


def test_should_review_false_for_small_delta():
    hv = HumanInTheLoopValidator(mock_pool, timeout_seconds=86400)
    diffs = {"a": {"delta": 0.05}, "b": {"delta": -0.02}}
    assert hv.should_review(diffs) is False  # total = 0.07 < 0.1


@pytest.mark.asyncio
async def test_submit_creates_pending_ticket():
    mock_pool = MagicMock()
    hv = HumanInTheLoopValidator(mock_pool, timeout_seconds=86400)
    diffs = {"a": {"old": 0.3, "new": 0.4, "delta": 0.1}}
    evaluation = {"a": {"avg_score": 0.8, "count": 100}}
    ticket_id = await hv.submit_for_review(diffs, evaluation)
    assert ticket_id.startswith("qr-review-")
    assert ticket_id in hv._pending
    assert hv._pending[ticket_id]["status"] == "pending"


@pytest.mark.asyncio
async def test_approve_writes_active_snapshot_and_bumps_version():
    """approve 写 is_active=TRUE 的新快照并 bump_weights_version。"""
    mock_pool = MagicMock()
    conn = MagicMock()
    conn.execute = AsyncMock(return_value=None)
    conn.fetchval = AsyncMock(return_value=2)
    acquire_ctx = MagicMock()
    acquire_ctx.__aenter__ = AsyncMock(return_value=conn)
    acquire_ctx.__aexit__ = AsyncMock(return_value=None)
    mock_pool.acquire = MagicMock(return_value=acquire_ctx)
    transaction_ctx = MagicMock()
    transaction_ctx.__aenter__ = AsyncMock(return_value=None)
    transaction_ctx.__aexit__ = AsyncMock(return_value=None)
    conn.transaction = MagicMock(return_value=transaction_ctx)

    hv = HumanInTheLoopValidator(mock_pool, timeout_seconds=86400)
    ticket_id = await hv.submit_for_review(
        {"a": {"old": 0.3, "new": 0.4, "delta": 0.1}},
        {"a": {"avg_score": 0.8, "count": 100}},
    )
    success = await hv.approve(ticket_id, approver="alice")
    assert success is True
    assert hv._pending[ticket_id]["status"] == "approved"
    assert hv._pending[ticket_id]["approver"] == "alice"


@pytest.mark.asyncio
async def test_reject_keeps_old_weights():
    """reject 不写新快照,状态变 rejected。"""
    mock_pool = MagicMock()
    hv = HumanInTheLoopValidator(mock_pool, timeout_seconds=86400)
    ticket_id = await hv.submit_for_review(
        {"a": {"old": 0.3, "new": 0.4, "delta": 0.1}},
        {"a": {"avg_score": 0.8, "count": 100}},
    )
    success = await hv.reject(ticket_id, approver="bob", reason="not confident")
    assert success is True
    assert hv._pending[ticket_id]["status"] == "rejected"
```

### Step 4.2: 运行测试,确认失败

Run:
```bash
cd /Users/Ray/TraeProjects/SPMA
pytest tests/unit/agents/supervisor/test_human_validator.py -v
```

Expected: ImportError(`human_validator` 不存在)

### Step 4.3: 写 `HumanInTheLoopValidator` 实现

`src/spma/agents/supervisor/human_validator.py`:

```python
"""人工审核闭环(主文件 ADR-006:完整工单流程 + 超时自动拒绝)。"""
import asyncio
import json
import logging
import time
import uuid

from spma.agents.supervisor.qr_state import bump_weights_version

logger = logging.getLogger(__name__)


class HumanInTheLoopValidator:
    """权重变更工单化。"""

    def __init__(self, db_pool, ticket_client=None, timeout_seconds: int = 86400):
        self._pool = db_pool
        self._tickets = ticket_client
        self._timeout = timeout_seconds
        self._pending: dict[str, dict] = {}

    def should_review(self, weight_diffs: dict) -> bool:
        total_delta = sum(abs(d["delta"]) for d in weight_diffs.values())
        return total_delta > 0.1

    async def submit_for_review(self, weight_diffs: dict, evaluation: dict) -> str:
        ticket_id = f"qr-review-{uuid.uuid4().hex[:8]}"
        self._pending[ticket_id] = {
            "created_at": time.time(),
            "diffs": weight_diffs,
            "evaluation": evaluation,
            "status": "pending",
        }
        if self._tickets:
            try:
                await self._tickets.create(
                    title=f"[QR Strategy] 权重变更待审核 ({ticket_id})",
                    body=self._format_report(weight_diffs, evaluation),
                    labels=["query-rewriter", "weight-review"],
                )
            except Exception as e:
                logger.warning(f"Ticket creation failed: {e}, will rely on in-memory pending")
        asyncio.create_task(self._expire_review(ticket_id))
        total_delta = sum(abs(d["delta"]) for d in weight_diffs.values())
        logger.info(f"Review {ticket_id} submitted, total_delta={total_delta:.4f}")
        return ticket_id

    async def approve(self, ticket_id: str, approver: str) -> bool:
        review = self._pending.get(ticket_id)
        if not review or review["status"] != "pending":
            return False
        new_weights = {n: d["new"] for n, d in review["diffs"].items()}
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute("UPDATE qr_weights_history SET is_active = FALSE")
            await conn.execute(
                "INSERT INTO qr_weights_history (source, applied_at, approver, payload, is_active) "
                "VALUES ('manual', NOW(), $1, $2::jsonb, TRUE)",
                approver, json.dumps({"weights": new_weights}),
            )
        await bump_weights_version(self._pool)
        review["status"] = "approved"
        review["approver"] = approver
        return True

    async def reject(self, ticket_id: str, approver: str, reason: str) -> bool:
        review = self._pending.get(ticket_id)
        if not review or review["status"] != "pending":
            return False
        review["status"] = "rejected"
        review["approver"] = approver
        review["reason"] = reason
        return True

    async def _expire_review(self, ticket_id: str):
        await asyncio.sleep(self._timeout)
        review = self._pending.get(ticket_id)
        if review and review["status"] == "pending":
            await self.reject(ticket_id, "system:timeout", "auto-rejected after timeout")
            logger.warning(f"Review {ticket_id} auto-rejected after timeout")

    @staticmethod
    def _format_report(weight_diffs, evaluation) -> str:
        total_delta = sum(abs(d["delta"]) for d in weight_diffs.values())
        lines = [f"=== 策略权重调整审核报告 (total_delta={total_delta:.4f}) ==="]
        for n, d in weight_diffs.items():
            lines.append(f"  {n}: {d['old']:.3f} → {d['new']:.3f} (Δ={d['delta']:+.3f})")
        return "\n".join(lines)
```

### Step 4.4: 重新运行测试

Run:
```bash
cd /Users/Ray/TraeProjects/SPMA
pytest tests/unit/agents/supervisor/test_human_validator.py -v
```

Expected: 5 passed

### Step 4.5: 提交

```bash
cd /Users/Ray/TraeProjects/SPMA
git add src/spma/agents/supervisor/human_validator.py tests/unit/agents/supervisor/test_human_validator.py
git commit -m "feat(qr): HumanInTheLoopValidator — 完整工单流程(G10)

主文件 ADR-006:触发→工单→审核→应用/拒绝 + 24h 超时自动拒绝。
approve 写 is_active=TRUE 新快照 + bump_weights_version(P8 信号源)。
默认拒绝策略:超时 = 不应用新权重。

Refs: SPMA-design-11-phase6 §3.4"
```

---

## Task 5: `graph.py` 集成 4 个组件 + 24h 灰度

### Step 5.1: 集成到 `graph.py`

在 `src/spma/agents/supervisor/graph.py` 启动时调用 `install_qr_metrics_bridge`,创建 `StableStrategyEvaluator` / `DistributionShiftDetector` / `HumanInTheLoopValidator` 单例(供后台批处理任务使用)。

### Step 5.2: 24h 灰度

| 监控项 | 期望 |
|--------|------|
| 13+ 原单测 + 14 个新单测(1+4+4+5) | 全过 |
| `qr_fallback_total` 指标有值 | > 0(说明 bridge 生效) |
| EMA 收敛(50 case 仿真) | 偏差 < 0.05 |
| MMD 检测准确度(离线数据集) | ≥ 90% |
| 审核超时自动拒绝 | 100% |

### Step 5.3: 关闭 P6

```bash
cd /Users/Ray/TraeProjects/SPMA
git add docs/designs/SPMA-design-11-query-rewrite-optimization-v2-final.md src/spma/agents/supervisor/graph.py
git commit -m "docs(qr): G8/G9/G10 标记为已修复(P6 完成)"
```

---

## 验收 checklist

- [ ] Task 1:1 个 bridge 单测通过
- [ ] Task 2:4 个 evaluator 单测通过
- [ ] Task 3:4 个 shift_detector 单测通过
- [ ] Task 4:5 个 human_validator 单测通过
- [ ] Task 5:24h 灰度无 P0 故障
- [ ] 主文件 §1.1 G8/G9/G10 标记为已修复

---

## 失败回滚

```bash
git revert <commit_hash_of_task_N>
# 4 个新文件,直接回滚;qr_state 已有表不受影响
```
