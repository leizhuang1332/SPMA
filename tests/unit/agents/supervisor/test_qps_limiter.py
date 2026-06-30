"""QPSLimiter 单测。"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from spma.agents.supervisor.qps_limiter import QPSLimiter


@pytest.fixture
def mock_redis():
    redis = MagicMock()
    # 第一次检查:zcard 返回 0(未超限)
    redis.zcard = AsyncMock(return_value=0)
    redis.zadd = AsyncMock(return_value=1)
    redis.zremrangebyscore = AsyncMock(return_value=0)
    redis.expire = AsyncMock(return_value=1)
    return redis


@pytest.mark.asyncio
async def test_default_qps_allows_under_limit(mock_redis):
    """未达上限 → 允许。"""
    limiter = QPSLimiter(mock_redis, default_qps=10)
    allowed = await limiter.check("tenant_a", "user_a")
    assert allowed is True
    mock_redis.zadd.assert_called_once()


@pytest.mark.asyncio
async def test_default_qps_rejects_over_limit():
    """超限 → 拒绝。"""
    redis = MagicMock()
    redis.zcard = AsyncMock(return_value=10)  # 已达上限
    redis.zremrangebyscore = AsyncMock(return_value=0)
    limiter = QPSLimiter(redis, default_qps=10)
    allowed = await limiter.check("tenant_a", "user_a")
    assert allowed is False


@pytest.mark.asyncio
async def test_vip_tenant_higher_limit(mock_redis):
    """VIP 租户 QPS 上限 50。"""
    mock_redis.zcard = AsyncMock(return_value=20)
    limiter = QPSLimiter(mock_redis, default_qps=10, vip_tenants={"vip_a"})
    allowed = await limiter.check("vip_a", "user_a")
    assert allowed is True  # 20 < 50
