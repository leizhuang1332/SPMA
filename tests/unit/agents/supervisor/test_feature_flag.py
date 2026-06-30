"""StrategyFeatureFlag 单测。"""
import pytest
import time
from unittest.mock import AsyncMock, MagicMock

from spma.agents.supervisor.feature_flag import StrategyFeatureFlag


@pytest.fixture
def mock_redis():
    redis = MagicMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock(return_value=True)
    return redis


@pytest.mark.asyncio
async def test_default_enabled_when_no_flag(mock_redis):
    """Redis 无 flag → 默认 enabled=True。"""
    ff = StrategyFeatureFlag(mock_redis)
    assert await ff.is_enabled("any_strategy") is True


@pytest.mark.asyncio
async def test_disabled_flag_returns_false(mock_redis):
    """flag={enabled: False} → 返回 False。"""
    import json
    mock_redis.get = AsyncMock(return_value=json.dumps({"enabled": False, "rollout_pct": 100}))
    ff = StrategyFeatureFlag(mock_redis)
    assert await ff.is_enabled("any_strategy") is False


@pytest.mark.asyncio
async def test_rollout_pct_filters_by_bucket(mock_redis):
    """rollout_pct=10 → bucket<10 通过,bucket>=10 不通过。"""
    import json
    mock_redis.get = AsyncMock(return_value=json.dumps({"enabled": True, "rollout_pct": 10}))
    ff = StrategyFeatureFlag(mock_redis)
    assert await ff.is_enabled("s", user_bucket=5) is True
    assert await ff.is_enabled("s", user_bucket=50) is False


@pytest.mark.asyncio
async def test_local_cache_avoids_redis_hit(mock_redis):
    """5s 内重复调用 → 只打 1 次 Redis。"""
    import json
    mock_redis.get = AsyncMock(return_value=json.dumps({"enabled": True, "rollout_pct": 100}))
    ff = StrategyFeatureFlag(mock_redis, local_cache_ttl=5)
    await ff.is_enabled("s")
    await ff.is_enabled("s")
    await ff.is_enabled("s")
    assert mock_redis.get.call_count == 1


@pytest.mark.asyncio
async def test_set_rollout_clears_local_cache(mock_redis):
    """set_rollout 后立即生效(清空本地缓存)。"""
    import json
    mock_redis.get = AsyncMock(return_value=json.dumps({"enabled": True, "rollout_pct": 100}))
    ff = StrategyFeatureFlag(mock_redis, local_cache_ttl=5)
    await ff.is_enabled("s")
    mock_redis.get.call_count = 1
    await ff.set_rollout("s", rollout_pct=0, enabled=False)
    mock_redis.get.call_count = 0  # 重置计数器
    await ff.is_enabled("s")
    assert mock_redis.get.call_count == 1  # 重新打 Redis


def test_user_bucket_is_stable():
    """同一 user_id 始终映射到同一 bucket(0-99)。"""
    ff = StrategyFeatureFlag(MagicMock())
    b1 = ff.user_bucket("user_123")
    b2 = ff.user_bucket("user_123")
    assert b1 == b2
    assert 0 <= b1 < 100
