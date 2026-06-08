"""检索日志——异步写入 PostgreSQL search_logs 表。"""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class SearchLogger:
    """异步检索日志记录器。"""

    def __init__(self, db_pool=None):
        self._db_pool = db_pool
        self._queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=1000)
        self._worker_task: asyncio.Task | None = None

    async def start(self) -> None:
        self._worker_task = asyncio.create_task(self._log_worker())

    async def stop(self) -> None:
        if self._worker_task:
            self._worker_task.cancel()
        while not self._queue.empty():
            entry = self._queue.get_nowait()
            await self._write_to_db(entry)

    async def log(self, entry_data: dict[str, Any]) -> None:
        entry: dict[str, Any] = {
            "log_id": str(uuid.uuid4()),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "worker_type": entry_data.get("worker_type", "doc"),
            "worker_version": entry_data.get("worker_version", "0.1"),
            "query_id": entry_data.get("query_id", ""),
            "query_text": entry_data.get("query_text", ""),
            "query_type": entry_data.get("query_type", "hybrid"),
            "trigger": entry_data.get("trigger", "user"),
            "entities": entry_data.get("entities", {}),
            "agent_rounds": entry_data.get("agent_rounds", 1),
            "convergence_reason": entry_data.get("convergence_reason", ""),
            "bm25_candidates": _extract_summary(entry_data.get("bm25_candidates", []), 20),
            "vector_candidates": _extract_summary(entry_data.get("vector_candidates", []), 20),
            "rrf_fused": _extract_summary(entry_data.get("rrf_fused", []), 10),
            "latency_ms": entry_data.get("latency_ms", 0),
            "feedback": entry_data.get("feedback", None),
        }

        try:
            self._queue.put_nowait(entry)
        except asyncio.QueueFull:
            logger.warning("检索日志队列已满 (1000)，丢弃一条日志")

    async def _log_worker(self) -> None:
        while True:
            try:
                entry = await asyncio.wait_for(self._queue.get(), timeout=5.0)
                await self._write_to_db(entry)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"日志写入失败: {e}")

    async def _write_to_db(self, entry: dict) -> None:
        if self._db_pool is None:
            logger.info(f"SEARCH_LOG: {json.dumps(entry, ensure_ascii=False)[:500]}")
            return

        try:
            async with self._db_pool.acquire() as conn:
                await conn.execute(
                    """INSERT INTO search_logs (log_id, timestamp, worker_type, query_text,
                       query_type, agent_rounds, convergence_reason, bm25_candidates,
                       vector_candidates, rrf_fused, latency_ms, entities)
                       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)""",
                    entry["log_id"], entry["timestamp"], entry["worker_type"],
                    entry["query_text"], entry["query_type"], entry["agent_rounds"],
                    entry["convergence_reason"],
                    json.dumps(entry["bm25_candidates"], ensure_ascii=False),
                    json.dumps(entry["vector_candidates"], ensure_ascii=False),
                    json.dumps(entry["rrf_fused"], ensure_ascii=False),
                    entry["latency_ms"],
                    json.dumps(entry["entities"], ensure_ascii=False),
                )
        except Exception as e:
            logger.error(f"DB 日志写入失败: {e}")


def _extract_summary(candidates: list[dict], max_count: int) -> list[dict]:
    return [
        {
            "chunk_id": c.get("chunk_id", ""),
            "score": c.get("score", 0),
            "snippet": str(c.get("snippet", c.get("content", "")))[:200],
        }
        for c in candidates[:max_count]
    ]
