"""会话端点——POST /api/v1/sessions, GET + DELETE /api/v1/sessions/{session_id}。

设计依据: API-01 §4 会话管理 + API contract POST/GET/DELETE /sessions
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from spma.api.dependencies import get_session_store
from spma.api.middleware.auth import get_current_user
from spma.api.schemas.session import (
    SessionCreateRequest,
    SessionCreateResponse,
    SessionRecord,
)
from spma.api.session_store import SessionStore

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/sessions", status_code=201, response_model=SessionCreateResponse)
async def create_session(
    body: SessionCreateRequest | None = None,
    store: SessionStore = Depends(get_session_store),
    _user: dict = Depends(get_current_user),
):
    """POST /api/v1/sessions — 创建新会话。

    返回 session_id 和 created_at。
    前端用于导航到 /chat/{session_id}。
    """
    title = body.title if body else None
    session_id = await store.create_session(title=title)
    session = await store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=500, detail="Failed to create session")
    return SessionCreateResponse(
        session_id=session["session_id"],
        created_at=session["created_at"],
    )


@router.get("/sessions/{session_id}", response_model=SessionRecord)
async def get_session(
    session_id: str,
    store: SessionStore = Depends(get_session_store),
    _user: dict = Depends(get_current_user),
):
    """GET /api/v1/sessions/{session_id} — 获取会话历史。

    返回完整 SessionRecord，包含所有 turns（按时间升序排列）。
    会话不存在时返回 404。
    """
    session = await store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    return SessionRecord(**session)


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(
    session_id: str,
    store: SessionStore = Depends(get_session_store),
    _user: dict = Depends(get_current_user),
):
    """DELETE /api/v1/sessions/{session_id} — 删除会话及其所有关联查询。

    返回 204 No Content。
    会话不存在时返回 404。
    """
    deleted = await store.delete_session(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
