"""审计日志——包装 QrAuditBuffer 加 PII hash 化(主文件 §3.10 最小特权)。"""
import hashlib
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class AuditLogger:
    def __init__(self, qr_audit_buffer, pii_detector):
        self._buffer = qr_audit_buffer
        self._pii = pii_detector

    async def log(
        self,
        request_id: str,
        original_query: str,
        rewritten: str | None,
        strategies_hit: list[str],
        weights_snapshot: dict,
        latency_ms: float,
    ):
        _, pii_types = self._pii.detect_and_mask(original_query)
        record = {
            "ts": datetime.utcnow().isoformat(),
            "request_id": request_id,
            "query_hash": hashlib.sha256(original_query.encode()).hexdigest()[:16],
            "query_length": len(original_query),
            "rewritten_hash": hashlib.sha256(rewritten.encode()).hexdigest()[:16] if rewritten else None,
            "strategies_hit": strategies_hit,
            "weights_snapshot": weights_snapshot,
            "latency_ms": latency_ms,
            "pii_types_detected": pii_types,
        }
        await self._buffer.enqueue(record)
