"""会话相关 Schema: SessionCreateRequest, SessionCreateResponse, SessionRecord, QueryRecord。

设计依据: API-01 §4 会话管理 + API contract SessionRecord/QueryRecord schemas
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class SessionCreateRequest(BaseModel):
    """创建会话请求体（API contract: POST /sessions requestBody）。"""
    title: str | None = None


class SessionCreateResponse(BaseModel):
    """创建会话响应体（API contract: 201 response）。"""
    session_id: str
    created_at: str


class QueryRecord(BaseModel):
    """单次查询记录（对应 API contract QueryRecord schema）。

    字段对齐 agent_traces 表的列 + 前端 types/api.ts QueryRecord 接口。
    """
    query_id: str
    session_id: str | None = None
    query_text: str
    answer: str | None = None
    sources: list[dict] = Field(default_factory=list)
    classification: dict | None = None
    degradation: dict | None = None
    sql_executed: str | None = None
    latency_ms: int | None = None
    user_feedback: str | None = None
    created_at: str


class SessionRecord(BaseModel):
    """会话历史记录（对应 API contract SessionRecord schema）。

    包含完整的 turns 列表，一次性返回给前端。
    """
    session_id: str
    turns: list[QueryRecord] = Field(default_factory=list)
    created_at: str
    updated_at: str
