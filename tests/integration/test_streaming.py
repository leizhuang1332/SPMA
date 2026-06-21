"""SSE 双通道流式集成测试。"""
import json
import asyncio
import pytest
from unittest.mock import AsyncMock, patch, MagicMock


class FakeRedis:
    """模拟 Redis Pub/Sub。"""
    def __init__(self):
        self.channels: dict[str, list[str]] = {}

    def pubsub(self):
        pubsub = MagicMock()
        pubsub.subscribe = AsyncMock()
        pubsub.unsubscribe = AsyncMock()

        async def listen():
            # Yield any pre-published messages, then exit.
            # In production, listen() blocks forever waiting for new messages,
            # but in tests we want the progress channel to complete so the
            # merger doesn't deadlock when there are no (or only pre-existing)
            # progress events.
            for msg in self.channels.get("spma:progress:test-query", []):
                yield {"type": "message", "data": msg}

        pubsub.listen = listen
        return pubsub

    async def publish(self, channel: str, message: str) -> int:
        if channel not in self.channels:
            self.channels[channel] = []
        self.channels[channel].append(message)
        return 1


class FakeGraph:
    """模拟 LangGraph compiled graph。"""
    def __init__(self, events: list):
        self.events = events

    async def astream(self, input_state, config, stream_mode=None):
        for event in self.events:
            yield event


@pytest.mark.asyncio
async def test_stream_merger_graph_channel():
    """验证 StreamMerger 正确转发 graph 通道的 classification 事件。"""
    from spma.api.stream_merger import StreamMerger
    from langchain_core.messages import AIMessage

    fake_redis = FakeRedis()
    graph = FakeGraph([
        ("updates", {"classify": {
            "classification": {"sources": ["doc", "code"], "completeness": "complete"},
            "entities": {},
        }}),
        ("messages", (
            AIMessage(content="这是回答"),
            {"langgraph_node": "synthesis"},
        )),
    ])

    merger = StreamMerger(
        graph=graph,
        input_state={"messages": [], "original_query": "test"},
        config={"configurable": {"thread_id": "s1"}},
        redis_client=fake_redis,
        query_id="test-query",
    )

    events = []
    async for evt in merger.run():
        events.append(evt)

    event_types = [e["event"] for e in events if e["event"] != "keepalive"]
    assert "classification" in event_types, f"Expected classification, got {event_types}"
    assert "done" in event_types, f"Expected done, got {event_types}"


@pytest.mark.asyncio
async def test_stream_merger_progress_channel():
    """验证 StreamMerger 正确转发 Redis 进度事件。"""
    from spma.api.stream_merger import StreamMerger
    from spma.api.progress import ProgressPublisher

    fake_redis = FakeRedis()
    progress = ProgressPublisher(fake_redis, "test-query")
    await progress.publish_start("doc_worker")

    graph = FakeGraph([])

    merger = StreamMerger(
        graph=graph,
        input_state={"messages": [], "original_query": "test"},
        config={"configurable": {"thread_id": "s1"}},
        redis_client=fake_redis,
        query_id="test-query",
    )

    events = []
    async for evt in merger.run():
        events.append(evt)

    event_types = [e["event"] for e in events]
    assert "worker_start" in event_types, f"Expected worker_start, got {event_types}"


@pytest.mark.asyncio
async def test_stream_merger_graceful_without_redis():
    """验证 Redis 不可用时 StreamMerger 正常工作（仅数据通道）。"""
    from spma.api.stream_merger import StreamMerger
    from langchain_core.messages import AIMessage

    graph = FakeGraph([
        ("updates", {"classify": {
            "classification": {"sources": ["doc"], "completeness": "complete"},
            "entities": {},
        }}),
    ])

    merger = StreamMerger(
        graph=graph,
        input_state={"messages": [], "original_query": "test"},
        config={"configurable": {"thread_id": "s1"}},
        redis_client=None,
        query_id="test-query",
    )

    events = []
    async for evt in merger.run():
        events.append(evt)

    event_types = [e["event"] for e in events]
    assert "classification" in event_types
    assert "done" in event_types
    assert "worker_start" not in event_types, "Should not have worker_start without Redis"
