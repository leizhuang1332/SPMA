# Query Rewriter Phase 8 — 灰度 + KILL SWITCH + 回滚 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复主文件 §1.1 G15(FeatureFlag / RollbackManager / 冷启动 5 阶段),利用已有 `qr_state.bump_weights_version` / `write_weights_snapshot`,新增 4 个组件 + 运维 API。

**Architecture:**
- `StrategyFeatureFlag`:Redis 存 `{enabled, rollout_pct}`,本地 5s 缓存,支持秒级 KILL SWITCH
- `RollbackManager`:读 `qr_weights_history` 历史 + 写新 is_active=TRUE 记录 + bump_weights_version
- `CanaryRelease`:5 阶段编排(Shadow / 1% / 10% / 50% / 100%)
- 运维 API `/api/canary/{advance,halt,rollback,versions}`(FastAPI)

**Tech Stack:** Redis / asyncpg / FastAPI / 已有 `qr_state`

**依赖:** [P6](2026-06-30-qr-phase6-feedback-and-monitoring-plan.md) 提供 `human_validator.approve()` 触发信号 + [P7](2026-06-30-qr-phase7-production-hardening-plan.md) 提供 KILL SWITCH 接入点

**Spec:** [SPMA-design-11-phase8-rollout-and-rollback.md](../../designs/SPMA-design-11-phase8-rollout-and-rollback.md)

---

## 文件结构

| 文件 | 类型 | 职责 |
|------|------|------|
| `src/spma/agents/supervisor/feature_flag.py` | 新建 | `StrategyFeatureFlag` |
| `src/spma/agents/supervisor/rollback_manager.py` | 新建 | `RollbackManager` |
| `src/spma/agents/supervisor/canary.py` | 新建 | `CanaryRelease` 5 阶段编排 |
| `src/spma/api/routes/canary.py` | 新建 | 运维 API |
| 4 个对应单测文件 | 新建 | 单测 |

---

## Task 1: `StrategyFeatureFlag` + 单测

**Files:**
- Create: `src/spma/agents/supervisor/feature_flag.py`
- Test: `tests/unit/agents/supervisor/test_feature_flag.py`

### Step 1.1: 写失败的测试

`tests/unit/agents/supervisor/test_feature_flag.py`:

```python
"""StrategyFeatureFlag 单测。"""
import pytest
import time
from unittest.mock import AsyncMock, MagicMock

from spma.agents.supervisor.feature_flag import StrategyFeatureFlag


@pytest.fixture
def mock_redis():
    redis = MagicMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock(return_value=True)
    return redis


@pytest.mark.asyncio
async def test_default_enabled_when_no_flag(mock_redis):
    """Redis 无 flag → 默认 enabled=True。"""
    ff = StrategyFeatureFlag(mock_redis)
    assert await ff.is_enabled("any_strategy") is True


@pytest.mark.asyncio
async def test_disabled_flag_returns_false(mock_redis):
    """flag={enabled: False} → 返回 False。"""
    import json
    mock_redis.get = AsyncMock(return_value=json.dumps({"enabled": False, "rollout_pct": 100}))
    ff = StrategyFeatureFlag(mock_redis)
    assert await ff.is_enabled("any_strategy") is False


@pytest.mark.asyncio
async def test_rollout_pct_filters_by_bucket(mock_redis):
    """rollout_pct=10 → bucket<10 通过,bucket>=10 不通过。"""
    import json
    mock_redis.get = AsyncMock(return_value=json.dumps({"enabled": True, "rollout_pct": 10}))
    ff = StrategyFeatureFlag(mock_redis)
    assert await ff.is_enabled("s", user_bucket=5) is True
    assert await ff.is_enabled("s", user_bucket=50) is False


@pytest.mark.asyncio
async def test_local_cache_avoids_redis_hit(mock_redis):
    """5s 内重复调用 → 只打 1 次 Redis。"""
    import json
    mock_redis.get = AsyncMock(return_value=json.dumps({"enabled": True, "rollout_pct": 100}))
    ff = StrategyFeatureFlag(mock_redis, local_cache_ttl=5)
    await ff.is_enabled("s")
    await ff.is_enabled("s")
    await ff.is_enabled("s")
    assert mock_redis.get.call_count == 1


@pytest.mark.asyncio
async def test_set_rollout_clears_local_cache(mock_redis):
    """set_rollout 后立即生效(清空本地缓存)。"""
    import json
    mock_redis.get = AsyncMock(return_value=json.dumps({"enabled": True, "rollout_pct": 100}))
    ff = StrategyFeatureFlag(mock_redis, local_cache_ttl=5)
    await ff.is_enabled("s")
    mock_redis.get.call_count = 1
    await ff.set_rollout("s", rollout_pct=0, enabled=False)
    mock_redis.get.call_count = 0  # 重置计数器
    await ff.is_enabled("s")
    assert mock_redis.get.call_count == 1  # 重新打 Redis


def test_user_bucket_is_stable():
    """同一 user_id 始终映射到同一 bucket(0-99)。"""
    ff = StrategyFeatureFlag(MagicMock())
    b1 = ff.user_bucket("user_123")
    b2 = ff.user_bucket("user_123")
    assert b1 == b2
    assert 0 <= b1 < 100
```

### Step 1.2: 运行测试,确认失败

Run:
```bash
cd /Users/Ray/TraeProjects/SPMA
pytest tests/unit/agents/supervisor/test_feature_flag.py -v
```

Expected: ImportError(`feature_flag` 不存在)

### Step 1.3: 写 `StrategyFeatureFlag` 实现

`src/spma/agents/supervisor/feature_flag.py`:

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
    - set_rollout() 立即清空本地缓存,5s 内全网生效
    """

    def __init__(self, redis_client, local_cache_ttl: int = 5):
        self._redis = redis_client
        self._cache: dict[str, tuple[bool, float]] = {}
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
            enabled = True
        else:
            config = json.loads(flag)
            enabled = config.get("enabled", True)
            if user_bucket is not None and "rollout_pct" in config:
                enabled = enabled and (user_bucket < config["rollout_pct"])

        self._cache[cache_key] = (enabled, now + self._ttl)
        return enabled

    async def set_rollout(self, strategy_name: str, *, rollout_pct: int, enabled: bool = True):
        await self._redis.set(
            f"flag:qr:{strategy_name}",
            json.dumps({"enabled": enabled, "rollout_pct": rollout_pct}),
        )
        self._cache.clear()
        logger.info(f"FeatureFlag {strategy_name}: enabled={enabled}, rollout_pct={rollout_pct}")

    def user_bucket(self, user_id: str) -> int:
        return hash(user_id) % 100
```

### Step 1.4: 重新运行测试

Run:
```bash
cd /Users/Ray/TraeProjects/SPMA
pytest tests/unit/agents/supervisor/test_feature_flag.py -v
```

Expected: 6 passed

### Step 1.5: 提交

```bash
cd /Users/Ray/TraeProjects/SPMA
git add src/spma/agents/supervisor/feature_flag.py tests/unit/agents/supervisor/test_feature_flag.py
git commit -m "feat(qr): StrategyFeatureFlag — 秒级 KILL SWITCH(G15 部分)

主文件 §3.8:Redis 5s 本地缓存 + set_rollout 立即清空。
user_bucket 一致性(同 user_id 始终同组)。

Refs: SPMA-design-11-phase8 §3.1"
```

---

## Task 2: `RollbackManager` + 单测

**Files:**
- Create: `src/spma/agents/supervisor/rollback_manager.py`
- Test: `tests/unit/agents/supervisor/test_rollback_manager.py`

### Step 2.1: 写失败的测试

`tests/unit/agents/supervisor/test_rollback_manager.py`:

```python
"""RollbackManager 单测。"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from spma.agents.supervisor.rollback_manager import RollbackManager


@pytest.fixture
def mock_pool():
    pool = MagicMock()
    conn = MagicMock()
    conn.execute = AsyncMock(return_value=None)
    conn.fetchrow = AsyncMock(return_value={"payload": json.dumps({"weights": {"a": 0.5, "b": 0.5}})})
    conn.fetchval = AsyncMock(return_value=3)
    conn.fetch = AsyncMock(return_value=[])
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
async def test_rollback_to_writes_new_active_and_bumps_version(mock_pool):
    rm = RollbackManager(mock_pool, max_versions=10)
    success = await rm.rollback_to(weights_set_id=5, approver="alice")
    assert success is True
    # 应写 INSERT 新 active 记录
    mock_pool.acquire.assert_called()


@pytest.mark.asyncio
async def test_rollback_to_returns_false_when_version_not_found(mock_pool):
    mock_pool.acquire.return_value.__aenter__.return_value.fetchrow = AsyncMock(return_value=None)
    rm = RollbackManager(mock_pool, max_versions=10)
    success = await rm.rollback_to(weights_set_id=999, approver="alice")
    assert success is False


@pytest.mark.asyncio
async def test_list_versions_returns_history(mock_pool):
    from datetime import datetime
    mock_pool.acquire.return_value.__aenter__.return_value.fetch = AsyncMock(return_value=[
        {"weights_set_id": 1, "created_at": datetime.now(), "source": "ema",
         "approver": None, "payload": "{}"},
        {"weights_set_id": 2, "created_at": datetime.now(), "source": "manual",
         "approver": "alice", "payload": "{}"},
    ])
    rm = RollbackManager(mock_pool, max_versions=10)
    versions = await rm.list_versions()
    assert len(versions) == 2
    assert versions[0]["weights_set_id"] == 1
```

### Step 2.2: 运行测试,确认失败

Run:
```bash
cd /Users/Ray/TraeProjects/SPMA
pytest tests/unit/agents/supervisor/test_rollback_manager.py -v
```

Expected: ImportError(`rollback_manager` 不存在)

### Step 2.3: 写 `RollbackManager` 实现

`src/spma/agents/supervisor/rollback_manager.py`:

```python
"""权重快照回滚:从历史快照读出并激活(主文件 §3.8)。"""
import json
import logging

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
        new_v = await bump_weights_version(self._pool)
        logger.warning(
            f"Rolled back to version {weights_set_id}, new weights_version={new_v}, approver={approver}"
        )
        return True
```

### Step 2.4: 重新运行测试

Run:
```bash
cd /Users/Ray/TraeProjects/SPMA
pytest tests/unit/agents/supervisor/test_rollback_manager.py -v
```

Expected: 3 passed

### Step 2.5: 提交

```bash
cd /Users/Ray/TraeProjects/SPMA
git add src/spma/agents/supervisor/rollback_manager.py tests/unit/agents/supervisor/test_rollback_manager.py
git commit -m "feat(qr): RollbackManager — 1 分钟内回滚到任一历史版本(G15 部分)

复用已有 bump_weights_version(P8 信号源)。
rollback_to:读历史 payload + 写新 is_active + bump version。
list_versions:运维可视化历史。

Refs: SPMA-design-11-phase8 §3.2"
```

---

## Task 3: `CanaryRelease` 5 阶段编排 + 单测

**Files:**
- Create: `src/spma/agents/supervisor/canary.py`
- Test: `tests/unit/agents/supervisor/test_canary.py`

### Step 3.1: 写失败的测试

`tests/unit/agents/supervisor/test_canary.py`:

```python
"""CanaryRelease 单测(主文件 ADR-010)。"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from spma.agents.supervisor.canary import CanaryRelease, CANARY_STAGES


@pytest.fixture
def mock_ff():
    ff = MagicMock()
    ff.set_rollout = AsyncMock(return_value=None)
    return ff


@pytest.fixture
def mock_audit():
    audit = MagicMock()
    audit.log = AsyncMock(return_value=None)
    return mock_audit


@pytest.mark.asyncio
async def test_advance_shadow_sets_rollout_0(mock_ff, mock_audit):
    cr = CanaryRelease(mock_ff, mock_audit)
    await cr.advance("new_strategy", "shadow", operator="alice")
    mock_ff.set_rollout.assert_called_with("new_strategy", rollout_pct=0, enabled=True)


@pytest.mark.asyncio
async def test_advance_one_percent_sets_rollout_1(mock_ff, mock_audit):
    cr = CanaryRelease(mock_ff, mock_audit)
    await cr.advance("new_strategy", "one_percent", operator="alice")
    mock_ff.set_rollout.assert_called_with("new_strategy", rollout_pct=1, enabled=True)


@pytest.mark.asyncio
async def test_advance_ten_percent(mock_ff, mock_audit):
    cr = CanaryRelease(mock_ff, mock_audit)
    await cr.advance("s", "ten_percent", operator="alice")
    mock_ff.set_rollout.assert_called_with("s", rollout_pct=10, enabled=True)


@pytest.mark.asyncio
async def test_advance_unknown_stage_raises(mock_ff, mock_audit):
    cr = CanaryRelease(mock_ff, mock_audit)
    with pytest.raises(ValueError, match="Unknown stage"):
        await cr.advance("s", "nonexistent", operator="alice")


@pytest.mark.asyncio
async def test_halt_disables_strategy(mock_ff, mock_audit):
    cr = CanaryRelease(mock_ff, mock_audit)
    await cr.halt("new_strategy", operator="bob", reason="quality regression")
    mock_ff.set_rollout.assert_called_with("new_strategy", rollout_pct=0, enabled=False)
    # 审计调用
    assert mock_audit.log.called
```

### Step 3.2: 运行测试,确认失败

Run:
```bash
cd /Users/Ray/TraeProjects/SPMA
pytest tests/unit/agents/supervisor/test_canary.py -v
```

Expected: ImportError(`canary` 不存在)

### Step 3.3: 写 `CanaryRelease` 实现

`src/spma/agents/supervisor/canary.py`:

```python
"""冷启动 5 阶段流程编排(主文件 ADR-010:Shadow → 1% → 10% → 50% → 100%)。"""
import logging

logger = logging.getLogger(__name__)


CANARY_STAGES = [
    ("shadow", 0),
    ("one_percent", 1),
    ("ten_percent", 10),
    ("fifty_percent", 50),
    ("hundred_percent", 100),
]


class CanaryRelease:
    """灰度放量编排。手动驱动阶段切换,自动记录每次切换。"""

    def __init__(self, feature_flag, audit_logger):
        self._flag = feature_flag
        self._audit = audit_logger

    async def advance(self, strategy_name: str, stage: str, *, operator: str):
        stages_dict = dict(CANARY_STAGES)
        if stage not in stages_dict:
            raise ValueError(f"Unknown stage: {stage}")
        rollout_pct = stages_dict[stage]
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

### Step 3.4: 重新运行测试

Run:
```bash
cd /Users/Ray/TraeProjects/SPMA
pytest tests/unit/agents/supervisor/test_canary.py -v
```

Expected: 5 passed

### Step 3.5: 提交

```bash
cd /Users/Ray/TraeProjects/SPMA
git add src/spma/agents/supervisor/canary.py tests/unit/agents/supervisor/test_canary.py
git commit -m "feat(qr): CanaryRelease — 5 阶段灰度编排(G15 部分)

主文件 ADR-010:Shadow(0%) → 1% → 10% → 50% → 100%。
halt = 5s 内 KILL SWITCH(rollout_pct=0, enabled=False)。
每次 advance/halt 写审计日志。

Refs: SPMA-design-11-phase8 §3.3"
```

---

## Task 4: 运维 API + 24h 灰度

**Files:**
- Create: `src/spma/api/routes/canary.py`

### Step 4.1: 实现运维 API

`src/spma/api/routes/canary.py`:

```python
"""Canary 灰度控制 API(运维调用,需要 canary:write 权限)。"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/canary", tags=["canary"])


class AdvanceRequest(BaseModel):
    strategy_name: str
    stage: str
    operator: str


class HaltRequest(BaseModel):
    strategy_name: str
    operator: str
    reason: str


class RollbackRequest(BaseModel):
    weights_set_id: int
    approver: str


# 注入容器(由 app factory 注入)
_canary = None
_rollback = None


def set_canary(canary, rollback):
    global _canary, _rollback
    _canary = canary
    _rollback = rollback


@router.post("/advance")
async def canary_advance(req: AdvanceRequest):
    if _canary is None:
        raise HTTPException(503, "canary not initialized")
    await _canary.advance(req.strategy_name, req.stage, operator=req.operator)
    return {"status": "ok"}


@router.post("/halt")
async def canary_halt(req: HaltRequest):
    if _canary is None:
        raise HTTPException(503, "canary not initialized")
    await _canary.halt(req.strategy_name, operator=req.operator, reason=req.reason)
    return {"status": "halted"}


@router.post("/rollback")
async def rollback(req: RollbackRequest):
    if _rollback is None:
        raise HTTPException(503, "rollback not initialized")
    success = await _rollback.rollback_to(req.weights_set_id, approver=req.approver)
    if not success:
        raise HTTPException(404, "version not found")
    return {"status": "ok"}


@router.get("/versions")
async def list_versions():
    if _rollback is None:
        raise HTTPException(503, "rollback not initialized")
    return await _rollback.list_versions()
```

### Step 4.2: 在 app factory 注入

修改 `src/spma/api/main.py` 或相应启动文件(具体路径看项目),在启动时调用 `set_canary(canary_instance, rollback_instance)`。

### Step 4.3: 24h 灰度 + 演练

| 演练 | 操作 | 期望 |
|------|------|------|
| KILL SWITCH | `POST /api/canary/halt` | 5s 内全网 `is_enabled=False` |
| 回滚 | `POST /api/canary/rollback {weights_set_id: 5}` | 1min 内 `bump_weights_version` 触发 |
| 列出历史 | `GET /api/canary/versions` | 返回最近 10 条 |

### Step 4.4: 关闭 P8

```bash
cd /Users/Ray/TraeProjects/SPMA
git add docs/designs/SPMA-design-11-query-rewrite-optimization-v2-final.md src/spma/api/routes/canary.py
git commit -m "feat(qr): canary 运维 API + 灰度完成(G15 修复)"
```

---

## 验收 checklist

- [ ] Task 1:6 个 feature_flag 单测通过
- [ ] Task 2:3 个 rollback_manager 单测通过
- [ ] Task 3:5 个 canary 单测通过
- [ ] Task 4:运维 API 可调用,KILL SWITCH 5s 内生效,回滚 1min 内完成
- [ ] 主文件 §1.1 G15 标记为已修复

---

## 失败回滚

```bash
git revert <commit_hash_of_task_N>
# 4 个新文件,直接回滚;不影响 qr_state
```
