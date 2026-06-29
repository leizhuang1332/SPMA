# Design: Query Rewriter Phase 6 — 反馈闭环 + 监控(基于已有 qr_metrics)

> **总览与索引**:[SPMA-design-11-query-rewrite-optimization-v2-final.md](SPMA-design-11-query-rewrite-optimization-v2-final.md) §1.1 中 G8 / G9 / G10
>
> **本文档角色**:8 份子 spec 中的第 6 份(Phase 6),gap-driven 结构。
> **上下游依赖**:**上游** [P3 (指代)](SPMA-design-11-phase3-multi-strategy-resolution.md) + [P4 (扩展)](SPMA-design-11-phase4-multi-strategy-expansion.md) + [P5 (分解)](SPMA-design-11-phase5-multi-strategy-decomposition.md) 多路策略;**下游** P8 消费权重变更信号。
> **预估工时**:2 周

---

## 0. 元信息

| 字段 | 值 |
|------|---|
| 状态 | 待开始 |
| 负责人 | TBD |
| 优先级 | 🟡 P1 |
| 关联缺陷 | G8 / G9 / G10 |
| 关联文件 | `src/spma/observability/qr_metrics.py` (已实现 8 项) + 新增 3 个类 |
| 预估工时 | 2 周 |
| 相关 ADR | ADR-005(MMD)、ADR-006(HITL) |

---

## 1. 现状核查(实际代码)

### 1.1 Prometheus 8 项指标**已实现**

`src/spma/observability/qr_metrics.py:58-111` 已实现 `QrMetrics` dataclass + `build_qr_metrics()` 工厂:

| 常量 | Prometheus 名称 | 含义 |
|------|----------------|------|
| `COUNTER_CACHE_REQUESTS` | `qr_cache_requests_total` | 缓存请求数 |
| `COUNTER_CACHE_ERRORS` | `qr_cache_errors_total` | 缓存错误数 |
| `HISTOGRAM_CACHE_LATENCY` | `qr_cache_latency_seconds` | 缓存延迟 |
| `HISTOGRAM_CACHE_L2_DISTANCE` | `qr_cache_l2_distance` | L2 余弦距离 |
| `COUNTER_FALLBACK` | `qr_fallback_total` | 降级触发(已有 label=level, stage) |
| `GAUGE_WEIGHT_VERSION` | `qr_state_weight_version` | 当前权重版本 |
| `GAUGE_FLUSH_LAG` | `qr_audit_flush_lag_seconds` | 审计 buffer 延迟 |
| `GAUGE_CACHE_HIT_RATIO` | `qr_cache_hit_ratio` | 1 分钟滚动命中率 |

### 1.2 状态变更回调已支持

`src/spma/infrastructure/circuit_breaker.py:207-219`:

```python
def set_default_state_change_callback(callback):
    """设置全局默认的熔断器状态变更回调"""
    ...
```

**未**接入:目前没有把 metrics 注册为回调,CB 状态变化未触发指标上报。

### 1.3 **未**实现的部分(G8 / G9 / G10)

| 缺陷 | 描述 | 状态 |
|------|------|------|
| **G8** 🟡 | 离线评估器 + EMA 权重进化 | 无 `StableStrategyEvaluator` |
| **G9** 🟡 | 分布漂移检测(MMD) | 无 `DistributionShiftDetector` |
| **G10** 🟡 | 人工审核闭环(HITL) | 无 `HumanInTheLoopValidator` |

**关键洞察**:P6 实际是**纯新增** — 监控基础设施已齐,缺的是"基于监控数据做决策"的 3 个组件。

---

## 2. 差距分析(目标 vs 现实)

| 目标 | 现实 | 差距 |
|------|------|------|
| 8 项指标已注册 | ✅ 已有 | **无差距** |
| CB 状态变更驱动 metrics | 回调未挂 metrics | **本 Phase 接入** |
| 离线评估 | 无 | **新增 `StableStrategyEvaluator`** |
| EMA 权重进化 | 无 | **包含在 `StableStrategyEvaluator`** |
| 分布漂移检测 | 无 | **新增 `DistributionShiftDetector`** |
| 人工审核闭环 | 无 | **新增 `HumanInTheLoopValidator`** |
| 权重写入 `qr_weights_history` | ✅ `qr_state.write_weights_snapshot` 已有 | **无差距** |
| `bump_weights_version` | ✅ 已有 | **无差距** |

**关键洞察**:P6 实际只需:
1. 把 `QrMetrics` 接入 `set_default_state_change_callback`(1 行)
2. 新增 3 个类(`StableStrategyEvaluator` / `DistributionShiftDetector` / `HumanInTheLoopValidator`)

---

## 3. 详细设计

### 3.1 CB 状态变更 → metrics 接入

`src/spma/agents/supervisor/qr_metrics_bridge.py` (新建):

```python
"""桥接 CB 状态变更 → Prometheus 指标。"""
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

**集成位置**:`graph.py` 启动时调用一次:
```python
install_qr_metrics_bridge(qr_metrics_instance)
```

### 3.2 `StableStrategyEvaluator`

`src/spma/agents/supervisor/strategy_evaluator.py` (新建):

```python
"""离线策略评估 + EMA 权重进化(主文件 ADR-006 边界约束 + 主文件 §3.5)。"""
import json
import logging
import asyncio
from datetime import datetime

from spma.agents.supervisor.qr_state import write_weights_snapshot, bump_weights_version

logger = logging.getLogger(__name__)


class StableStrategyEvaluator:
    """带 EMA 和 min_weight 约束的离线评估器。"""

    def __init__(self, db_pool, ema_alpha: float = 0.1, min_weight: float = 0.1):
        self._pool = db_pool
        self._ema_alpha = ema_alpha
        self._min_weight = min_weight

    async def evaluate_and_propose(
        self,
        test_cases: list[dict],
        current_weights: dict[str, float],
        evaluator,  # async (case) -> {strategy_name: score}
    ) -> dict:
        """评估 + 提出新权重(写入 qr_weights_history 但不应用)。

        Returns:
            {
                "evaluation": {strategy_name: avg_score, ...},
                "weight_diffs": {strategy_name: {"old": x, "new": y, "delta": z}},
                "snapshot_id": int,  # qr_weights_history 写入 ID
                "should_review": bool,  # 总 delta > 阈值需人工审核
            }
        """
        # 1) 评估每个 case
        results: dict[str, list[float]] = {n: [] for n in current_weights}
        for case in test_cases:
            scores = await evaluator(case)
            for n, s in scores.items():
                if n in results:
                    results[n].append(s)

        eval_summary = {
            n: {"avg_score": sum(s) / len(s) if s else 0.0, "count": len(s)}
            for n, s in results.items()
        }

        # 2) 计算 EMA 平滑后的新权重
        diffs = {}
        for n, current_w in current_weights.items():
            target_w = eval_summary[n]["avg_score"]
            new_w = (1 - self._ema_alpha) * current_w + self._ema_alpha * target_w
            new_w = max(self._min_weight, new_w)
            diffs[n] = {"old": current_w, "new": new_w, "delta": new_w - current_w}

        # 归一化(权重和 = 1)
        total = sum(d["new"] for d in diffs.values())
        if total > 0:
            for d in diffs.values():
                d["new"] /= total

        # 3) 写入 qr_weights_history(不激活,等人工审核)
        snapshot_id = await write_weights_snapshot(
            self._pool,
            payload={"weights": {n: d["new"] for n, d in diffs.items()}},
            source="ema",
            applied_at=None,  # 关键:不应用
        )

        # 4) 是否需要人工审核
        total_delta = sum(abs(d["delta"]) for d in diffs.values())
        should_review = total_delta > 0.1  # 阈值 0.1

        return {
            "evaluation": eval_summary,
            "weight_diffs": diffs,
            "snapshot_id": snapshot_id,
            "should_review": should_review,
        }
```

### 3.3 `DistributionShiftDetector`

`src/spma/agents/supervisor/shift_detector.py` (新建):

```python
"""分布漂移检测——基于 MMD + 采样相似度分布(主文件 ADR-005)。"""
import asyncio
import random
import logging
import numpy as np

logger = logging.getLogger(__name__)


class DistributionShiftDetector:
    """MMD 漂移检测(替代 v3.0 错误的"embedding 均值差")。"""

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

    def _compute_mmd(self, X, Y, sigma=1.0) -> float:
        def gaussian(A, B):
            d = np.sum(A ** 2, axis=1)[:, None] + np.sum(B ** 2, axis=1)[None, :] - 2 * A @ B.T
            return np.exp(-d / (2 * sigma ** 2))
        Kxx = gaussian(X, X)
        Kyy = gaussian(Y, Y)
        Kxy = gaussian(X, Y)
        np.fill_diagonal(Kxx, 0)
        np.fill_diagonal(Kyy, 0)
        n, m = len(X), len(Y)
        return float(Kxx.sum() / (n * (n - 1)) + Kyy.sum() / (m * (m - 1)) - 2 * Kxy.mean())

    def _pairwise_sim(self, embs: np.ndarray) -> np.ndarray:
        n = min(len(embs), 100)
        idx = np.random.choice(len(embs), n, replace=False)
        sampled = embs[idx]
        normed = sampled / (np.linalg.norm(sampled, axis=1, keepdims=True) + 1e-10)
        sims = normed @ normed.T
        return sims[np.triu_indices(n, k=1)]
```

### 3.4 `HumanInTheLoopValidator`

`src/spma/agents/supervisor/human_validator.py` (新建):

```python
"""人工审核闭环(主文件 ADR-006:完整工单流程 + 超时自动拒绝)。"""
import asyncio
import logging
import time
import uuid

logger = logging.getLogger(__name__)


class HumanInTheLoopValidator:
    """权重变更工单化:触发 → 工单创建 → 审核 → 应用/拒绝。"""

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
        logger.info(f"Review {ticket_id} submitted, total_delta={sum(abs(d['delta']) for d in weight_diffs.values()):.4f}")
        return ticket_id

    async def approve(self, ticket_id: str, approver: str) -> bool:
        from spma.agents.supervisor.qr_state import bump_weights_version
        review = self._pending.get(ticket_id)
        if not review or review["status"] != "pending":
            return False
        # 应用新权重:激活已有 snapshot(此处简化为 bump version + 写 is_active=TRUE)
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

### 3.5 集成到 `graph.py`

```python
# graph.py 启动时
from spma.agents.supervisor.qr_metrics_bridge import install_qr_metrics_bridge
install_qr_metrics_bridge(qr_metrics_instance)

# 后台批处理
_evaluator = StableStrategyEvaluator(db_pool)
_shift_detector = DistributionShiftDetector(embedder)
_human_validator = HumanInTheLoopValidator(db_pool, ticket_client=...)
```

---

## 4. 与上游/下游 spec 的接口契约

### 4.1 新增文件

| 文件 | 改动 |
|------|------|
| `src/spma/agents/supervisor/qr_metrics_bridge.py` | CB→metrics 桥接 |
| `src/spma/agents/supervisor/strategy_evaluator.py` | `StableStrategyEvaluator` |
| `src/spma/agents/supervisor/shift_detector.py` | `DistributionShiftDetector` |
| `src/spma/agents/supervisor/human_validator.py` | `HumanInTheLoopValidator` |

### 4.2 不需要做的事

- **不**重新实现 Prometheus 指标(`qr_metrics.py` 已完整)
- **不**重新实现 CB 回调机制(`infrastructure/circuit_breaker.py` 已完整)
- **不**重新实现 `qr_state`(`qr_weights_history` 写、`bump_weights_version` 都已存在)

### 4.3 下游契约

[P8](SPMA-design-11-phase8-rollout-and-rollback.md) 消费 `human_validator.approve()` 触发的 `bump_weights_version()` 信号。

### 4.4 配置 Key

| Key | 默认 | 说明 |
|-----|------|------|
| `QR_EVAL_EMA_ALPHA` | 0.1 | EMA 平滑系数 |
| `QR_EVAL_MIN_WEIGHT` | 0.1 | 策略最小权重 |
| `QR_EVAL_REVIEW_THRESHOLD` | 0.1 | 人工审核触发阈值 |
| `QR_EVAL_REVIEW_TIMEOUT` | 86400 | 审核超时(秒) |
| `QR_SHIFT_MMD_THRESHOLD` | 0.05 | MMD 漂移阈值 |

---

## 5. 验收标准

| ID | 指标 | 当前 | 验收 | 测量 |
|----|------|------|------|------|
| V1 | 4 个新文件存在 | ❌ | ✅ | `ls` 检查 |
| V2 | CB 状态变更触发 `qr_fallback_total` 指标 | ❌ | ✅ | 注入测试 |
| V3 | 13 原单测无回归 | 13/13 | 13/13 | pytest |
| V4 | 新增 ≥ 15 单测(3 类 + bridge) | 0 | ≥ 15 | pytest |
| V5 | EMA 权重收敛 | N/A | 50 case 后偏差 < 0.05 | 离线模拟 |
| V6 | MMD 检测准确度 | N/A | ≥ 90% | 离线数据集 |
| V7 | 人工审核超时自动拒绝 | N/A | 100% | 注入测试 |

---

## 6. 风险与降级

| 风险 | 触发 | 影响 | 缓解 |
|------|------|------|------|
| **R1**:MMD 计算慢 | n=200 | ~200ms 评估延迟 | 非热路径,可接受 |
| **R2**:工单系统挂 | Jira/Lark 故障 | 审核流阻塞 | 本地 `_pending` 暂存 |
| **R3**:权重持久化失败 | DB 故障 | 重启丢权重 | 启动时从 `qr_weights_history.is_active=TRUE` 恢复 |
| **R4**:bridge 回调异常 | metrics 挂 | CB 状态变更不报 | `try/except` 隔离 |

---

## 7. 实施步骤

### 7.1 PR 切分(4 个 PR)

**PR #1**:`qr_metrics_bridge` + 接入 `graph.py`
**PR #2**:`StableStrategyEvaluator` + 单测
**PR #3**:`DistributionShiftDetector` + 单测
**PR #4**:`HumanInTheLoopValidator` + 单测

### 7.2 时间表

| 工作日 | 任务 | 产出 |
|--------|------|------|
| D1-D2 | bridge + 接入 | PR #1 ready |
| D3 | Review PR #1 + 合并 | - |
| D3-D4 | `StableStrategyEvaluator` | PR #2 ready |
| D5 | Review PR #2 + 合并 | - |
| D5-D6 | `DistributionShiftDetector` | PR #3 ready |
| D7 | Review PR #3 + 合并 | - |
| D7-D8 | `HumanInTheLoopValidator` | PR #4 ready |
| D9 | Review PR #4 + 合并 | - |

### 7.3 上线 checklist

- [ ] PR #1-4 合并
- [ ] 13 原单测无回归
- [ ] 15+ 新单测全过
- [ ] 24h 灰度验证 CB→metrics 链路

---

## 8. 变更日志

| 日期 | 版本 | 变更 |
|------|------|------|
| 2026-06-29 | 1.0 | **gap-driven 重写**:复用已有 `qr_metrics` 8 项 + `qr_state` 权重管理,新增 4 个文件 |
| 2026-06-29 | 0.9 | (回退)初次拆分(已回退) |
