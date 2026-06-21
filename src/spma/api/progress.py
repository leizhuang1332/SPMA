# src/spma/api/progress.py
"""Redis Pub/Sub 进度发布器——类型安全的进度事件推送。

核心设计：
- 静默降级：Redis 异常不抛出，进度故障不影响回答质量
- 无 Redis 模式：redis_client=None 时 _publish() 直接 return
- Channel 命名：spma:progress:{query_id}，天然隔离不同查询
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ProgressEvent:
    """进度事件——序列化为 JSON 通过 Redis Pub/Sub 发送。"""
    query_id: str
    event_type: str  # "worker_start" | "worker_progress" | "thinking"
    node: str        # "doc_worker" | "code_worker" | "sql_worker" | "synthesis"
    timestamp: str = ""
    step: str | None = None          # 子步骤名: "routing" | "searching" | ...
    message: str | None = None       # 人类可读描述
    stats: dict | None = None        # {"found": 12, "round": 2}
    thinking_chunk: str | None = None

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()


class ProgressPublisher:
    """类型安全的 Redis Pub/Sub 进度发布器。

    用法:
        progress = ProgressPublisher(redis_client, "query-123")
        await progress.publish_start("doc_worker")
        await progress.publish_step("doc_worker", "searching", "正在检索…", {"found": 12})
        await progress.publish_thinking("synthesis", "我需要综合…")
    """

    def __init__(self, redis_client: Any | None, query_id: str):
        self._redis = redis_client
        self.query_id = query_id

    @property
    def channel(self) -> str:
        return f"spma:progress:{self.query_id}"

    async def _publish(self, event: ProgressEvent) -> None:
        """非阻塞发布——Redis 挂了也不影响主流程。"""
        if self._redis is None:
            return
        try:
            data = json.dumps(asdict(event), ensure_ascii=False, default=str)
            await self._redis.publish(self.channel, data)
        except Exception:
            pass  # 静默吞下——进度流故障不影响主业务

    # ── 便捷方法 ──────────────────────────────────────────────

    async def publish_start(self, node: str) -> None:
        """发布 worker 启动事件。"""
        await self._publish(ProgressEvent(
            query_id=self.query_id,
            event_type="worker_start",
            node=node,
        ))

    async def publish_step(
        self, node: str, step: str, message: str = "", stats: dict | None = None
    ) -> None:
        """发布子步骤进度事件。"""
        await self._publish(ProgressEvent(
            query_id=self.query_id,
            event_type="worker_progress",
            node=node,
            step=step,
            message=message,
            stats=stats,
        ))

    async def publish_thinking(self, node: str, chunk: str) -> None:
        """发布模型思考 token 事件。"""
        await self._publish(ProgressEvent(
            query_id=self.query_id,
            event_type="thinking",
            node=node,
            thinking_chunk=chunk,
        ))
