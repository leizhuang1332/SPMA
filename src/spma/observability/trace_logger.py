"""Agent Trace Logger——异步写入 PostgreSQL agent_traces / agent_rounds 表。"""

import asyncio
import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class AgentTraceLogger:
    """异步 Agent 执行轨迹记录器（参照 SearchLogger 模式）。"""

    def __init__(self, db_pool=None, max_queue: int = 500):
        self._db_pool = db_pool
        self._queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=max_queue)
        self._worker_task: asyncio.Task | None = None

    async def start(self):
        self._worker_task = asyncio.create_task(self._log_worker())

    async def stop(self):
        if self._worker_task:
            self._worker_task.cancel()
        while not self._queue.empty():
            entry = self._queue.get_nowait()
            await self._write_to_db(entry)

    async def log_query(self, query_id: str, state: dict):
        entry = {
            "table": "agent_traces",
            "query_id": query_id,
            "session_id": state.get("session_id", ""),
            "original_query": state.get("original_query", ""),
            "answer": state.get("answer", ""),
            "classification": state.get("classification", {}),
            "entities": state.get("entities", {}),
            "worker_outputs": state.get("worker_outputs", []),
            "quality_scores": state.get("quality_scores", {}),
            "reschedule_count": state.get("reschedule_count", 0),
            "total_llm_calls": state.get("total_llm_calls", 0),
            "total_tokens": state.get("total_tokens", 0),
            "convergence_reason": state.get("convergence_reason", ""),
            "latency_ms": state.get("latency_ms", 0),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            self._queue.put_nowait(entry)
        except asyncio.QueueFull:
            logger.warning("Trace 队列已满")

    async def log_round(self, query_id: str, agent_type: str, round_num: int, snapshot: dict):
        entry = {
            "table": "agent_rounds",
            "query_id": query_id,
            "agent_type": agent_type,
            "round_num": round_num,
            "action": snapshot.get("action", ""),
            "results_summary": json.dumps(snapshot.get("results", [])[:10], ensure_ascii=False, default=str)[:2048],
            "assessment": snapshot.get("assessment", ""),
            "confidence": snapshot.get("confidence", 0),
            "latency_ms": snapshot.get("latency_ms", 0),
            "llm_calls": snapshot.get("llm_calls", 0),
            "tokens_used": snapshot.get("tokens_used", 0),
        }
        try:
            self._queue.put_nowait(entry)
        except asyncio.QueueFull:
            pass

    async def _log_worker(self):
        while True:
            try:
                entry = await asyncio.wait_for(self._queue.get(), timeout=5.0)
                await self._write_to_db(entry)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Trace 写入失败: {e}")

    async def _write_to_db(self, entry: dict):
        if self._db_pool is None:
            logger.debug(f"TRACE_LOG: {json.dumps(entry, ensure_ascii=False, default=str)[:500]}")
            return
        try:
            async with self._db_pool.acquire() as conn:
                table = entry.pop("table")
                if table == "agent_traces":
                    await conn.execute(
                        """INSERT INTO agent_traces (query_id, session_id, original_query, answer,
                           classification, entities, worker_outputs, quality_scores,
                           reschedule_count, total_llm_calls, total_tokens,
                           convergence_reason, latency_ms)
                           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
                           ON CONFLICT (query_id) DO UPDATE SET
                           answer=$4, worker_outputs=$7, quality_scores=$8,
                           reschedule_count=$9, latency_ms=$13""",
                        entry["query_id"], entry["session_id"], entry["original_query"],
                        entry["answer"],
                        json.dumps(entry["classification"]), json.dumps(entry["entities"]),
                        json.dumps(entry["worker_outputs"]), json.dumps(entry["quality_scores"]),
                        entry["reschedule_count"], entry["total_llm_calls"],
                        entry["total_tokens"], entry["convergence_reason"],
                        entry["latency_ms"],
                    )
                elif table == "agent_rounds":
                    await conn.execute(
                        """INSERT INTO agent_rounds (query_id, agent_type, round_num, action,
                           results_summary, assessment, confidence, latency_ms, llm_calls, tokens_used)
                           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)""",
                        entry["query_id"], entry["agent_type"], entry["round_num"],
                        entry["action"], entry["results_summary"], entry["assessment"],
                        entry["confidence"], entry["latency_ms"], entry["llm_calls"],
                        entry["tokens_used"],
                    )
        except Exception as e:
            logger.error(f"DB trace 写入失败: {e}")
