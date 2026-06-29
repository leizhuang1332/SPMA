# Design: Query Rewriter Phase 8 — 灰度放量 + KILL SWITCH(基于已有 qr_state)

> **总览与索引**:[SPMA-design-11-query-rewrite-optimization-v2-final.md](SPMA-design-11-query-rewrite-optimization-v2-final.md) §1.1 中 G15
>
> **本文档角色**:8 份子 spec 中的第 8 份(Phase 8),gap-driven 结构。
> **上下游依赖**:**上游** [P6 (HITL 审核流)](SPMA-design-11-phase6-feedback-and-monitoring.md) + [P7 (生产加固)](SPMA-design-11-phase7-production-hardening.md);**下游** 无。
> **预估工时**:1 周

---

## 0. 元信息

| 字段 | 值 |
|------|---|
| 状态 | 待开始 |
| 负责人 | TBD |
| 优先级 | 🟡 P1 |
| 关联缺陷 | G15 |
| 关联文件 | 2 个新文件 + 复用 `qr_state.py` |
| 预估工时 | 1 周 |
| 相关 ADR | ADR-010(冷启动 Shadow 流量) |

---

## 1. 现状核查(实际代码)

### 1.1 `qr_state` 已实现权重版本管理

`src/spma/agents/supervisor/qr_state.py:57-91`:

| 已实现 | API |
|--------|-----|
| 权重版本号自增 | `await bump_weights_version(pool) -> int` |
| synonym 版本号自增 | `await bump_synonym_version(pool) -> int` |
| 写权重快照(自动取消旧 active) | `await write_weights_snapshot(pool, payload, source, ...) -> int` |
| 读当前版本 | `await get_versions(pool) -> (int, int)` |
| 缓存 key 构造 | `build_cache_key(...)` |

### 1.2 仍**未**实现

| 缺陷 | 描述 | 关联 |
|------|------|------|
| **G15** 🟡 | `StrategyFeatureFlag`(秒级 KILL SWITCH) | 无 |
| **G15** 🟡 | `RollbackManager`(回滚到任一历史版本) | 无(`write_weights_snapshot` 可写但回滚流程未实现) |
| **G15** 🟡 | 冷启动 5 阶段流程(Shadow / 1% / 10% / 50% / 100%) | 无 |

**关键洞察**:P8 实际只缺 2 个组件(`StrategyFeatureFlag` + `RollbackManager`)+ 1 个流程编排。`qr_state` 已提供底层能力。

---

## 2. 差距分析(目标 vs 现实)

| 目标 | 现实 | 差距 |
|------|------|------|
| 策略级 feature flag | 无 | **新增 `StrategyFeatureFlag`** |
| 秒级 KILL SWITCH | 无 | **`StrategyFeatureFlag.is_enabled()` + Redis 5s 缓存** |
| 冷启动 5 阶段 | 无 | **`StrategyFeatureFlag` 配合 rollout_pct** |
| 版本快照写入 | ✅ `qr_state.write_weights_snapshot` | **无差距** |
| 版本快照回滚 | 无(只能写新) | **新增 `RollbackManager.rollback_to()`** |
| 1 分钟内回滚 | 无 | **`RollbackManager` 读历史 + 写新 active** |

---

## 3. 详细设计

### 3.1 `StrategyFeatureFlag`

`src/spma/agents/supervisor/feature_flag.py` (新建):

```python
"""策略级 feature flag,支持秒级 KILL SWITCH(主文件 §3.8 + ADR-010)。"""
import json
import logging
import time

logger = logging.getLogger(__name__)


class StrategyFeatureFlag:
    """策略级 feature flag。

    - Redis 存 `{enabled: bool, rollout_pct: 0-100}`
    - 本地缓存 5s,避免每请求打 Redis
    - 运维调整通过 set_rollout(),清空本地缓存 5s 内全网生效
    """

    def __init__(self, redis_client, local_cache_ttl: int = 5):
        self._redis = redis_client
        self._cache: dict[str, tuple[bool, float]] = {}  # key → (enabled, expires_at)
        self._ttl = local_cache_ttl

    async def is_enabled(self, strategy_name: str, user_bucket: int | None = None) -> bool:
        cache_key = f"{strategy_name}:{user_bucket}"
        now = time.time()
        if cache_key in self._cache:
            enabled, expires_at = self._cache[cache_key]
            if expires_at > now:
                return enabled

        flag = await self._redis.get(f"flag:qr:{strategy_name}")
        if not flag:
            enabled = True  # 默认开启
        else:
            config = json.loads(flag)
            enabled = config.get("enabled", True)
            if user_bucket is not None and "rollout_pct" in config:
                enabled = enabled and (user_bucket < config["rollout_pct"])

        self._cache[cache_key] = (enabled, now + self._ttl)
        return enabled

    async def set_rollout(self, strategy_name: str, *, rollout_pct: int, enabled: bool = True):
        """运维接口:秒级调整灰度比例。"""
        await self._redis.set(
            f"flag:qr:{strategy_name}",
            json.dumps({"enabled": enabled, "rollout_pct": rollout_pct}),
        )
        self._cache.clear()  # 立即清空本地缓存
        logger.info(f"FeatureFlag {strategy_name}: enabled={enabled}, rollout_pct={rollout_pct}")

    def user_bucket(self, user_id: str) -> int:
        """把 user_id 映射到 0-99 的桶(同一用户始终同一组)。"""
        return hash(user_id) % 100
```

### 3.2 `RollbackManager`

`src/spma/agents/supervisor/rollback_manager.py` (新建):

```python
"""权重快照回滚:从历史快照读出并激活(主文件 §3.8)。"""
import json
import logging
from datetime import datetime

from spma.agents.supervisor.qr_state import bump_weights_version

logger = logging.getLogger(__name__)


class RollbackManager:
    """读历史快照 + 写新 active 记录(1 分钟内回滚到任意版本)。"""

    def __init__(self, db_pool, max_versions: int = 10):
        self._pool = db_pool
        self._max = max_versions

    async def list_versions(self) -> list[dict]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT weights_set_id, created_at, source, approver, payload "
                "FROM qr_weights_history ORDER BY created_at DESC LIMIT $1",
                self._max,
            )
            return [dict(r) for r in rows]

    async def rollback_to(self, weights_set_id: int, *, approver: str) -> bool:
        """回滚到指定历史版本。

        步骤:
        1. 读历史 payload
        2. 取消当前 active
        3. 写新 active(来源标记为 'rollback')
        4. bump_weights_version
        """
        async with self._pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                "SELECT payload FROM qr_weights_history WHERE weights_set_id = $1",
                weights_set_id,
            )
            if not row:
                logger.error(f"RollbackManager: version {weights_set_id} not found")
                return False

            payload = row["payload"]
            await conn.execute("UPDATE qr_weights_history SET is_active = FALSE")
            await conn.execute(
                "INSERT INTO qr_weights_history (source, applied_at, approver, payload, is_active) "
                "VALUES ('rollback', NOW(), $1, $2::jsonb, TRUE)",
                approver, payload,
            )
        # 自增版本号,触发下游缓存失效
        new_v = await bump_weights_version(self._pool)
        logger.warning(f"Rolled back to version {weights_set_id}, new weights_version={new_v}, approver={approver}")
        return True
```

### 3.3 冷启动 5 阶段流程

`src/spma/agents/supervisor/canary.py` (新建,**流程脚本**):

```python
"""冷启动 5 阶段流程编排(主文件 ADR-010:Shadow → 1% → 10% → 50% → 100%)。

不引入新存储,使用 feature_flag.set_rollout()。
阶段判定由人工 review Prometheus 指标 + 灰度控制台。
"""
import logging

from spma.agents.supervisor.feature_flag import StrategyFeatureFlag

logger = logging.getLogger(__name__)


CANARY_STAGES = [
    ("shadow", 0),       # 新策略只打分不返回
    ("one_percent", 1),
    ("ten_percent", 10),
    ("fifty_percent", 50),
    ("hundred_percent", 100),
]


class CanaryRelease:
    """灰度放量编排。手动驱动阶段切换,自动记录每次切换。"""

    def __init__(self, feature_flag: StrategyFeatureFlag, audit_logger):
        self._flag = feature_flag
        self._audit = audit_logger

    async def advance(self, strategy_name: str, stage: str, *, operator: str):
        if stage not in dict(CANARY_STAGES):
            raise ValueError(f"Unknown stage: {stage}")
        rollout_pct = dict(CANARY_STAGES)[stage]
        await self._flag.set_rollout(strategy_name, rollout_pct=rollout_pct, enabled=True)
        await self._audit.log(
            request_id=f"canary-{strategy_name}-{stage}",
            original_query="<canary-advance>",
            rewritten=None,
            strategies_hit=[f"canary:{stage}"],
            weights_snapshot={"strategy": strategy_name, "rollout_pct": rollout_pct},
            latency_ms=0,
        )
        logger.info(f"Canary {strategy_name} → {stage} ({rollout_pct}%) by {operator}")

    async def halt(self, strategy_name: str, *, operator: str, reason: str):
        """KILL SWITCH:5 秒内全网关闭。"""
        await self._flag.set_rollout(strategy_name, rollout_pct=0, enabled=False)
        await self._audit.log(
            request_id=f"canary-halt-{strategy_name}",
            original_query=f"<canary-halt:{reason}>",
            rewritten=None,
            strategies_hit=["canary:halt"],
            weights_snapshot={"strategy": strategy_name, "rollout_pct": 0},
            latency_ms=0,
        )
        logger.warning(f"Canary {strategy_name} HALTED by {operator}: {reason}")
```

### 3.4 集成到 `graph.py`

```python
# graph.py 启动时
_feature_flag = StrategyFeatureFlag(redis_client)
_rollback = RollbackManager(db_pool)
_canary = CanaryRelease(_feature_flag, audit_logger)

# 策略调用前(由 P2 编排器包装)
async def rewrite_node(state):
    if not await _feature_flag.is_enabled("multi_strategy_rewrite",
                                          user_bucket=_feature_flag.user_bucket(user_id)):
        # KILL SWITCH 命中,降级到单策略
        return await _single_strategy_rewrite(state)
    ...
```

### 3.5 运维 API(可选)

`src/spma/api/routes/canary.py` (新建):

```python
"""Canary 灰度控制 API(运维调用)。"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel

router = APIRouter(prefix="/api/canary", tags=["canary"])


class AdvanceRequest(BaseModel):
    strategy_name: str
    stage: str  # shadow / one_percent / ...
    operator: str


class RollbackRequest(BaseModel):
    weights_set_id: int
    approver: str


@router.post("/advance")
async def canary_advance(req: AdvanceRequest, canary: CanaryRelease = Depends(...)):
    await canary.advance(req.strategy_name, req.stage, operator=req.operator)
    return {"status": "ok"}


@router.post("/halt")
async def canary_halt(strategy_name: str, operator: str, reason: str,
                      canary: CanaryRelease = Depends(...)):
    await canary.halt(strategy_name, operator=operator, reason=reason)
    return {"status": "halted"}


@router.post("/rollback")
async def rollback(req: RollbackRequest, rollback_mgr: RollbackManager = Depends(...)):
    success = await rollback_mgr.rollback_to(req.weights_set_id, approver=req.approver)
    return {"status": "ok" if success else "version_not_found"}


@router.get("/versions")
async def list_versions(rollback_mgr: RollbackManager = Depends(...)):
    return await rollback_mgr.list_versions()
```

---

## 4. 与上游/下游 spec 的接口契约

### 4.1 新增文件

| 文件 | 改动 |
|------|------|
| `src/spma/agents/supervisor/feature_flag.py` | `StrategyFeatureFlag` |
| `src/spma/agents/supervisor/rollback_manager.py` | `RollbackManager` |
| `src/spma/agents/supervisor/canary.py` | `CanaryRelease` 5 阶段编排 |
| `src/spma/api/routes/canary.py` | 运维 API(可选) |

### 4.2 不需要做的事

- **不**重新实现版本号管理(`qr_state` 已实现)
- **不**重新实现权重快照写入(`qr_state.write_weights_snapshot` 已实现)

### 4.3 下游契约

无。

### 4.4 配置 Key

| Key | 默认 | 说明 |
|-----|------|------|
| `QR_FLAG_LOCAL_CACHE_TTL` | 5 | 本地缓存 TTL(秒) |
| `QR_ROLLBACK_MAX_VERSIONS` | 10 | 保留历史版本数 |

---

## 5. 验收标准

| ID | 指标 | 当前 | 验收 | 测量 |
|----|------|------|------|------|
| V1 | 3 个新文件存在 | ❌ | ✅ | `ls` |
| V2 | 13 原单测无回归 | 13/13 | 13/13 | pytest |
| V3 | 新增 ≥ 10 单测(3 类) | 0 | ≥ 10 | pytest |
| V4 | KILL SWITCH 生效时间 | N/A | ≤ 5s (本地缓存过期) | 注入测试 |
| V5 | 回滚时间 | N/A | ≤ 1min | 注入测试 |
| V6 | 灰度分桶一致性 | N/A | 100% (同 user_id 始终同组) | 1000 用户重复请求 |
| V7 | 运维 API 可调用 | ❌ | ✅ | curl /api/canary/versions |

---

## 6. 风险与降级

| 风险 | 触发 | 影响 | 缓解 |
|------|------|------|------|
| **R1**:Redis 延迟 | 网络抖动 | 灰度变更延迟 5s+ | 本地缓存 TTL 5s 已限制 |
| **R2**:历史快照被清 | Redis 数据丢失 | 无法回滚 | 启动时检查 + 报警 |
| **R3**:本地缓存长期不刷新 | Redis 断开 | flag 状态陈旧 | TTL 自然过期 |
| **R4**:运维误操作 | 误调 halt | 用户受影响 | 5s 可恢复 + 审计日志 |

---

## 7. 实施步骤

### 7.1 PR 切分(4 个 PR)

**PR #1**:`StrategyFeatureFlag` + 单测
**PR #2**:`RollbackManager` + 单测
**PR #3**:`CanaryRelease` + 单测
**PR #4**:运维 API(`api/routes/canary.py`)

### 7.2 时间表

| 工作日 | 任务 | 产出 |
|--------|------|------|
| D1 | `StrategyFeatureFlag` + 单测 | PR #1 ready |
| D2 | Review PR #1 + 合并 | - |
| D2 | `RollbackManager` + 单测 | PR #2 ready |
| D3 | Review PR #2 + 合并 | - |
| D3-D4 | `CanaryRelease` + 单测 | PR #3 ready |
| D5 | Review PR #3 + 合并 | - |
| D5 | 运维 API | PR #4 ready |
| D6 | Review PR #4 + 合并 | - |

### 7.3 上线 checklist

- [ ] PR #1-4 合并
- [ ] 13 原单测无回归
- [ ] 10+ 新单测全过
- [ ] KILL SWITCH 演练:5s 内生效
- [ ] 回滚演练:1min 内回滚
- [ ] 运维 runbook 更新

---

## 8. 变更日志

| 日期 | 版本 | 变更 |
|------|------|------|
| 2026-06-29 | 1.0 | **gap-driven 重写**:复用 `qr_state.bump_weights_version` / `write_weights_snapshot`,新增 3 个文件 |
| 2026-06-29 | 0.9 | (回退)初次拆分(已回退) |
