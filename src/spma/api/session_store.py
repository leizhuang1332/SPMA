"""SessionStore — 管理 sessions 表的 CRUD 以及 agent_traces 的 turns 聚合查询。

遵循 run_store.py 模式：raw SQL + asyncpg pool，非 ORM。
当 db_pool 不可用时自动降级到进程内存存储（匹配 RedisStateStore 降级模式）。
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

import asyncpg

logger = logging.getLogger(__name__)


class SessionStore:
    """会话持久化存储——PostgreSQL 优先，无 db_pool 时降级到内存。

    通过 asyncpg pool 直接操作 PostgreSQL。
    session_id 使用 UUID 格式，与 API contract / 前端对齐。
    """

    def __init__(self, db_pool: asyncpg.Pool | None = None):
        self._db_pool = db_pool
        # 内存降级存储: {session_id: {session_id, title, created_at, updated_at}}
        self._memory_sessions: dict[str, dict] = {}
        # 内存降级存储: {session_id: [turn_dict, ...]}
        self._memory_turns: dict[str, list[dict]] = {}
        if db_pool is None:
            logger.warning("SessionStore: db_pool 未传入，使用进程内存存储（重启后数据丢失）")

    @property
    def _use_db(self) -> bool:
        return self._db_pool is not None

    async def create_session(self, title: str | None = None, user_id: str = "") -> str:
        """创建新会话，返回 session_id (UUID)。"""
        session_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        if self._use_db:
            async with self._db_pool.acquire() as conn:
                await conn.execute(
                    """INSERT INTO sessions (session_id, title, user_id, metadata, created_at, updated_at)
                       VALUES ($1, $2, $3, '{}', $4, $4)""",
                    session_id, title, user_id, now,
                )
        else:
            now_iso = now.isoformat()
            self._memory_sessions[session_id] = {
                "session_id": session_id,
                "title": title,
                "user_id": user_id,
                "created_at": now_iso,
                "updated_at": now_iso,
            }
            self._memory_turns[session_id] = []

        logger.info("Session created: %s (db=%s)", session_id, self._use_db)
        return session_id

    async def get_session(self, session_id: str) -> dict | None:
        """获取会话元数据 + 聚合 turns。返回对齐 SessionRecord 的 dict。"""
        if self._use_db:
            return await self._get_session_db(session_id)
        return self._get_session_memory(session_id)

    async def _get_session_db(self, session_id: str) -> dict | None:
        async with self._db_pool.acquire() as conn:
            session_row = await conn.fetchrow(
                "SELECT session_id, title, created_at, updated_at FROM sessions WHERE session_id = $1",
                session_id,
            )
            if session_row is None:
                return None

            trace_rows = await conn.fetch(
                """SELECT query_id, session_id, original_query, answer,
                          worker_outputs, classification, latency_ms, created_at
                   FROM agent_traces
                   WHERE session_id = $1
                   ORDER BY created_at ASC""",
                session_id,
            )

        turns = _build_turns(trace_rows)
        created_at = session_row["created_at"]
        updated_at = session_row["updated_at"]

        return {
            "session_id": session_row["session_id"],
            "title": session_row["title"],
            "turns": turns,
            "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at),
            "updated_at": updated_at.isoformat() if hasattr(updated_at, "isoformat") else str(updated_at),
        }

    def _get_session_memory(self, session_id: str) -> dict | None:
        session = self._memory_sessions.get(session_id)
        if session is None:
            return None
        return {
            "session_id": session["session_id"],
            "title": session.get("title"),
            "turns": self._memory_turns.get(session_id, []),
            "created_at": session["created_at"],
            "updated_at": session["updated_at"],
        }

    async def delete_session(self, session_id: str) -> bool:
        """删除会话及其关联的 traces。返回 True 如果存在并被删除。"""
        if self._use_db:
            return await self._delete_session_db(session_id)

        if session_id not in self._memory_sessions:
            return False
        del self._memory_sessions[session_id]
        self._memory_turns.pop(session_id, None)
        logger.info("Session deleted (memory): %s", session_id)
        return True

    async def _delete_session_db(self, session_id: str) -> bool:
        async with self._db_pool.acquire() as conn:
            async with conn.transaction():
                existing = await conn.fetchrow(
                    "SELECT session_id FROM sessions WHERE session_id = $1", session_id
                )
                if existing is None:
                    return False
                await conn.execute(
                    """DELETE FROM agent_rounds
                       WHERE query_id IN (
                           SELECT query_id FROM agent_traces WHERE session_id = $1
                       )""",
                    session_id,
                )
                await conn.execute(
                    "DELETE FROM agent_traces WHERE session_id = $1", session_id
                )
                await conn.execute(
                    "DELETE FROM sessions WHERE session_id = $1", session_id
                )
        logger.info("Session deleted (db): %s", session_id)
        return True

    async def session_exists(self, session_id: str) -> bool:
        """快速检查会话是否存在。"""
        if self._use_db:
            async with self._db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT 1 FROM sessions WHERE session_id = $1", session_id
                )
                return row is not None
        return session_id in self._memory_sessions

    async def update_session_title(self, session_id: str, title: str) -> None:
        """更新会话标题。"""
        now = datetime.now(timezone.utc)
        if self._use_db:
            async with self._db_pool.acquire() as conn:
                await conn.execute(
                    "UPDATE sessions SET title = $1, updated_at = $2 WHERE session_id = $3",
                    title, now, session_id,
                )
        elif session_id in self._memory_sessions:
            self._memory_sessions[session_id]["title"] = title
            self._memory_sessions[session_id]["updated_at"] = now.isoformat()

    # ---- 内部方法：供 query.py trace 完成后写入 turn 到内存 ----

    def add_turn_memory(self, session_id: str, turn: dict) -> None:
        """仅内存模式：在查询完成后将 turn 追加到内存 turns 列表。

        db 模式下由 agent_traces 表承担此角色，无需此方法。
        """
        if session_id not in self._memory_turns:
            self._memory_turns[session_id] = []
        self._memory_turns[session_id].append(turn)
        # 更新 updated_at
        if session_id in self._memory_sessions:
            self._memory_sessions[session_id]["updated_at"] = datetime.now(timezone.utc).isoformat()


def _build_turns(trace_rows) -> list[dict]:
    """将 agent_traces 行转换为 QueryRecord-compatible dict 列表。"""
    turns = []
    for row in trace_rows:
        worker_outputs_raw = row["worker_outputs"]
        if isinstance(worker_outputs_raw, str):
            try:
                worker_outputs = json.loads(worker_outputs_raw)
            except json.JSONDecodeError:
                worker_outputs = []
        else:
            worker_outputs = worker_outputs_raw or []

        classification_raw = row["classification"]
        if isinstance(classification_raw, str):
            try:
                classification = json.loads(classification_raw)
            except json.JSONDecodeError:
                classification = None
        else:
            classification = classification_raw

        created_at = row["created_at"]
        turns.append({
            "query_id": row["query_id"],
            "session_id": row["session_id"],
            "query_text": row["original_query"],
            "answer": row["answer"] or "",
            "sources": _extract_sources(worker_outputs),
            "classification": classification,
            "degradation": None,
            "sql_executed": None,
            "latency_ms": row["latency_ms"] or 0,
            "user_feedback": "none",
            "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at),
        })
    return turns


def _extract_sources(worker_outputs: list[dict]) -> list[dict]:
    """从 worker_outputs 中提取可引用的来源列表。"""
    sources: list[dict] = []
    for output in worker_outputs:
        citations = output.get("citations", [])
        for c in citations:
            if isinstance(c, dict):
                sources.append(c)
    return sources
