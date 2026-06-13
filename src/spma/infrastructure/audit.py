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
        logger.info(
            json.dumps(asdict(event), ensure_ascii=False, default=str)
        )
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
