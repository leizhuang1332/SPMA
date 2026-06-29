"""qr_request_audit 内存缓冲 + 异步 flush worker。

设计依据: docs/superpowers/specs/2026-06-29-qr-cache-and-observability-design.md §4.1, §4.3
"""

import asyncio
import json
import logging
from datetime import UTC, datetime

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
            record.setdefault("ts", datetime.now(UTC).isoformat())
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
