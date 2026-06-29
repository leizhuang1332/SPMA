"""qr_state_meta + 缓存版本号工具。

设计依据: docs/superpowers/specs/2026-06-29-qr-cache-and-observability-design.md §2.2
"""

import hashlib
import json
import logging
from datetime import datetime

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
    # fingerprint 只看最近 3 轮(后缀):截取最后 32 字符,丢弃较早轮次
    fp_norm = history_fingerprint[-32:]
    raw = (
        query
        + fp_norm
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
    async with pool.acquire() as conn, conn.transaction():
        new_v = await conn.fetchval(
            "UPDATE qr_state_meta SET weights_version = weights_version + 1, "
            "updated_at = NOW() WHERE state_id = 1 RETURNING weights_version"
        )
        return int(new_v)


async def bump_synonym_version(pool) -> int:
    """自增 synonym_version 并返回新值,需要在事务内调用。"""
    async with pool.acquire() as conn, conn.transaction():
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
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("UPDATE qr_weights_history SET is_active = FALSE")
        new_id = await conn.fetchval(
            "INSERT INTO qr_weights_history (source, applied_at, approver, payload, is_active) "
            "VALUES ($1, $2, $3, $4::jsonb, TRUE) RETURNING weights_set_id",
            source, applied_at, approver, json.dumps(payload),
        )
        return int(new_id)
