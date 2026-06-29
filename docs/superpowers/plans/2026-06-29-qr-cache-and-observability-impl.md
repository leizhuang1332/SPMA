# Query Rewriter §3.7 缓存 + §3.11 可观测性 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 v3.1 设计稿基础上落地 PG/Redis 职责分离的 QueryCache(L1 精确 + L2 pgvector 语义)+ audit buffer + OTel spans + Prometheus metrics + Alertmanager 规则,让 query_rewriter 全链路具备生产级缓存与可观测能力。

**Architecture:** 新建 1 个 PG 迁移(3 张表)、4 个新 Python 模块(`query_cache.py` / `qr_state.py` / `qr_metrics.py` / `qr_tracing.py`)、审计类型扩展;改造 `query_rewriter.rewrite_queries` 接入 QueryCache。Redis 仅承担 L1 精确缓存,PG 承担 L2 + 状态 + 历史 + 审计。

**Tech Stack:** Python 3.13 / asyncpg / pgvector / redis[hiredis] / opentelemetry-api / prometheus-client / pytest + pytest-asyncio / testcontainers[postgres,redis]。

**Spec:** [`docs/superpowers/specs/2026-06-29-qr-cache-and-observability-design.md`](../specs/2026-06-29-qr-cache-and-observability-design.md)

---

## 文件结构(实现开始前先看)

| 文件 | 职责 |
|---|---|
| `deployments/docker/migrations/002_qr_cache_and_state.sql` (新建) | 3 张 PG 表 + HNSW 索引 |
| `src/spma/agents/supervisor/query_cache.py` (新建) | L1+L2 双层缓存 + cache_key + PII skip |
| `src/spma/agents/supervisor/qr_state.py` (新建) | `qr_state_meta` / `qr_weights_history` 读写 + 版本号工具 |
| `src/spma/agents/supervisor/qr_audit.py` (新建) | `AuditBuffer`:in-memory queue + 5s flush worker |
| `src/spma/observability/qr_tracing.py` (新建) | OTel span helpers(`cache.lookup` / `cache.l1` / `cache.l2`) |
| `src/spma/observability/qr_metrics.py` (新建) | Prometheus 指标 + Alertmanager 规则 YAML |
| `src/spma/agents/supervisor/query_rewriter.py` (修改) | 接收可选 `cache` 参数,audit + cache wrap |
| `src/spma/agents/supervisor/graph.py` (修改) | `rewrite_node` 注入 `cache` 与 `audit_buffer` 依赖 |
| `src/spma/api/dependencies.py` (修改) | 提供 `QueryCache` / `AuditBuffer` 单例 |
| `tests/unit/agents/supervisor/test_query_cache_key.py` (新建) | cache_key 公式 |
| `tests/unit/agents/supervisor/test_query_cache_l1.py` (新建) | L1 Redis |
| `tests/unit/agents/supervisor/test_query_cache_l2.py` (新建) | L2 单 SQL 双召回 + PII skip |
| `tests/unit/agents/supervisor/test_qr_audit.py` (新建) | audit buffer flush |
| `tests/unit/agents/supervisor/test_qr_state.py` (新建) | 版本号读写 |
| `tests/integration/test_query_cache_pg.py` (新建) | Testcontainers PG + pgvector 全链路 |
| `deployments/observability/qr_alerts.yaml` (新建) | Alertmanager 规则 |

> 每个新文件**单一职责**;修改点最小化,不改现有 cache.py / audit.py 的现有契约。

---

## Task 1: PG 迁移脚本 — 3 张表 + HNSW 索引

**Files:**
- Create: `deployments/docker/migrations/002_qr_cache_and_state.sql`
- Create: `tests/integration/test_qr_migration.py`

- [ ] **Step 1: 写 SQL 迁移**

写入 `deployments/docker/migrations/002_qr_cache_and_state.sql`:

```sql
-- Migration 002: Query Rewriter 缓存 / 状态 / 审计基础表
-- 依赖: PG 16 + pgvector 0.7+

-- 1) 权重历史快照
CREATE TABLE IF NOT EXISTS qr_weights_history (
    weights_set_id  BIGSERIAL PRIMARY KEY,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source          TEXT NOT NULL CHECK (source IN ('ema','manual','rollback','init')),
    applied_at      TIMESTAMPTZ,
    approver        TEXT,
    payload         JSONB NOT NULL,
    is_active       BOOLEAN NOT NULL DEFAULT FALSE,
    CONSTRAINT qr_weights_only_one_active EXCLUDE USING btree (is_active WITH =) WHERE (is_active = true)
);

-- 2) 单行状态元数据(权重版本号 + synonym 版本号)
CREATE TABLE IF NOT EXISTS qr_state_meta (
    state_id        INT PRIMARY KEY DEFAULT 1,
    weights_version BIGINT NOT NULL DEFAULT 1,
    synonym_version BIGINT NOT NULL DEFAULT 1,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT qr_state_single_row CHECK (state_id = 1)
);
INSERT INTO qr_state_meta (state_id) VALUES (1) ON CONFLICT DO NOTHING;

-- 3) L2 语义缓存
CREATE TABLE IF NOT EXISTS qr_cache_entries (
    cache_id        BIGSERIAL PRIMARY KEY,
    query_hash      TEXT NOT NULL,
    weights_version BIGINT NOT NULL,
    synonym_version BIGINT NOT NULL,
    embedding       vector(1024) NOT NULL,
    payload         JSONB NOT NULL,
    ttl_ts          TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    query_preview   TEXT,
    UNIQUE (query_hash, weights_version, synonym_version)
);

CREATE INDEX IF NOT EXISTS idx_qr_cache_hnsw ON qr_cache_entries
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

CREATE INDEX IF NOT EXISTS idx_qr_cache_ttl ON qr_cache_entries (ttl_ts);

-- 4) 请求审计(unlogged + 按月分区,本期只建 default partition)
CREATE UNLOGGED TABLE IF NOT EXISTS qr_request_audit (
    request_id      UUID NOT NULL,
    ts              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    query_hash      TEXT NOT NULL,
    rewritten_hash  TEXT,
    pii_types       TEXT[],
    stage           TEXT NOT NULL,
    strategy_weights JSONB,
    weights_version BIGINT,
    synonym_version BIGINT,
    latency_ms      INT,
    cache_hit_l1    BOOLEAN,
    cache_hit_l2    BOOLEAN,
    cache_layer     TEXT,
    error_stage     TEXT,
    fallback_level  TEXT
) PARTITION BY RANGE (ts);
CREATE TABLE IF NOT EXISTS qr_request_audit_default PARTITION OF qr_request_audit DEFAULT;
```

- [ ] **Step 2: 写 migration 测试**

写入 `tests/integration/test_qr_migration.py`:

```python
"""验证 002 迁移文件应用成功 + 三张表存在 + HNSW 索引可创建。"""

import pytest
import asyncpg
from pathlib import Path

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "deployments/docker/migrations/002_qr_cache_and_state.sql"
)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_migration_creates_three_tables(pg_with_pgvector):
    sql = MIGRATION_PATH.read_text()
    async with pg_with_pgvector.acquire() as conn:
        await conn.execute(sql)
        rows = await conn.fetch(
            "SELECT tablename FROM pg_tables WHERE tablename IN "
            "('qr_weights_history','qr_state_meta','qr_cache_entries')"
        )
        names = {r["tablename"] for r in rows}
        assert {"qr_weights_history", "qr_state_meta", "qr_cache_entries"} <= names


@pytest.mark.integration
@pytest.mark.asyncio
async def test_state_meta_has_single_row(pg_with_pgvector):
    sql = MIGRATION_PATH.read_text()
    async with pg_with_pgvector.acquire() as conn:
        await conn.execute(sql)
        row = await conn.fetchrow("SELECT weights_version, synonym_version FROM qr_state_meta")
        assert row["weights_version"] == 1
        assert row["synonym_version"] == 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_hnsw_index_usable(pg_with_pgvector):
    sql = MIGRATION_PATH.read_text()
    async with pg_with_pgvector.acquire() as conn:
        await conn.execute(sql)
        row = await conn.fetchrow(
            "SELECT indexdef FROM pg_indexes WHERE indexname='idx_qr_cache_hnsw'"
        )
        assert row is not None
        assert "hnsw" in row["indexdef"].lower()
```

- [ ] **Step 3: 在 conftest.py 添加 fixture(若尚未存在)**

打开 `tests/integration/conftest.py`(若不存在则新建),追加:

```python
import pytest
from testcontainers.postgres import PostgresContainer
import asyncpg


@pytest.fixture(scope="session")
def pg_with_pgvector():
    container = PostgresContainer("pgvector/pgvector:pg16")
    container.start()
    yield container
    container.stop()


@pytest.fixture
async def pg_pool(pg_with_pgvector):
    url = pg_with_pgvector.get_connection_url()
    pool = await asyncpg.create_pool(dsn=url.replace("postgresql+psycopg", "postgres"))
    yield pool
    await pool.close()
```

- [ ] **Step 4: 运行测试确认绿**

Run: `uv run pytest tests/integration/test_qr_migration.py -v -m integration`
Expected: `3 passed`

- [ ] **Step 5: 提交**

```bash
git add deployments/docker/migrations/002_qr_cache_and_state.sql \
        tests/integration/test_qr_migration.py \
        tests/integration/conftest.py
git commit -m "feat(db): 002 migration — qr_weights_history/state_meta/cache_entries + hnsw"
```

---

## Task 2: cache_key 构造函数 + 版本号读取

**Files:**
- Create: `src/spma/agents/supervisor/qr_state.py`
- Create: `tests/unit/agents/supervisor/test_qr_state.py`

- [ ] **Step 1: 写失败测试**

写入 `tests/unit/agents/supervisor/test_qr_state.py`:

```python
"""qr_state_meta 读写 + cache_key 构造单元测试。"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from spma.agents.supervisor.qr_state import (
    build_cache_key,
    get_versions,
    bump_weights_version,
    bump_synonym_version,
)


@pytest.mark.asyncio
async def test_get_versions_returns_current_state():
    """get_versions 必须返回 (weights_version, synonym_version)。"""
    pool = MagicMock()
    pool.acquire = MagicMock()
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value={"weights_version": 7, "synonym_version": 3})
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

    w, s = await get_versions(pool)
    assert w == 7
    assert s == 3


def test_cache_key_differs_when_weights_version_bumps():
    """权重版本号变化必须改变 cache_key。"""
    base = build_cache_key(
        query="怎么取消订单",
        history_fingerprint="abc",
        entities={},
        weights_version=1,
        synonym_version=1,
    )
    bumped = build_cache_key(
        query="怎么取消订单",
        history_fingerprint="abc",
        entities={},
        weights_version=2,
        synonym_version=1,
    )
    assert base != bumped
    assert len(base) == 32  # md5 hex


def test_cache_key_differs_when_synonym_version_bumps():
    """synonym 版本号变化必须改变 cache_key。"""
    base = build_cache_key(query="q", history_fingerprint="h", entities={},
                           weights_version=1, synonym_version=1)
    bumped = build_cache_key(query="q", history_fingerprint="h", entities={},
                             weights_version=1, synonym_version=2)
    assert base != bumped


def test_cache_key_is_deterministic_for_same_input():
    """相同输入必须产生相同 key。"""
    k1 = build_cache_key(query="q", history_fingerprint="h", entities={"a": 1},
                         weights_version=1, synonym_version=1)
    k2 = build_cache_key(query="q", history_fingerprint="h", entities={"a": 1},
                         weights_version=1, synonym_version=1)
    assert k1 == k2


def test_history_fingerprint_only_uses_last_3_turns():
    """history_fingerprint 仅依赖最近 3 轮输入(由调用方保证)。"""
    fp_long = "deadbeef" * 4
    fp_short = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
    a = build_cache_key("q", history_fingerprint=fp_long, entities={},
                        weights_version=1, synonym_version=1)
    b = build_cache_key("q", history_fingerprint=fp_short, entities={},
                        weights_version=1, synonym_version=1)
    assert a == b


@pytest.mark.asyncio
async def test_bump_weights_version_returns_new_number():
    pool = MagicMock()
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=42)
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

    new_v = await bump_weights_version(pool)
    assert new_v == 42
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/unit/agents/supervisor/test_qr_state.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'spma.agents.supervisor.qr_state'`

- [ ] **Step 3: 实现 qr_state.py**

写入 `src/spma/agents/supervisor/qr_state.py`:

```python
"""qr_state_meta + 缓存版本号工具。

设计依据: docs/superpowers/specs/2026-06-29-qr-cache-and-observability-design.md §2.2
"""

import hashlib
import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def build_cache_key(
    query: str,
    history_fingerprint: str,
    entities: dict,
    weights_version: int,
    synonym_version: int,
) -> str:
    """构造 32 字符 md5 hex 作为缓存 key。

    公式: md5(query + history_fingerprint + sorted_json(entities)
              + str(weights_version) + str(synonym_version))

    history_fingerprint 由调用方计算(取最近 3 轮 sha256[:16] 等)。
    """
    entities_str = json.dumps(entities or {}, sort_keys=True, ensure_ascii=False)
    raw = (
        query
        + history_fingerprint
        + entities_str
        + str(weights_version)
        + str(synonym_version)
    )
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


async def get_versions(pool) -> tuple[int, int]:
    """从 qr_state_meta 取 (weights_version, synonym_version)。"""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT weights_version, synonym_version FROM qr_state_meta WHERE state_id=1"
        )
        if row is None:
            return (1, 1)
        return (int(row["weights_version"]), int(row["synonym_version"]))


async def bump_weights_version(pool) -> int:
    """自增 weights_version 并返回新值,需要在事务内调用。"""
    async with pool.acquire() as conn:
        async with conn.transaction():
            new_v = await conn.fetchval(
                "UPDATE qr_state_meta SET weights_version = weights_version + 1, "
                "updated_at = NOW() WHERE state_id = 1 RETURNING weights_version"
            )
            return int(new_v)


async def bump_synonym_version(pool) -> int:
    """自增 synonym_version 并返回新值,需要在事务内调用。"""
    async with pool.acquire() as conn:
        async with conn.transaction():
            new_v = await conn.fetchval(
                "UPDATE qr_state_meta SET synonym_version = synonym_version + 1, "
                "updated_at = NOW() WHERE state_id = 1 RETURNING synonym_version"
            )
            return int(new_v)


async def write_weights_snapshot(
    pool, *, payload: dict, source: str, applied_at: datetime | None = None,
    approver: str | None = None,
) -> int:
    """写一条新权重快照,自动取消旧 active 并激活此条。"""
    if source not in {"ema", "manual", "rollback", "init"}:
        raise ValueError(f"invalid source: {source}")
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("UPDATE qr_weights_history SET is_active = FALSE")
            new_id = await conn.fetchval(
                "INSERT INTO qr_weights_history (source, applied_at, approver, payload, is_active) "
                "VALUES ($1, $2, $3, $4::jsonb, TRUE) RETURNING weights_set_id",
                source, applied_at, approver, json.dumps(payload),
            )
            return int(new_id)
```

- [ ] **Step 4: 跑测试确认绿**

Run: `uv run pytest tests/unit/agents/supervisor/test_qr_state.py -v`
Expected: `7 passed`

- [ ] **Step 5: 提交**

```bash
git add src/spma/agents/supervisor/qr_state.py \
        tests/unit/agents/supervisor/test_qr_state.py
git commit -m "feat(qr): qr_state — versions + cache_key + weights snapshot"
```

---

## Task 3: L1 Redis 精确缓存 + 降级

**Files:**
- Create: `src/spma/agents/supervisor/query_cache.py`
- Create: `tests/unit/agents/supervisor/test_query_cache_l1.py`

> 此任务只实现 L1 部分,L2 在 Task 4。`QueryCache` 类的 __init__ 暂不接 L2,Task 5 才整合。

- [ ] **Step 1: 写失败测试**

写入 `tests/unit/agents/supervisor/test_query_cache_l1.py`:

```python
"""L1 Redis 精确缓存单元测试 + 故障降级。"""

import json
import pytest
from unittest.mock import AsyncMock

from spma.agents.supervisor.query_cache import L1Cache


@pytest.mark.asyncio
async def test_l1_get_returns_payload_on_hit():
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=b'{"rewrite":"x","candidates":["x"]}')
    l1 = L1Cache(redis)
    out = await l1.get("deadbeef")
    assert out == {"rewrite": "x", "candidates": ["x"]}


@pytest.mark.asyncio
async def test_l1_get_returns_none_on_miss():
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    l1 = L1Cache(redis)
    assert await l1.get("deadbeef") is None


@pytest.mark.asyncio
async def test_l1_get_transparently_falls_back_on_connection_error():
    from redis.exceptions import ConnectionError
    redis = AsyncMock()
    redis.get = AsyncMock(side_effect=ConnectionError("redis down"))
    l1 = L1Cache(redis)
    out = await l1.get("deadbeef")
    assert out is None  # 不抛异常,健康降级
    redis.get.assert_awaited_once()


@pytest.mark.asyncio
async def test_l1_set_calls_setex_with_ttl():
    redis = AsyncMock()
    redis.setex = AsyncMock()
    l1 = L1Cache(redis, ttl_s=3600)
    await l1.set("deadbeef", {"rewrite": "x"})
    redis.setex.assert_awaited_once()
    args = redis.setex.await_args.args
    assert args[0] == "qr:exact:deadbeef"
    assert args[1] == 3600
    payload = json.loads(args[2].decode())
    assert payload == {"rewrite": "x"}


@pytest.mark.asyncio
async def test_l1_set_swallows_connection_errors(caplog):
    """Redis 不可用时写 L1 不应阻塞 hot path。"""
    from redis.exceptions import ConnectionError
    redis = AsyncMock()
    redis.setex = AsyncMock(side_effect=ConnectionError("redis down"))
    l1 = L1Cache(redis)
    await l1.set("deadbeef", {"rewrite": "x"})  # 不抛异常
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/unit/agents/supervisor/test_query_cache_l1.py -v`
Expected: FAIL with `cannot import name 'L1Cache'`

- [ ] **Step 3: 在 query_cache.py 先实现 L1**

写入 `src/spma/agents/supervisor/query_cache.py`(本任务只含 L1):

```python
"""Query Rewriter 双层缓存(L1 Redis + L2 pgvector)。

设计依据: docs/superpowers/specs/2026-06-29-qr-cache-and-observability-design.md §3
"""

import json
import logging

logger = logging.getLogger(__name__)


class L1Cache:
    """Redis 精确匹配缓存。健康降级:Redis 出错时不抛异常。"""

    KEY_PREFIX = "qr:exact:"

    def __init__(self, redis_client, ttl_s: int = 3600):
        self._redis = redis_client
        self._ttl = ttl_s

    def _key(self, query_hash: str) -> str:
        return f"{self.KEY_PREFIX}{query_hash}"

    async def get(self, query_hash: str) -> dict | None:
        try:
            raw = await self._redis.get(self._key(query_hash))
        except Exception as e:
            logger.warning("qr l1 get failed: %s: %s", type(e).__name__, e)
            return None
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("qr l1 payload not json, dropping")
            return None

    async def set(self, query_hash: str, payload: dict) -> None:
        try:
            await self._redis.setex(
                self._key(query_hash),
                self._ttl,
                json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            )
        except Exception as e:
            logger.warning("qr l1 set failed: %s: %s", type(e).__name__, e)

    async def delete(self, query_hash: str) -> None:
        try:
            await self._redis.delete(self._key(query_hash))
        except Exception as e:
            logger.warning("qr l1 delete failed: %s: %s", type(e).__name__, e)
```

- [ ] **Step 4: 跑测试确认绿**

Run: `uv run pytest tests/unit/agents/supervisor/test_query_cache_l1.py -v`
Expected: `5 passed`

- [ ] **Step 5: 提交**

```bash
git add src/spma/agents/supervisor/query_cache.py \
        tests/unit/agents/supervisor/test_query_cache_l1.py
git commit -m "feat(qr): L1 Redis cache with transparent degradation"
```

---

## Task 4: L2 pgvector 单 SQL 双召回 + PII skip

**Files:**
- Modify: `src/spma/agents/supervisor/query_cache.py`(追加 L2Cache + PII 检测)
- Create: `tests/unit/agents/supervisor/test_query_cache_l2.py`

- [ ] **Step 1: 写失败测试**

写入 `tests/unit/agents/supervisor/test_query_cache_l2.py`:

```python
"""L2 pgvector 单 SQL 双召回 + PII skip 单元测试。"""

import re
import pytest
from unittest.mock import AsyncMock, MagicMock

from spma.agents.supervisor.query_cache import L2Cache, contains_pii


@pytest.mark.parametrize(
    "text",
    [
        "我的手机号是 13812345678 怎么改",
        "身份证 110101199003078888",
        "邮箱 user@example.com 怎么联系",
    ],
)
def test_contains_pii_detects_phone_id_email(text):
    assert contains_pii(text) is True


@pytest.mark.parametrize(
    "text",
    ["订单取消怎么操作", "怎么查询 user_id=42 的订单", "表 user_orders 是什么"],
)
def test_contains_pii_returns_false_for_clean_text(text):
    assert contains_pii(text) is False


@pytest.mark.asyncio
async def test_l2_lookup_returns_exact_match_first():
    """精确 hash 命中应优先于语义近邻。"""
    pool = MagicMock()
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value={
        "payload": {"rewrite": "EXACT"},
        "match_type": "exact_match",
        "cosine_distance": 0.0,
    })
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

    l2 = L2Cache(pool, embedding_dim=1024)
    out = await l2.lookup(
        query_hash="abc", query_embedding=[0.0] * 1024,
        weights_version=1, synonym_version=1,
    )
    assert out == {"payload": {"rewrite": "EXACT"}, "match_type": "exact_match"}


@pytest.mark.asyncio
async def test_l2_lookup_returns_none_on_miss():
    pool = MagicMock()
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=None)
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

    l2 = L2Cache(pool, embedding_dim=1024)
    out = await l2.lookup(
        query_hash="miss", query_embedding=[0.0] * 1024,
        weights_version=1, synonym_version=1,
    )
    assert out is None


@pytest.mark.asyncio
async def test_l2_lookup_passes_versions_into_sql():
    """SQL 必须用传入的 weights/synonym version 过滤。"""
    pool = MagicMock()
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=None)
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

    l2 = L2Cache(pool, embedding_dim=1024)
    await l2.lookup(
        query_hash="abc", query_embedding=[0.0] * 1024,
        weights_version=7, synonym_version=3,
    )

    sql = conn.fetchrow.await_args.args[0]
    assert "weights_version" in sql
    assert "synonym_version" in sql
    # values must include version 7 and 3
    assert 7 in conn.fetchrow.await_args.args


@pytest.mark.asyncio
async def test_l2_put_skips_when_query_contains_pii(caplog):
    """含 PII 的 query 必须跳过 L2 写入。"""
    pool = MagicMock()
    conn = AsyncMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

    l2 = L2Cache(pool, embedding_dim=4)
    await l2.put(
        query="我的手机号 13812345678",
        query_embedding=[0.1, 0.2, 0.3, 0.4],
        payload={"rewrite": "x"},
        weights_version=1,
        synonym_version=1,
        query_hash="abc",
    )
    conn.execute.assert_not_called()


@pytest.mark.asyncio
async def test_l2_put_inserts_when_clean():
    pool = MagicMock()
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=42)
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

    l2 = L2Cache(pool, embedding_dim=4)
    new_id = await l2.put(
        query="取消订单流程",
        query_embedding=[0.1, 0.2, 0.3, 0.4],
        payload={"rewrite": "订单取消流程"},
        weights_version=1,
        synonym_version=1,
        query_hash="abc",
    )
    assert new_id == 42
    conn.fetchval.assert_awaited_once()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/unit/agents/supervisor/test_query_cache_l2.py -v`
Expected: FAIL with `cannot import name 'L2Cache'`

- [ ] **Step 3: 在 query_cache.py 追加 L2 + PII**

编辑 `src/spma/agents/supervisor/query_cache.py`,在文件末尾追加:

```python
import hashlib
import re
from datetime import datetime, timedelta, timezone
from typing import Sequence


_PII_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b1[3-9]\d{9}\b"),                    # 中国手机号
    re.compile(r"\b\d{17}[\dXx]\b"),                   # 中国身份证
    re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),       # email
    re.compile(r"\b(?:\d[ -]*?){13,16}\b"),            # 信用卡
)


def contains_pii(text: str) -> bool:
    """粗略 PII 检测:正则匹配 PII 即视为含 PII。"""
    return any(p.search(text) for p in _PII_PATTERNS)


class L2Cache:
    """pgvector 单 SQL 双召回缓存。

    lookup(): 单条 SQL 同时按精确 hash 与 cosine 距离排序,
              优先返回精确命中。
    put(): 含 PII 时直接跳过(PII 不入库,仅由 L1 短 TTL 兜底)。
    """

    def __init__(self, pg_pool, embedding_dim: int = 1024,
                 distance_threshold: float = 0.08,
                 ttl_s: int = 86400):
        self._pool = pg_pool
        self._dim = embedding_dim
        self._threshold = distance_threshold
        self._ttl_s = ttl_s

    async def lookup(
        self,
        *,
        query_hash: str,
        query_embedding: Sequence[float],
        weights_version: int,
        synonym_version: int,
    ) -> dict | None:
        sql = """
            SELECT payload,
                   CASE WHEN query_hash = $1 THEN 'exact_match'::text
                        ELSE 'semantic_match'::text END AS match_type,
                   (embedding <=> $2) AS cosine_distance
            FROM qr_cache_entries
            WHERE (query_hash = $1 OR (embedding <=> $2) < $3)
              AND weights_version = $4
              AND synonym_version = $5
              AND ttl_ts > NOW()
            ORDER BY CASE WHEN query_hash = $1 THEN 0 ELSE 1 END,
                     embedding <=> $2
            LIMIT 1
        """
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    sql, query_hash, list(query_embedding),
                    self._threshold, weights_version, synonym_version,
                )
        except Exception as e:
            logger.warning("qr l2 lookup failed: %s: %s", type(e).__name__, e)
            return None
        if row is None:
            return None
        return {
            "payload": row["payload"],
            "match_type": row["match_type"],
            "cosine_distance": float(row["cosine_distance"]),
        }

    async def put(
        self,
        *,
        query: str,
        query_embedding: Sequence[float],
        payload: dict,
        weights_version: int,
        synonym_version: int,
        query_hash: str,
    ) -> int | None:
        """返回新 cache_id;含 PII 时返回 None 且不写库。"""
        if contains_pii(query):
            logger.info("qr l2 put skipped due to PII: %s", query[:50])
            return None
        ttl_ts = datetime.now(timezone.utc) + timedelta(seconds=self._ttl_s)
        sql = """
            INSERT INTO qr_cache_entries
                (query_hash, weights_version, synonym_version,
                 embedding, payload, ttl_ts, query_preview)
            VALUES ($1, $2, $3, $4::vector, $5::jsonb, $6, $7)
            ON CONFLICT (query_hash, weights_version, synonym_version) DO UPDATE
                SET payload = EXCLUDED.payload,
                    ttl_ts = EXCLUDED.ttl_ts,
                    embedding = EXCLUDED.embedding
            RETURNING cache_id
        """
        try:
            async with self._pool.acquire() as conn:
                new_id = await conn.fetchval(
                    sql, query_hash, weights_version, synonym_version,
                    list(query_embedding),
                    json.dumps(payload, ensure_ascii=False),
                    ttl_ts, query[:64],
                )
                return int(new_id) if new_id is not None else None
        except Exception as e:
            logger.warning("qr l2 put failed: %s: %s", type(e).__name__, e)
            return None
```

- [ ] **Step 4: 跑测试确认绿**

Run: `uv run pytest tests/unit/agents/supervisor/test_query_cache_l2.py -v`
Expected: `8 passed`

- [ ] **Step 5: 提交**

```bash
git add src/spma/agents/supervisor/query_cache.py \
        tests/unit/agents/supervisor/test_query_cache_l2.py
git commit -m "feat(qr): L2 pgvector single-SQL dual recall + PII skip"
```

---

## Task 5: QueryCache 整合(L1→L2→Orchestrator 调用)

**Files:**
- Modify: `src/spma/agents/supervisor/query_cache.py`(追加 QueryCache 类)
- Create: `tests/unit/agents/supervisor/test_query_cache.py`

- [ ] **Step 1: 写失败测试**

写入 `tests/unit/agents/supervisor/test_query_cache.py`:

```python
"""QueryCache 整合:lookup_or_compute 走 L1→L2→compute 链路。"""

import pytest
from unittest.mock import AsyncMock, MagicMock, call

from spma.agents.supervisor.query_cache import QueryCache


@pytest.mark.asyncio
async def test_lookup_returns_l1_payload_without_touching_l2():
    l1 = AsyncMock()
    l1.get = AsyncMock(return_value={"rewrite": "L1"})
    l2 = AsyncMock()
    pool = MagicMock()

    qc = QueryCache(l1=l1, l2=l2, pool=pool, embedder=AsyncMock())
    out = await qc.lookup_or_compute(
        query="q", history_fingerprint="fp", entities={},
        weights_version=1, synonym_version=1,
        compute=AsyncMock(return_value={"rewrite": "COMPUTE"}),
    )
    assert out["rewrite"] == "L1"
    assert out["cache_layer"] == "l1"
    l2.lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_lookup_falls_through_to_l2_when_l1_misses():
    l1 = AsyncMock()
    l1.get = AsyncMock(return_value=None)
    l2 = AsyncMock()
    l2.lookup = AsyncMock(return_value={
        "payload": {"rewrite": "L2"},
        "match_type": "semantic_match",
        "cosine_distance": 0.04,
    })

    qc = QueryCache(l1=l1, l2=l2, pool=MagicMock(), embedder=AsyncMock())
    out = await qc.lookup_or_compute(
        query="q", history_fingerprint="fp", entities={},
        weights_version=1, synonym_version=1,
        compute=AsyncMock(),
    )
    assert out["rewrite"] == "L2"
    assert out["cache_layer"] == "l2"
    # L1 应被回填
    l1.set.assert_awaited_once()


@pytest.mark.asyncio
async def test_lookup_falls_through_to_compute_when_both_miss():
    l1 = AsyncMock()
    l1.get = AsyncMock(return_value=None)
    l2 = AsyncMock()
    l2.lookup = AsyncMock(return_value=None)
    embedder = AsyncMock()
    embedder.embed_query = AsyncMock(return_value=[0.1] * 1024)

    qc = QueryCache(l1=l1, l2=l2, pool=MagicMock(), embedder=embedder)
    out = await qc.lookup_or_compute(
        query="q", history_fingerprint="fp", entities={},
        weights_version=1, synonym_version=1,
        compute=AsyncMock(return_value={"rewrite": "COMPUTE"}),
    )
    assert out["rewrite"] == "COMPUTE"
    assert out["cache_layer"] == "miss"
    # compute 触发后必须 L1+L2 都被回填
    l1.set.assert_awaited_once()
    l2.put.assert_awaited_once()


@pytest.mark.asyncio
async def test_lookup_does_not_cache_when_compute_times_out():
    """compute 超时时,绝不能写 L1/L2。"""
    import asyncio
    l1 = AsyncMock()
    l1.get = AsyncMock(return_value=None)
    l2 = AsyncMock()
    l2.lookup = AsyncMock(return_value=None)
    embedder = AsyncMock()
    embedder.embed_query = AsyncMock(return_value=[0.1] * 1024)

    async def timeout_compute(*a, **kw):
        raise asyncio.TimeoutError()

    qc = QueryCache(l1=l1, l2=l2, pool=MagicMock(), embedder=embedder)
    with pytest.raises(asyncio.TimeoutError):
        await qc.lookup_or_compute(
            query="q", history_fingerprint="fp", entities={},
            weights_version=1, synonym_version=1,
            compute=timeout_compute,
        )
    l1.set.assert_not_awaited()
    l2.put.assert_not_awaited()


@pytest.mark.asyncio
async def test_lookup_skips_l2_when_query_contains_pii(caplog):
    l1 = AsyncMock()
    l1.get = AsyncMock(return_value=None)
    l2 = AsyncMock()
    l2.lookup = AsyncMock(return_value=None)

    qc = QueryCache(l1=l1, l2=l2, pool=MagicMock(),
                    embedder=AsyncMock(embed_query=AsyncMock(return_value=[0.0]*4)))
    out = await qc.lookup_or_compute(
        query="手机号 13812345678 怎么改",
        history_fingerprint="fp", entities={},
        weights_version=1, synonym_version=1,
        compute=AsyncMock(return_value={"rewrite": "OK"}),
    )
    assert out["cache_layer"] == "miss"
    l2.lookup.assert_not_awaited()  # PII 路径直接走 compute


@pytest.mark.asyncio
async def test_lookup_degrades_when_l1_raises_connection_error():
    from redis.exceptions import ConnectionError
    l1 = AsyncMock()
    l1.get = AsyncMock(side_effect=ConnectionError("redis down"))
    l2 = AsyncMock()
    l2.lookup = AsyncMock(return_value={
        "payload": {"rewrite": "L2"}, "match_type": "semantic_match",
    })

    qc = QueryCache(l1=l1, l2=l2, pool=MagicMock(), embedder=AsyncMock())
    out = await qc.lookup_or_compute(
        query="q", history_fingerprint="fp", entities={},
        weights_version=1, synonym_version=1,
        compute=AsyncMock(),
    )
    assert out["rewrite"] == "L2"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/unit/agents/supervisor/test_query_cache.py -v`
Expected: FAIL with `cannot import name 'QueryCache'`

- [ ] **Step 3: 在 query_cache.py 追加 QueryCache 类**

编辑 `src/spma/agents/supervisor/query_cache.py`,在 L2Cache 后追加:

```python
class EmbedderProtocol:
    """最小协议:QueryCache 需要 embedder.embed_query 同步返回 list[float]。"""
    async def embed_query(self, text: str) -> list[float]:  # pragma: no cover
        ...


class QueryCache:
    """双层缓存编排:L1 精确 → L2 语义(单 SQL)→ compute(回调用)→ 异步回填。

    行为契约:
      * L1 hit   → 直接返回, layer='l1', 不调 embedder
      * L1 miss + L2 hit → 返回 L2 结果, layer='l2', 同步回填 L1
      * L1 miss + L2 miss → 调 compute, layer='miss', 异步回填 L1 + L2
      * compute 抛 TimeoutError 时**永不**写 L1/L2(防污染)
      * query 含 PII 时跳过 L2 lookup 与 L2 put(仅写 L1)
    """

    def __init__(
        self,
        *,
        l1: L1Cache,
        l2: L2Cache,
        pool,
        embedder: EmbedderProtocol,
    ):
        self._l1 = l1
        self._l2 = l2
        self._pool = pool
        self._embedder = embedder

    async def lookup_or_compute(
        self,
        *,
        query: str,
        history_fingerprint: str,
        entities: dict,
        weights_version: int,
        synonym_version: int,
        compute,
    ) -> dict:
        from spma.agents.supervisor.qr_state import build_cache_key

        query_hash = build_cache_key(
            query=query,
            history_fingerprint=history_fingerprint,
            entities=entities,
            weights_version=weights_version,
            synonym_version=synonym_version,
        )

        # 1) L1 hit
        hit = await self._l1.get(query_hash)
        if hit is not None:
            return {**hit, "cache_layer": "l1"}

        # 2) L2 hit(PII 路径直接跳过)
        if not contains_pii(query):
            embedding = await self._embedder.embed_query(query)
            l2_hit = await self._l2.lookup(
                query_hash=query_hash,
                query_embedding=embedding,
                weights_version=weights_version,
                synonym_version=synonym_version,
            )
            if l2_hit is not None:
                # 同步回填 L1(hot path 上做,避免再次落到编排器)
                await self._l1.set(query_hash, l2_hit["payload"])
                return {**l2_hit["payload"], "cache_layer": "l2"}

        # 3) compute — 绝不能在 timeout 时落库
        result = await compute(query=query, entities=entities)  # type: ignore[arg-type]
        # 必须 compute 成功才回填(异常会向上抛,不会落库)
        await self._refill(
            query=query,
            query_hash=query_hash,
            result=result,
            weights_version=weights_version,
            synonym_version=synonym_version,
        )
        return {**result, "cache_layer": "miss"}

    async def _refill(self, *, query, query_hash, result, weights_version, synonym_version):
        # L1 同步(短 TTL,因为 PII 路径也走 L1)
        await self._l1.set(query_hash, result)
        # L2 异步(失败仅日志)
        if not contains_pii(query):
            try:
                embedding = await self._embedder.embed_query(query)
                import asyncio
                asyncio.create_task(
                    self._l2.put(
                        query=query,
                        query_embedding=embedding,
                        payload=result,
                        weights_version=weights_version,
                        synonym_version=synonym_version,
                        query_hash=query_hash,
                    )
                )
            except Exception as e:
                logger.warning("qr l2 refill embed failed: %s: %s",
                               type(e).__name__, e)
```

- [ ] **Step 4: 跑测试确认绿**

Run: `uv run pytest tests/unit/agents/supervisor/test_query_cache.py -v`
Expected: `6 passed`

- [ ] **Step 5: 跑全部缓存相关测试 + ruff**

Run: `uv run pytest tests/unit/agents/supervisor/test_query_cache_l1.py tests/unit/agents/supervisor/test_query_cache_l2.py tests/unit/agents/supervisor/test_query_cache.py tests/unit/agents/supervisor/test_qr_state.py -v`
Expected: `26 passed`

Run: `uv run ruff check src/spma/agents/supervisor/query_cache.py src/spma/agents/supervisor/qr_state.py`
Expected: `All checks passed!`

- [ ] **Step 6: 提交**

```bash
git add src/spma/agents/supervisor/query_cache.py \
        tests/unit/agents/supervisor/test_query_cache.py
git commit -m "feat(qr): QueryCache orchestration — L1→L2→compute + LLM timeout no-pollute"
```

---

## Task 6: AuditBuffer(in-memory + 5s flush worker)

**Files:**
- Create: `src/spma/agents/supervisor/qr_audit.py`
- Create: `tests/unit/agents/supervisor/test_qr_audit.py`

> 复用 `spma.infrastructure.audit.AuditLogger` 的非阻塞行为,但本任务的 `AuditBuffer` 是 QR 专属:`qr_request_audit` 表专用,5s flush,失败 fallback。

- [ ] **Step 1: 写失败测试**

写入 `tests/unit/agents/supervisor/test_qr_audit.py`:

```python
"""qr_request_audit 内存缓冲 + 5s flush 测试。"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from spma.agents.supervisor.qr_audit import QrAuditBuffer


@pytest.mark.asyncio
async def test_enqueue_does_not_block_on_db():
    """enqueue 不应阻塞 hot path(异步入队即可)。"""
    buf = QrAuditBuffer(pool=None, flush_interval_s=3600)
    await buf.enqueue({"request_id": "1", "ts": "now"})
    assert len(buf._queue) == 1


@pytest.mark.asyncio
async def test_flush_inserts_all_records_to_qr_request_audit():
    pool = MagicMock()
    conn = AsyncMock()
    # executemany 时 fetch 不到 row
    conn.executemany = AsyncMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

    buf = QrAuditBuffer(pool=pool, flush_interval_s=3600, batch_size=10)
    for i in range(3):
        await buf.enqueue({"request_id": str(i), "ts": "now", "stage": "rewrite"})
    await buf._flush()

    assert len(buf._queue) == 0
    conn.executemany.assert_awaited_once()
    sql = conn.executemany.await_args.args[0]
    assert "qr_request_audit" in sql.lower()


@pytest.mark.asyncio
async def test_flush_swallows_db_errors_and_retains_records(caplog):
    """PG 不可用时,记录保留在内存,下次启动再决定丢弃/落盘。"""
    pool = MagicMock()
    conn = AsyncMock()
    conn.executemany = AsyncMock(side_effect=Exception("pg down"))
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

    buf = QrAuditBuffer(pool=pool, flush_interval_s=3600)
    await buf.enqueue({"request_id": "1", "ts": "now"})
    await buf._flush()
    assert len(buf._queue) == 1  # 保留以便下次重试


@pytest.mark.asyncio
async def test_flush_respects_batch_size():
    pool = MagicMock()
    conn = AsyncMock()
    conn.executemany = AsyncMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

    buf = QrAuditBuffer(pool=pool, flush_interval_s=3600, batch_size=2)
    for i in range(5):
        await buf.enqueue({"request_id": str(i), "ts": "now"})
    await buf._flush()
    # 一次只 flush batch_size 条
    assert len(buf._queue) == 3
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/unit/agents/supervisor/test_qr_audit.py -v`
Expected: FAIL with `cannot import name 'QrAuditBuffer'`

- [ ] **Step 3: 实现 qr_audit.py**

写入 `src/spma/agents/supervisor/qr_audit.py`:

```python
"""qr_request_audit 内存缓冲 + 异步 flush worker。

设计依据: docs/superpowers/specs/2026-06-29-qr-cache-and-observability-design.md §4.1, §4.3
"""

import asyncio
import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class QrAuditBuffer:
    """QR 请求审计缓冲:enqueue 非阻塞,后台定时 flush 到 PG。

    行为契约:
      * enqueue 只把记录 push 到内存队列,O(1)
      * flush 由 background 定时任务触发(默认 5s)
      * flush 失败不抛异常,记录保留在内存
      * 进程重启时保留未 flush 的记录由调用方决定
        (PII 记录不持久化到磁盘)
    """

    SQL_INSERT = """
        INSERT INTO qr_request_audit
            (request_id, ts, query_hash, rewritten_hash, pii_types,
             stage, strategy_weights, weights_version, synonym_version,
             latency_ms, cache_hit_l1, cache_hit_l2, cache_layer,
             error_stage, fallback_level)
        VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9, $10, $11, $12, $13, $14, $15)
    """

    def __init__(self, pool, *, flush_interval_s: float = 5.0, batch_size: int = 100):
        self._pool = pool
        self._interval = flush_interval_s
        self._batch_size = batch_size
        self._queue: list[dict] = []
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None

    async def enqueue(self, record: dict) -> None:
        """非阻塞入队;若记录缺失 ts/created_at 则补齐。"""
        async with self._lock:
            record.setdefault("ts", datetime.now(timezone.utc).isoformat())
            self._queue.append(record)

    async def _flush(self) -> None:
        """单次 flush:取 batch_size 条记录写入 PG;失败记录保留。"""
        if self._pool is None or not self._queue:
            return
        async with self._lock:
            batch, self._queue = (
                self._queue[:self._batch_size],
                self._queue[self._batch_size:],
            )
        if not batch:
            return
        try:
            params = [
                (
                    r.get("request_id"),
                    r.get("ts"),
                    r.get("query_hash"),
                    r.get("rewritten_hash"),
                    r.get("pii_types", []),
                    r.get("stage"),
                    json.dumps(r.get("strategy_weights") or {}),
                    r.get("weights_version"),
                    r.get("synonym_version"),
                    r.get("latency_ms"),
                    r.get("cache_hit_l1"),
                    r.get("cache_hit_l2"),
                    r.get("cache_layer"),
                    r.get("error_stage"),
                    r.get("fallback_level"),
                )
                for r in batch
            ]
            async with self._pool.acquire() as conn:
                await conn.executemany(self.SQL_INSERT, params)
        except Exception as e:
            logger.warning("qr audit flush failed: %s: %s",
                           type(e).__name__, e)
            async with self._lock:
                self._queue = batch + self._queue  # 归还

    async def start(self) -> None:
        """启动后台 flush worker。"""
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        """停止 worker 并最后一次 flush。"""
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except (asyncio.CancelledError, Exception):
            pass
        self._task = None
        await self._flush()

    async def _run(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._interval)
                await self._flush()
            except asyncio.CancelledError:
                return
            except Exception as e:  # noqa: BLE001
                logger.warning("qr audit worker error: %s: %s",
                               type(e).__name__, e)
```

- [ ] **Step 4: 跑测试确认绿**

Run: `uv run pytest tests/unit/agents/supervisor/test_qr_audit.py -v`
Expected: `4 passed`

- [ ] **Step 5: 提交**

```bash
git add src/spma/agents/supervisor/qr_audit.py \
        tests/unit/agents/supervisor/test_qr_audit.py
git commit -m "feat(qr): QrAuditBuffer — in-memory queue + 5s flush worker"
```

---

## Task 7: 与 query_rewriter 集成(cache + audit wrap)

**Files:**
- Modify: `src/spma/agents/supervisor/query_rewriter.py`(新增可选 `cache`/`audit_buffer`/`query_embedding` 参数)
- Modify: `src/spma/agents/supervisor/graph.py`(`rewrite_node` 注入依赖)
- Create: `tests/unit/agents/supervisor/test_query_rewriter_with_cache.py`

- [ ] **Step 1: 写失败测试**

写入 `tests/unit/agents/supervisor/test_query_rewriter_with_cache.py`:

```python
"""rewrite_queries 接入 QueryCache + QrAuditBuffer 后行为。"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from spma.agents.supervisor import query_rewriter


@pytest.mark.asyncio
async def test_rewrite_queries_passes_through_cache_when_provided():
    cache = AsyncMock()
    cache.lookup_or_compute = AsyncMock(return_value={
        "original": "q", "normalized": "q", "resolved": "q",
        "expanded": "Q", "doc": "Q", "code": "Q",
        "cache_layer": "l1",
    })
    audit = AsyncMock()
    audit.enqueue = AsyncMock()

    out = await query_rewriter.rewrite_queries(
        query="q",
        classification={"sources": ["doc", "code"], "query_type": "search",
                        "is_cross_source": False},
        entities={},
        llm=None,
        synonym_map=None,
        conversation_history="",
        cache=cache,
        audit_buffer=audit,
        query_embedding=None,
    )
    cache.lookup_or_compute.assert_awaited_once()
    assert out["cache_layer"] == "l1"


@pytest.mark.asyncio
async def test_rewrite_queries_records_audit_with_cache_layer():
    cache = AsyncMock()
    cache.lookup_or_compute = AsyncMock(return_value={
        "original": "q", "normalized": "q", "resolved": "q",
        "expanded": "Q", "doc": "Q", "code": "Q",
        "cache_layer": "l2",
    })
    audit = AsyncMock()
    audit.enqueue = AsyncMock()

    out = await query_rewriter.rewrite_queries(
        query="q",
        classification={"sources": ["doc", "code"], "query_type": "search"},
        entities={},
        llm=None, synonym_map=None, conversation_history="",
        cache=cache, audit_buffer=audit, query_embedding=None,
    )
    audit.enqueue.assert_awaited_once()
    record = audit.enqueue.await_args.args[0]
    assert record["stage"] == "rewrite"
    assert record["cache_layer"] == "l2"
    assert record["latency_ms"] >= 0


@pytest.mark.asyncio
async def test_rewrite_queries_skips_cache_when_not_provided():
    """无 cache 参数时保持原 5 阶段管道行为。"""
    out = await query_rewriter.rewrite_queries(
        query="q",
        classification={"sources": ["doc"], "query_type": "search"},
        entities={}, llm=None, synonym_map=None, conversation_history="",
    )
    assert out["original"] == "q"
    assert "cache_layer" not in out
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/unit/agents/supervisor/test_query_rewriter_with_cache.py -v`
Expected: FAIL(rewrite_queries 不接受 cache 参数)

- [ ] **Step 3: 修改 query_rewriter.py**

编辑 `src/spma/agents/supervisor/query_rewriter.py`,把头三行 `import` 后、函数 `rewrite_queries` 的签名改为:

```python
import hashlib
import time

logger = logging.getLogger(__name__)


def _history_fingerprint(conversation_history: str) -> str:
    """取最近 3 轮作为 fingerprint(sha256[:16]);空历史返回 'none'。"""
    if not conversation_history:
        return "none"
    turns = [t for t in conversation_history.split("\n") if t.strip()][-3:]
    return hashlib.sha256("|".join(turns).encode("utf-8")).hexdigest()[:16]


async def rewrite_queries(
    query: str,
    classification: dict,
    entities: dict,
    llm,
    synonym_map: dict | None = None,
    conversation_history: str = "",
    *,
    cache=None,
    audit_buffer=None,
    query_embedding=None,
    weights_version: int = 1,
    synonym_version: int = 1,
) -> dict[str, str]:
    """
    查询重写主函数 - 五阶段管道 + 可选缓存

    参数(新增):
        cache: QueryCache 实例,None 时禁用缓存
        audit_buffer: QrAuditBuffer 实例,None 时禁用审计
        query_embedding: 已计算的 embedding;None 时由 cache 内部计算
        weights_version: 当前权重版本号(参与 cache key)
        synonym_version: 当前 synonym 版本号(参与 cache key)
    """
    if cache is not None:
        async def _compute(query: str, entities: dict) -> dict:
            return await _do_rewrite_pipeline(query, classification, entities,
                                              llm, synonym_map, conversation_history)

        history_fp = _history_fingerprint(conversation_history)
        cached = await cache.lookup_or_compute(
            query=query,
            history_fingerprint=history_fp,
            entities=entities,
            weights_version=weights_version,
            synonym_version=synonym_version,
            compute=_compute,
        )
        result = {k: v for k, v in cached.items() if k != "cache_layer"}

        if audit_buffer is not None:
            await audit_buffer.enqueue({
                "request_id": str(hashlib.md5(
                    (query + history_fp).encode()).hexdigest()),
                "ts": None,  # 由 audit buffer 补
                "query_hash": hashlib.sha256(query.encode()).hexdigest()[:16],
                "rewritten_hash": hashlib.sha256(
                    (result.get("expanded") or "").encode()).hexdigest()[:16],
                "pii_types": [],
                "stage": "rewrite",
                "strategy_weights": None,
                "weights_version": weights_version,
                "synonym_version": synonym_version,
                "latency_ms": 0,  # 调用方可在 graph 层补
                "cache_hit_l1": cached.get("cache_layer") == "l1",
                "cache_hit_l2": cached.get("cache_layer") == "l2",
                "cache_layer": cached.get("cache_layer"),
                "error_stage": None,
                "fallback_level": None,
            })
        return result
    # cache=None 走原 5 阶段管道
    return await _do_rewrite_pipeline(
        query, classification, entities, llm, synonym_map, conversation_history)


async def _do_rewrite_pipeline(
    query, classification, entities, llm, synonym_map, conversation_history,
) -> dict:
    """原 rewrite_queries 主体(去掉外层 cache wrap)."""
    result: dict[str, str] = {"original": query}
    normalized = await _normalize_with_synonyms(query, synonym_map, entities)
    result["normalized"] = normalized
    resolved = await _resolve_references(normalized, conversation_history, llm)
    result["resolved"] = resolved
    query_type = classification.get("query_type", "search")
    sources = classification.get("sources", [])
    is_cross_source = classification.get("is_cross_source", False)
    should_expand = len(query) <= 50 or query_type == "search"
    if should_expand and llm:
        expanded = await _expand_query(resolved, classification, entities, llm)
        result["expanded"] = expanded
    else:
        result["expanded"] = resolved
    if is_cross_source and len(sources) > 1 and llm:
        try:
            sub_queries = await _decompose_query(resolved, entities, sources, llm)
            for sq in sub_queries:
                target = sq.get("target", "")
                if target in sources:
                    result[target] = sq.get("query", resolved)
        except Exception as e:
            logger.warning(f"查询分解失败: {e}")
            for source in sources:
                result[source] = result.get("expanded", resolved)
    else:
        for source in sources:
            result[source] = result.get("expanded", resolved)
    logger.info(f"Query rewrite: original={query[:50]}, "
                f"sources={sources}, expanded={result.get('expanded', '')[:50] if result.get('expanded') else None}")
    return result
```

> 把原 `rewrite_queries` 函数体迁移到 `_do_rewrite_pipeline`,保留所有原有测试不变。

- [ ] **Step 4: 跑全部相关单测确认绿**

Run: `uv run pytest tests/unit/agents/supervisor/ -v -k "query_rewriter or query_cache or qr_state or qr_audit" -m "not integration"`
Expected: 所有相关测试 pass,旧的 `test_query_rewriter` 系列也 pass(因为 `_do_rewrite_pipeline` 是直接调用,行为不变)

- [ ] **Step 5: 修改 graph.py 注入 cache + audit + 版本号**

编辑 `src/spma/agents/supervisor/graph.py`,找到 `build_supervisor_graph(...)` 签名追加 3 个可选参数:

```python
def build_supervisor_graph(
    primary_llm,
    fallback_llm=None,
    doc_graph=None,
    code_graph=None,
    sql_graph=None,
    synthesis_graph=None,
    max_rounds: int = 5,
    timeout_ms: int = 5000,
    quality_threshold: float = 0.6,
    reschedule_max: int = 2,
    *,
    qr_cache=None,           # 新增
    qr_audit_buffer=None,    # 新增
    qr_state_lookup=None,    # 新增:async () -> (weights_v, synonym_v)
) -> StateGraph:
```

`rewrite_node` 改为:

```python
    async def rewrite_node(state: SupervisorState) -> dict:
        if qr_state_lookup is not None:
            weights_v, synonym_v = await qr_state_lookup()
        else:
            weights_v, synonym_v = 1, 1

        rewritten = await rewrite_queries(
            query=state["original_query"],
            classification=state["classification"],
            entities=state.get("entities", {}),
            llm=primary_llm,
            synonym_map=None,
            conversation_history=state.get("conversation_history", ""),
            cache=qr_cache,
            audit_buffer=qr_audit_buffer,
            weights_version=weights_v,
            synonym_version=synonym_v,
        )
        return {"rewritten_queries": rewritten}
```

- [ ] **Step 6: 跑 supervisor 集成测试 + ruff**

Run: `uv run pytest tests/integration/test_supervisor_loop.py -v -m "not slow"`
Expected: 不应破坏既有路径

Run: `uv run ruff check src/spma/agents/supervisor/query_rewriter.py src/spma/agents/supervisor/graph.py`
Expected: `All checks passed!`

- [ ] **Step 7: 提交**

```bash
git add src/spma/agents/supervisor/query_rewriter.py \
        src/spma/agents/supervisor/graph.py \
        tests/unit/agents/supervisor/test_query_rewriter_with_cache.py
git commit -m "feat(qr): wire query_rewriter with QueryCache + QrAuditBuffer"
```

---

## Task 8: OTel spans for QR cache

**Files:**
- Create: `src/spma/observability/qr_tracing.py`
- Create: `tests/unit/observability/test_qr_tracing.py`

- [ ] **Step 1: 写失败测试**

写入 `tests/unit/observability/test_qr_tracing.py`:

```python
"""qr_tracing span helpers 单元测试(用 in-memory exporter 验证属性)。"""

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from spma.observability.qr_tracing import tracer, span_cache_lookup


def test_cache_lookup_span_exposes_required_attributes():
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    with span_cache_lookup(query="q", weights_version=2, synonym_version=3,
                          cache_layer="l2") as span:
        span.set_attribute("extra", "x")

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    s = spans[0]
    assert s.name == "qr.cache.lookup"
    assert s.attributes["cache.weights_version"] == 2
    assert s.attributes["cache.synonym_version"] == 3
    assert s.attributes["extra"] == "x"


def test_cache_lookup_default_layer_is_none_when_not_provided():
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    with span_cache_lookup(query="q", weights_version=1, synonym_version=1):
        pass
    s = exporter.get_finished_spans()[0]
    assert "cache.layer" not in s.attributes or s.attributes.get("cache.layer") is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/unit/observability/test_qr_tracing.py -v`
Expected: FAIL with `cannot import name 'span_cache_lookup'`

- [ ] **Step 3: 实现 qr_tracing.py**

写入 `src/spma/observability/qr_tracing.py`:

```python
"""Query Rewriter 链路追踪(基于 OpenTelemetry)。

设计依据: docs/superpowers/specs/2026-06-29-qr-cache-and-observability-design.md §4.2
"""

import hashlib
from contextlib import contextmanager

from opentelemetry import trace
from opentelemetry.trace import StatusCode, Span

tracer = trace.get_tracer("query_rewriter")


@contextmanager
def span_cache_lookup(query: str, weights_version: int, synonym_version: int,
                      cache_layer: str | None = None):
    """qr.cache.lookup 根 span,必填属性:weights/synonym version。"""
    with tracer.start_as_current_span("qr.cache.lookup") as span:
        span.set_attribute("query.hash", hashlib.md5(query.encode()).hexdigest()[:8])
        span.set_attribute("query.length", len(query))
        span.set_attribute("cache.weights_version", weights_version)
        span.set_attribute("cache.synonym_version", synonym_version)
        if cache_layer is not None:
            span.set_attribute("cache.layer", cache_layer)
        try:
            yield span
        except Exception as e:
            span.set_status(StatusCode.ERROR, str(e))
            span.record_exception(e)
            raise


def record_cache_layer(span: Span, cache_layer: str) -> None:
    """在 root span 上记录命中的层(l1/l2/miss)。"""
    span.set_attribute("cache.layer", cache_layer)


def record_l2_distance(span: Span, distance: float, match_type: str) -> None:
    """L2 子 span 属性:l2.distance + l2.match_type。"""
    span.set_attribute("l2.distance", float(distance))
    span.set_attribute("l2.match_type", match_type)
```

- [ ] **Step 4: 跑测试确认绿**

Run: `uv run pytest tests/unit/observability/test_qr_tracing.py -v`
Expected: `2 passed`

- [ ] **Step 5: 提交**

```bash
git add src/spma/observability/qr_tracing.py \
        tests/unit/observability/test_qr_tracing.py
git commit -m "feat(obs): qr_tracing — qr.cache.lookup span + l2 attributes"
```

---

## Task 9: Prometheus metrics for QR cache

**Files:**
- Create: `src/spma/observability/qr_metrics.py`
- Create: `tests/unit/observability/test_qr_metrics.py`
- Create: `deployments/observability/qr_alerts.yaml`

- [ ] **Step 1: 写失败测试**

写入 `tests/unit/observability/test_qr_metrics.py`:

```python
"""QR Prometheus 指标注册与采集测试。"""

from prometheus_client import CollectorRegistry
from spma.observability.qr_metrics import (
    QrMetrics, build_qr_metrics, COUNTER_CACHE_REQUESTS,
    COUNTER_CACHE_ERRORS, GAUGE_WEIGHT_VERSION,
)


def test_build_qr_metrics_returns_distinct_registry_per_call():
    a = build_qr_metrics()
    b = build_qr_metrics()
    assert a is not b
    assert isinstance(a, QrMetrics)


def test_qr_metrics_increments():
    m = build_qr_metrics()
    m.observe_request(layer="l1", stage="rewrite")
    m.observe_request(layer="l1", stage="rewrite")
    m.observe_request(layer="miss", stage="rewrite")
    m.observe_error(layer="l2", error_type="pgvector_down")
    m.observe_l2_distance(distance=0.04, match_type="semantic_match")
    m.observe_flush_lag(seconds=12)
    m.set_weight_version(version=3)

    # 计数器值累计
    counters = {fam.name: fam for fam in m.registry.collect()}
    val_l1 = next(
        s.value for fam in counters.values()
        if fam.name == "qr_cache_requests_total"
        for s in fam.samples if s.labels.get("layer") == "l1"
    )
    assert val_l1 == 2
    assert any(s.value == 1 for fam in counters.values()
               if fam.name == "qr_cache_requests_total"
               for s in fam.samples if s.labels.get("layer") == "miss")
    assert any(s.value == 1 for fam in counters.values()
               if fam.name == "qr_cache_errors_total"
               for s in fam.samples if s.labels.get("error_type") == "pgvector_down")


def test_qr_metrics_well_known_names():
    m = build_qr_metrics()
    m.observe_request(layer="l1", stage="rewrite")
    fam_names = {fam.name for fam in m.registry.collect()}
    expected = {
        "qr_cache_requests_total",
        "qr_cache_errors_total",
        "qr_cache_latency_seconds",
        "qr_cache_l2_distance",
        "qr_state_weight_version",
        "qr_audit_flush_lag_seconds",
    }
    assert expected <= fam_names
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/unit/observability/test_qr_metrics.py -v`
Expected: FAIL with `cannot import name 'build_qr_metrics'`

- [ ] **Step 3: 实现 qr_metrics.py**

写入 `src/spma/observability/qr_metrics.py`:

```python
"""Query Rewriter Prometheus 指标。

设计依据: docs/superpowers/specs/2026-06-29-qr-cache-and-observability-design.md §4.3
"""

import time
from dataclasses import dataclass

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram


COUNTER_CACHE_REQUESTS = "qr_cache_requests_total"
COUNTER_CACHE_ERRORS = "qr_cache_errors_total"
HISTOGRAM_CACHE_LATENCY = "qr_cache_latency_seconds"
HISTOGRAM_CACHE_L2_DISTANCE = "qr_cache_l2_distance"
COUNTER_FALLBACK = "qr_fallback_total"
GAUGE_WEIGHT_VERSION = "qr_state_weight_version"
GAUGE_FLUSH_LAG = "qr_audit_flush_lag_seconds"


@dataclass
class QrMetrics:
    registry: CollectorRegistry
    cache_requests: Counter
    cache_errors: Counter
    cache_latency: Histogram
    cache_l2_distance: Histogram
    fallback_total: Counter
    weight_version: Gauge
    flush_lag: Gauge

    def observe_request(self, *, layer: str, stage: str = "rewrite") -> None:
        self.cache_requests.labels(layer=layer, stage=stage).inc()

    def observe_error(self, *, layer: str, error_type: str) -> None:
        self.cache_errors.labels(layer=layer, error_type=error_type).inc()

    def observe_latency(self, *, layer: str, op: str, seconds: float) -> None:
        self.cache_latency.labels(layer=layer, op=op).observe(seconds)

    def observe_l2_distance(self, *, distance: float, match_type: str) -> None:
        self.cache_l2_distance.labels(match_type=match_type).observe(distance)

    def observe_fallback(self, *, level: str, stage: str) -> None:
        self.fallback_total.labels(level=level, stage=stage).inc()

    def set_weight_version(self, *, version: int) -> None:
        self.weight_version.set(version)

    def observe_flush_lag(self, *, seconds: float) -> None:
        self.flush_lag.set(seconds)


def build_qr_metrics() -> QrMetrics:
    """每次调用返回独立 CollectorRegistry(便于多实例 / 多测试)。"""
    registry = CollectorRegistry()
    return QrMetrics(
        registry=registry,
        cache_requests=Counter(
            COUNTER_CACHE_REQUESTS,
            "QR cache requests by layer",
            labelnames=("layer", "stage"),
            registry=registry,
        ),
        cache_errors=Counter(
            COUNTER_CACHE_ERRORS,
            "QR cache errors",
            labelnames=("layer", "error_type"),
            registry=registry,
        ),
        cache_latency=Histogram(
            HISTOGRAM_CACHE_LATENCY,
            "QR cache latency seconds",
            labelnames=("layer", "op"),
            buckets=(0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0),
            registry=registry,
        ),
        cache_l2_distance=Histogram(
            HISTOGRAM_CACHE_L2_DISTANCE,
            "QR cache L2 cosine distance",
            labelnames=("match_type",),
            buckets=(0.01, 0.04, 0.08, 0.12, 0.16, 0.20, 0.30, 0.50),
            registry=registry,
        ),
        fallback_total=Counter(
            COUNTER_FALLBACK,
            "QR fallback triggers",
            labelnames=("level", "stage"),
            registry=registry,
        ),
        weight_version=Gauge(
            GAUGE_WEIGHT_VERSION,
            "Current weights version (PG qr_state_meta)",
            registry=registry,
        ),
        flush_lag=Gauge(
            GAUGE_FLUSH_LAG,
            "QR audit buffer flush lag seconds",
            registry=registry,
        ),
    )
```

- [ ] **Step 4: 跑测试确认绿**

Run: `uv run pytest tests/unit/observability/test_qr_metrics.py -v`
Expected: `3 passed`

- [ ] **Step 5: 写 Alertmanager 规则 YAML(独立验证无单测)**

写入 `deployments/observability/qr_alerts.yaml`:

```yaml
groups:
- name: query_rewriter_cache
  rules:
  - alert: QRCacheL2Unavailable
    expr: rate(qr_cache_errors_total{error_type="pgvector_down"}[5m]) > 0
    for: 1m
    labels: {severity: critical}
    annotations:
      summary: "L2 缓存 PG 持续报错,自动降级到编排器(成本↑↑)"

  - alert: QRCacheHitRateDrop
    expr: qr_cache_hit_ratio{layer="l1"} < 0.4
    for: 30m
    labels: {severity: warning}
    annotations:
      summary: "L1 命中率长期走低,可能 cache key 粒度过细"

  - alert: QRCacheL2DistanceShift
    expr: histogram_quantile(0.5, qr_cache_l2_distance_bucket) > 0.15
    for: 1h
    labels: {severity: warning}
    annotations:
      summary: "L2 余弦距离 P50 上涨,阈值 0.08 偏严"

  - alert: QRAuditFlushLag
    expr: qr_audit_flush_lag_seconds > 30
    for: 5m
    labels: {severity: warning}
    annotations:
      summary: "审计 flush 滞后,可能审计数据不全"

  - alert: QRWeightVersionRolledBack
    expr: changes(qr_state_weight_version[10m]) > 5
    labels: {severity: warning}
    annotations:
      summary: "10 分钟内权重版本号变更 >5,可能是 §3.8 回滚频率过高"
```

- [ ] **Step 6: 提交**

```bash
git add src/spma/observability/qr_metrics.py \
        tests/unit/observability/test_qr_metrics.py \
        deployments/observability/qr_alerts.yaml
git commit -m "feat(obs): qr_metrics — 8 Prometheus counters/histograms + Alertmanager rules"
```

---

## Task 10: 集成测试(Testcontainers + pgvector 端到端)

**Files:**
- Create: `tests/integration/test_query_cache_pg.py`

- [ ] **Step 1: 写集成测试**

写入 `tests/integration/test_query_cache_pg.py`:

```python
"""QueryCache 端到端集成测试:Testcontainers PG + pgvector + Redis stub。"""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from spma.agents.supervisor.query_cache import (
    QueryCache, L1Cache, L2Cache,
)


class RedisStub:
    """最小 Redis stub,只支持 setex/get/delete。"""
    def __init__(self):
        self.store: dict[bytes, bytes] = {}

    async def get(self, k):
        return self.store.get(k.encode())

    async def setex(self, k, ttl, v):
        self.store[k.encode()] = v if isinstance(v, bytes) else v.encode()

    async def delete(self, k):
        self.store.pop(k.encode(), None)


class FakeEmbedder:
    """稳定 hash 嵌入:同 query 同 embedding,不同 query 不同 embedding。"""
    async def embed_query(self, q):
        # 把字符串 hash 成 1024 维向量(简单稳定的 spec)
        import hashlib, struct
        digest = hashlib.sha512(q.encode()).digest()
        floats = []
        while len(floats) < 1024:
            chunk = digest[:8]
            digest = hashlib.sha512(digest).digest()
            f = struct.unpack("<d", chunk)[0] / 1e18
            floats.append(f)
        return floats


@pytest.fixture
async def pg_with_table(pg_with_pgvector):
    sql_path = (
        Path(__file__).resolve().parents[2]
        / "deployments/docker/migrations/002_qr_cache_and_state.sql"
    )
    async with pg_with_pgvector.acquire() as conn:
        await conn.execute(sql_path.read_text())
    yield pg_with_pgvector


@pytest.mark.integration
@pytest.mark.asyncio
async def test_end_to_end_l1_l2_compute(pg_with_table):
    redis = RedisStub()
    l1 = L1Cache(redis, ttl_s=60)
    l2 = L2Cache(pg_with_table, embedding_dim=1024)

    async def compute(query, entities):
        return {"rewrite": "如何取消订单", "candidates": ["cancel"]}

    qc = QueryCache(l1=l1, l2=l2, pool=pg_with_table, embedder=FakeEmbedder())

    # 第一次: miss → 走 compute → 写 L1 + L2
    out1 = await qc.lookup_or_compute(
        query="怎么取消订单",
        history_fingerprint="fp",
        entities={},
        weights_version=1,
        synonym_version=1,
        compute=compute,
    )
    assert out1["cache_layer"] == "miss"
    assert out1["rewrite"] == "如何取消订单"

    # 第二次: L1 hit
    out2 = await qc.lookup_or_compute(
        query="怎么取消订单",
        history_fingerprint="fp",
        entities={},
        weights_version=1,
        synonym_version=1,
        compute=compute,
    )
    assert out2["cache_layer"] == "l1"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_end_to_end_version_bump_invalidates_l1(pg_with_table):
    redis = RedisStub()
    l1 = L1Cache(redis, ttl_s=60)
    l2 = L2Cache(pg_with_table, embedding_dim=1024)
    compute = AsyncMock(return_value={"rewrite": "x"})
    qc = QueryCache(l1=l1, l2=l2, pool=pg_with_table, embedder=FakeEmbedder())

    await qc.lookup_or_compute(
        query="q", history_fingerprint="fp", entities={},
        weights_version=1, synonym_version=1, compute=compute,
    )
    # bumps weights version → key 不一样 → 必然 miss
    out = await qc.lookup_or_compute(
        query="q", history_fingerprint="fp", entities={},
        weights_version=2, synonym_version=1, compute=compute,
    )
    assert out["cache_layer"] == "miss"
    assert compute.await_count == 2


@pytest.mark.integration
@pytest.mark.asyncio
async def test_pii_query_does_not_pollute_l2(pg_with_table):
    redis = RedisStub()
    l1 = L1Cache(redis, ttl_s=60)
    l2 = L2Cache(pg_with_table, embedding_dim=1024)

    compute = AsyncMock(return_value={"rewrite": "请联系客服"})
    qc = QueryCache(l1=l1, l2=l2, pool=pg_with_table, embedder=FakeEmbedder())

    out = await qc.lookup_or_compute(
        query="我的手机号是 13812345678 怎么改",
        history_fingerprint="fp", entities={},
        weights_version=1, synonym_version=1, compute=compute,
    )
    assert out["cache_layer"] == "miss"

    async with pg_with_table.acquire() as conn:
        rows = await conn.fetch(
            "SELECT 1 FROM qr_cache_entries WHERE query_preview LIKE '%13812345678%'"
        )
        assert len(rows) == 0
```

- [ ] **Step 2: 跑测试确认绿**

Run: `uv run pytest tests/integration/test_query_cache_pg.py -v -m integration`
Expected: `3 passed`

- [ ] **Step 3: 跑全量单测 + ruff + mypy**

Run: `uv run pytest tests/unit/agents/supervisor/ tests/unit/observability/ -v -m "not integration"`
Expected: 所有相关测试 pass

Run: `uv run ruff check src/spma/agents/supervisor/ src/spma/observability/`
Expected: `All checks passed!`

Run: `uv run mypy src/spma/agents/supervisor/query_cache.py src/spma/agents/supervisor/qr_state.py src/spma/agents/supervisor/qr_audit.py src/spma/observability/qr_metrics.py src/spma/observability/qr_tracing.py`
Expected: 无 error

- [ ] **Step 4: 提交**

```bash
git add tests/integration/test_query_cache_pg.py
git commit -m "test(qr): end-to-end integration — Testcontainers PG + Redis stub"
```

---

## Self-Review Checklist(实现后我做的检查,不属上述任务)

| 检查项 | 通过情况 |
|---|---|
| spec §3.1 三张表(状态/权重历史/L2 缓存) | Task 1 |
| spec §3.1 audit unlogged 表 | Task 1 + Task 6 |
| spec §3.2 L1 Redis + 版本号进 key | Task 3 + Task 2 |
| spec §3.3 数据流(L1→L2→compute→回填) | Task 5 |
| spec §3.4 单 SQL 双召回 | Task 4 |
| spec §4.1 Redis 故障降级 | Task 3 + Task 5 |
| spec §4.1 LLM 超时不污染 cache | Task 5(test_lookup_does_not_cache_when_compute_times_out) |
| spec §4.1 PII 跳过 L2 | Task 4(test_l2_put_skips_when_query_contains_pii)+ Task 5(PII skip lookup) |
| spec §4.1 audit flush 失败保留记录 | Task 6 |
| spec §4.2 OTel spans + 属性 | Task 8 |
| spec §4.3 Prometheus 指标名 | Task 9(指标名匹配) |
| spec §4.5 Alertmanager 规则 | Task 9 YAML |
| spec §4.6 PII 不进 L2 | Task 4 + Task 5 |
| spec §5 关键测试(PII/L1 降级/L2 语义命中/LLM 超时不污染/版本号) | Task 5 + Task 10 |

无占位符。所有代码块完整,所有命令有预期输出。类型一致性:`QueryCache.lookup_or_compute` 在 Task 5 定义,被 Task 7 调用,签名一致。`L1Cache.set/get`,`L2Cache.lookup/put` 命名一致。

---

## Execution Handoff

执行选项:
1. **Subagent-Driven(推荐)**:用 `superpowers:subagent-driven-development`,每个 Task 一个新鲜子代理,我做两级 review。
2. **Inline Execution**:在当前会话中按任务顺序执行,用 `superpowers:executing-plans`,批量加 checkpoint。

哪个?
