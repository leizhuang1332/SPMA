"""qr_request_audit 内存缓冲 + 5s flush 测试。"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from spma.agents.supervisor.qr_audit import QrAuditBuffer


@pytest.mark.asyncio
async def test_enqueue_does_not_block_on_db():
    """enqueue 不应阻塞 hot path(异步入队即可)。"""
    buf = QrAuditBuffer(pool=None, flush_interval_s=3600)
    await buf.enqueue({"request_id": "1", "ts": "now"})
    assert len(buf._queue) == 1


@pytest.mark.asyncio
async def test_flush_inserts_all_records_to_qr_request_audit():
    pool = MagicMock()
    conn = AsyncMock()
    # executemany 时 fetch 不到 row
    conn.executemany = AsyncMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

    buf = QrAuditBuffer(pool=pool, flush_interval_s=3600, batch_size=10)
    for i in range(3):
        await buf.enqueue({"request_id": str(i), "ts": "now", "stage": "rewrite"})
    await buf._flush()

    assert len(buf._queue) == 0
    conn.executemany.assert_awaited_once()
    sql = conn.executemany.await_args.args[0]
    assert "qr_request_audit" in sql.lower()


@pytest.mark.asyncio
async def test_flush_swallows_db_errors_and_retains_records(caplog):
    """PG 不可用时,记录保留在内存,下次启动再决定丢弃/落盘。"""
    pool = MagicMock()
    conn = AsyncMock()
    conn.executemany = AsyncMock(side_effect=Exception("pg down"))
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

    buf = QrAuditBuffer(pool=pool, flush_interval_s=3600)
    await buf.enqueue({"request_id": "1", "ts": "now"})
    await buf._flush()
    assert len(buf._queue) == 1  # 保留以便下次重试


@pytest.mark.asyncio
async def test_flush_respects_batch_size():
    pool = MagicMock()
    conn = AsyncMock()
    conn.executemany = AsyncMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

    buf = QrAuditBuffer(pool=pool, flush_interval_s=3600, batch_size=2)
    for i in range(5):
        await buf.enqueue({"request_id": str(i), "ts": "now"})
    await buf._flush()
    # 一次只 flush batch_size 条
    assert len(buf._queue) == 3
