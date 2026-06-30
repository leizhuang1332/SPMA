"""RollbackManager 单测。"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from spma.agents.supervisor.rollback_manager import RollbackManager


@pytest.fixture
def mock_pool():
    pool = MagicMock()
    conn = MagicMock()
    conn.execute = AsyncMock(return_value=None)
    conn.fetchrow = AsyncMock(return_value={"payload": json.dumps({"weights": {"a": 0.5, "b": 0.5}})})
    conn.fetchval = AsyncMock(return_value=3)
    conn.fetch = AsyncMock(return_value=[])
    acquire_ctx = MagicMock()
    acquire_ctx.__aenter__ = AsyncMock(return_value=conn)
    acquire_ctx.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=acquire_ctx)
    transaction_ctx = MagicMock()
    transaction_ctx.__aenter__ = AsyncMock(return_value=None)
    transaction_ctx.__aexit__ = AsyncMock(return_value=None)
    conn.transaction = MagicMock(return_value=transaction_ctx)
    return pool


@pytest.mark.asyncio
async def test_rollback_to_writes_new_active_and_bumps_version(mock_pool):
    rm = RollbackManager(mock_pool, max_versions=10)
    success = await rm.rollback_to(weights_set_id=5, approver="alice")
    assert success is True
    # 应写 INSERT 新 active 记录
    mock_pool.acquire.assert_called()


@pytest.mark.asyncio
async def test_rollback_to_returns_false_when_version_not_found(mock_pool):
    mock_pool.acquire.return_value.__aenter__.return_value.fetchrow = AsyncMock(return_value=None)
    rm = RollbackManager(mock_pool, max_versions=10)
    success = await rm.rollback_to(weights_set_id=999, approver="alice")
    assert success is False


@pytest.mark.asyncio
async def test_list_versions_returns_history(mock_pool):
    from datetime import datetime
    mock_pool.acquire.return_value.__aenter__.return_value.fetch = AsyncMock(return_value=[
        {"weights_set_id": 1, "created_at": datetime.now(), "source": "ema",
         "approver": None, "payload": "{}"},
        {"weights_set_id": 2, "created_at": datetime.now(), "source": "manual",
         "approver": "alice", "payload": "{}"},
    ])
    rm = RollbackManager(mock_pool, max_versions=10)
    versions = await rm.list_versions()
    assert len(versions) == 2
    assert versions[0]["weights_set_id"] == 1
