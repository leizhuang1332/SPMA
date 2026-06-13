"""审计日志测试。"""
import json
import pytest
from spma.infrastructure.audit import AuditLogger, AuditEvent


class TestAuditLogger:
    """测试 AuditLogger 核心功能。"""

    @pytest.mark.asyncio
    async def test_log_enqueues_for_db(self):
        """log() 将事件入队待批量写 PG。"""
        al = AuditLogger()
        await al.log(AuditEvent(
            event_type="circuit_breaker.open",
            details={"breaker_name": "llm_sonnet"},
        ))
        assert len(al._queue) == 1

    @pytest.mark.asyncio
    async def test_log_is_non_blocking(self):
        """log() 在无 DB 时也不抛异常（非阻塞）。"""
        al = AuditLogger()  # db_pool=None
        await al.log(AuditEvent(event_type="degradation.recovered", level="L0"))

    @pytest.mark.asyncio
    async def test_flush_clears_queue(self):
        """flush 清空队列。"""
        al = AuditLogger(batch_size=2)
        await al.log(AuditEvent(event_type="feature_flag.changed"))
        await al.log(AuditEvent(event_type="feature_flag.changed"))
        assert len(al._queue) == 2
        await al._flush()
        assert len(al._queue) == 0

    @pytest.mark.asyncio
    async def test_event_serialization(self):
        """AuditEvent 可 JSON 序列化。"""
        event = AuditEvent(
            event_type="degradation.manual",
            level="L3",
            details={"reason": "scheduled maintenance"},
            operator="admin",
        )
        d = json.dumps(event.__dict__, default=str)
        assert "degradation.manual" in d
        assert "L3" in d
        assert "admin" in d
