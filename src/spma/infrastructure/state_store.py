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
