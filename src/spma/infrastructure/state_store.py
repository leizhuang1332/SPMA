"""三层状态存储——进程内存 → Redis热状态 → PostgreSQL冷trace。

Layer 1: ProcessMemoryStore (Phase 1, Python dict, 无外部依赖)
Layer 2: RedisHotStore (Phase 2+, Write-through, TTL=5min)
Layer 3: PostgresColdStore (Phase 3+, Write-back, 异步写入)

降级: Redis 不可用 → 自动降级到进程内存，标注 degradation level

设计依据: SPMA-design-06 §2 Checkpointer隔离 + SPMA-design-07 §5 状态管理
"""

from typing import Protocol


class StateStorageProtocol(Protocol):
    """状态存储的抽象接口——三层实现共用。"""

    async def save(self, key: str, state: dict, ttl: int | None = None) -> None: ...
    async def load(self, key: str) -> dict | None: ...
    async def delete(self, key: str) -> None: ...
    async def health_check(self) -> bool: ...


# ============================================================
# 确认闸门 Token 存储——Slice 3
# ============================================================

import uuid
import time


class ConfirmationTokenStore:
    """确认闸门的 token → state 映射存储。

    Slice 3: 内存 dict 实现（单进程）。Phase 2 迁移到 Redis。
    """

    def __init__(self):
        self._store: dict[str, dict] = {}

    def save(self, state: dict, ttl_seconds: int = 180) -> str:
        """保存状态，返回 token。"""
        token = f"tok_{uuid.uuid4().hex[:12]}"
        self._store[token] = {
            "state": state,
            "expires_at": time.time() + ttl_seconds,
            "original_query": state.get("original_query", state.get("query", "")),
        }
        return token

    def load(self, token: str) -> dict | None:
        """加载状态，检查过期。"""
        entry = self._store.get(token)
        if entry is None:
            return None
        if time.time() > entry["expires_at"]:
            del self._store[token]
            return None
        return entry

    def delete(self, token: str) -> None:
        """删除状态。"""
        self._store.pop(token, None)


# 全局单例
confirmation_store = ConfirmationTokenStore()
