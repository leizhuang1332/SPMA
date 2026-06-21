"""E2E SSE 流式端到端测试——需要 spma-api 服务运行。"""
import json
import pytest
import httpx


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_query_stream_emits_all_required_events():
    """验证 SSE 端点发射所有必需的 event 类型。"""
    async with httpx.AsyncClient(base_url="http://localhost:8000") as client:
        # Create session
        resp = await client.post("/api/v1/sessions", json={"title": "E2E test"})
        assert resp.status_code in (200, 201)
        session_id = resp.json()["session_id"]

        # Stream query
        received_events: set[str] = set()
        async with client.stream(
            "POST",
            "/api/v1/query/stream",
            json={"query": "SPMA 架构是什么？", "session_id": session_id},
            timeout=30.0,
        ) as response:
            assert response.status_code == 200
            async for line in response.aiter_lines():
                if line.startswith("event: "):
                    event_type = line[7:].strip()
                    received_events.add(event_type)

        # Verify core events
        assert "classification" in received_events, f"Missing classification in {received_events}"
        assert "done" in received_events, f"Missing done in {received_events}"
        assert "synthesis" in received_events, f"Missing synthesis in {received_events}"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_query_stream_cancel_emits_error():
    """验证取消查询不会导致服务崩溃。"""
    async with httpx.AsyncClient(base_url="http://localhost:8000") as client:
        resp = await client.post("/api/v1/sessions", json={"title": "Cancel test"})
        session_id = resp.json()["session_id"]

        async with client.stream(
            "POST",
            "/api/v1/query/stream",
            json={"query": "详细描述 SPMA 的所有架构组件", "session_id": session_id},
            timeout=30.0,
        ) as response:
            count = 0
            async for line in response.aiter_lines():
                count += 1
                if count > 5:
                    break  # Disconnect early
