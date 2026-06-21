"""ProgressPublisher 单元测试。"""
import pytest
from spma.api.progress import ProgressPublisher, ProgressEvent


class FakeRedis:
    """模拟 Redis 客户端——记录 publish 调用。"""
    def __init__(self, should_fail: bool = False):
        self.published: list[tuple[str, str]] = []
        self.should_fail = should_fail

    async def publish(self, channel: str, message: str) -> int:
        if self.should_fail:
            raise ConnectionError("fake redis down")
        self.published.append((channel, message))
        return 1


@pytest.mark.asyncio
async def test_publisher_with_none_redis():
    """redis=None 时 publish 不抛异常。"""
    p = ProgressPublisher(redis_client=None, query_id="q1")
    await p.publish_start("doc_worker")
    await p.publish_step("doc_worker", "searching", "test")
    await p.publish_thinking("synthesis", "thinking...")


@pytest.mark.asyncio
async def test_publisher_publishes_to_correct_channel():
    """验证发布到正确的 Redis channel。"""
    redis = FakeRedis()
    p = ProgressPublisher(redis_client=redis, query_id="q123")

    await p.publish_start("doc_worker")

    assert len(redis.published) == 1
    channel, data = redis.published[0]
    assert channel == "spma:progress:q123"
    import json
    event = json.loads(data)
    assert event["event_type"] == "worker_start"
    assert event["node"] == "doc_worker"


@pytest.mark.asyncio
async def test_publisher_silently_catches_redis_error():
    """Redis 异常被静默 catch，不向上传播。"""
    redis = FakeRedis(should_fail=True)
    p = ProgressPublisher(redis_client=redis, query_id="q1")
    await p.publish_start("doc_worker")
    await p.publish_step("doc_worker", "searching", "test")


@pytest.mark.asyncio
async def test_publish_step_includes_stats():
    """publish_step 正确传递 stats。"""
    redis = FakeRedis()
    p = ProgressPublisher(redis_client=redis, query_id="q1")

    await p.publish_step("doc_worker", "aggregating",
                         message="正在聚合…",
                         stats={"found": 12, "round": 2})

    assert len(redis.published) == 1
    _, data = redis.published[0]
    import json
    event = json.loads(data)
    assert event["event_type"] == "worker_progress"
    assert event["step"] == "aggregating"
    assert event["stats"] == {"found": 12, "round": 2}


@pytest.mark.asyncio
async def test_publish_thinking():
    """publish_thinking 正确传递 thinking chunk。"""
    redis = FakeRedis()
    p = ProgressPublisher(redis_client=redis, query_id="q1")

    await p.publish_thinking("synthesis", "我需要综合…")

    assert len(redis.published) == 1
    _, data = redis.published[0]
    import json
    event = json.loads(data)
    assert event["event_type"] == "thinking"
    assert event["thinking_chunk"] == "我需要综合…"
