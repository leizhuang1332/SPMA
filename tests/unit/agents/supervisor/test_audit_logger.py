"""AuditLogger 单测(主文件 §3.10 最小特权)。"""
import hashlib
import pytest
from unittest.mock import AsyncMock, MagicMock

from spma.agents.supervisor.audit_logger import AuditLogger


def test_logs_hashed_query_not_raw():
    """审计日志存 query hash,不是原文。"""
    buffer = MagicMock()
    buffer.enqueue = AsyncMock()
    pii = MagicMock()
    pii.detect_and_mask = MagicMock(return_value=("masked", []))

    al = AuditLogger(buffer, pii)
    import asyncio
    asyncio.run(al.log(
        request_id="req-1",
        original_query="my secret phone 13800138000",
        rewritten="rewritten text",
        strategies_hit=["rule_based"],
        weights_snapshot={"a": 0.5},
        latency_ms=10.5,
    ))

    buffer.enqueue.assert_called_once()
    record = buffer.enqueue.call_args[0][0]
    # 不应有原文
    assert "13800138000" not in str(record)
    # 应有 hash
    expected_hash = hashlib.sha256(b"my secret phone 13800138000").hexdigest()[:16]
    assert record["query_hash"] == expected_hash


def test_records_pii_types_detected():
    buffer = MagicMock()
    buffer.enqueue = AsyncMock()
    pii = MagicMock()
    pii.detect_and_mask = MagicMock(return_value=("masked", ["phone_cn"]))

    al = AuditLogger(buffer, pii)
    import asyncio
    asyncio.run(al.log("req-2", "test", None, [], {}, 1.0))

    record = buffer.enqueue.call_args[0][0]
    assert record["pii_types_detected"] == ["phone_cn"]
