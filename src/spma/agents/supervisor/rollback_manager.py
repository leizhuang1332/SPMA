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
                logger.error(
                    "RollbackManager: version %s not found",
                    weights_set_id,
                )
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
            "Rolled back to version %s, new weights_version=%s, approver=%s",
            weights_set_id, new_v, approver,
        )
        return True
