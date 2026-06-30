"""HumanInTheLoopValidator 单测(主文件 ADR-006)。"""
import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from spma.agents.supervisor.human_validator import HumanInTheLoopValidator


@pytest.fixture
def mock_pool():
    """标准化 mock pool,所有测试复用。"""
    pool = MagicMock()
    conn = MagicMock()
    conn.execute = AsyncMock(return_value=None)
    conn.fetchval = AsyncMock(return_value=2)
    acquire_ctx = MagicMock()
    acquire_ctx.__aenter__ = AsyncMock(return_value=conn)
    acquire_ctx.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=acquire_ctx)
    transaction_ctx = MagicMock()
    transaction_ctx.__aenter__ = AsyncMock(return_value=None)
    transaction_ctx.__aexit__ = AsyncMock(return_value=None)
    conn.transaction = MagicMock(return_value=transaction_ctx)
    return pool


def test_should_review_true_for_large_delta():
    hv = HumanInTheLoopValidator(mock_pool, timeout_seconds=86400)
    diffs = {"a": {"delta": 0.15}, "b": {"delta": -0.05}}
    assert hv.should_review(diffs) is True  # total = 0.20 > 0.1


def test_should_review_false_for_small_delta():
    hv = HumanInTheLoopValidator(mock_pool, timeout_seconds=86400)
    diffs = {"a": {"delta": 0.05}, "b": {"delta": -0.02}}
    assert hv.should_review(diffs) is False  # total = 0.07 < 0.1


@pytest.mark.asyncio
async def test_submit_creates_pending_ticket(mock_pool):
    hv = HumanInTheLoopValidator(mock_pool, timeout_seconds=86400)
    diffs = {"a": {"old": 0.3, "new": 0.4, "delta": 0.1}}
    evaluation = {"a": {"avg_score": 0.8, "count": 100}}
    ticket_id = await hv.submit_for_review(diffs, evaluation)
    assert ticket_id.startswith("qr-review-")
    assert ticket_id in hv._pending
    assert hv._pending[ticket_id]["status"] == "pending"
    # 清理:避免后台 scan_task 在 fixture 销毁后还跑
    await hv.stop()


@pytest.mark.asyncio
async def test_approve_writes_active_snapshot_and_bumps_version(mock_pool):
    """approve 写 is_active=TRUE 的新快照并 bump_weights_version(精确断言)。"""
    hv = HumanInTheLoopValidator(mock_pool, timeout_seconds=86400)
    ticket_id = await hv.submit_for_review(
        {"a": {"old": 0.3, "new": 0.4, "delta": 0.1}},
        {"a": {"avg_score": 0.8, "count": 100}},
    )

    with patch(
        "spma.agents.supervisor.human_validator.write_weights_snapshot"
    ) as mock_snapshot, patch(
        "spma.agents.supervisor.human_validator.bump_weights_version"
    ) as mock_bump:
        mock_snapshot.return_value = 1
        mock_bump.return_value = AsyncMock(return_value=2)
        success = await hv.approve(ticket_id, approver="alice")

    assert success is True
    assert hv._pending[ticket_id]["status"] == "approved"
    assert hv._pending[ticket_id]["approver"] == "alice"
    # 精确断言:write_weights_snapshot 被调用 1 次,参数正确
    mock_snapshot.assert_awaited_once()
    call_kwargs = mock_snapshot.await_args.kwargs
    assert call_kwargs["payload"] == {"weights": {"a": 0.4}}
    assert call_kwargs["source"] == "manual"
    assert call_kwargs["approver"] == "alice"
    # 精确断言:bump_weights_version 被调用 1 次
    mock_bump.assert_awaited_once_with(mock_pool)
    await hv.stop()


@pytest.mark.asyncio
async def test_reject_keeps_old_weights(mock_pool):
    """reject 不写新快照,状态变 rejected。"""
    hv = HumanInTheLoopValidator(mock_pool, timeout_seconds=86400)
    ticket_id = await hv.submit_for_review(
        {"a": {"old": 0.3, "new": 0.4, "delta": 0.1}},
        {"a": {"avg_score": 0.8, "count": 100}},
    )
    success = await hv.reject(ticket_id, approver="bob", reason="not confident")
    assert success is True
    assert hv._pending[ticket_id]["status"] == "rejected"
    await hv.stop()


# === Issue 3: 5 个新边界测试 ===

@pytest.mark.asyncio
async def test_submit_ticket_client_failure_is_logged_only(mock_pool):
    """ticket_client.create() 抛错时,submit_for_review 仍正常返回 ticket_id,_pending 被创建。"""

    class FailingTicketClient:
        async def create(self, title, body, labels):
            raise RuntimeError("GitHub API down")

    hv = HumanInTheLoopValidator(
        mock_pool, ticket_client=FailingTicketClient(), timeout_seconds=86400,
    )
    ticket_id = await hv.submit_for_review(
        {"a": {"old": 0.3, "new": 0.4, "delta": 0.1}},
        {"a": {"avg_score": 0.8, "count": 100}},
    )
    # ticket 创建成功(内存中),ticket_client 失败仅 warning
    assert ticket_id.startswith("qr-review-")
    assert hv._pending[ticket_id]["status"] == "pending"
    await hv.stop()


@pytest.mark.asyncio
async def test_approve_nonexistent_ticket_returns_false(mock_pool):
    """不存在的 ticket_id → approve 返回 False。"""
    hv = HumanInTheLoopValidator(mock_pool, timeout_seconds=86400)
    success = await hv.approve("qr-review-nonexistent", approver="alice")
    assert success is False
    await hv.stop()


@pytest.mark.asyncio
async def test_approve_already_approved_returns_false(mock_pool):
    """已 approved 的 ticket 再次 approve → 返回 False。"""
    hv = HumanInTheLoopValidator(mock_pool, timeout_seconds=86400)
    ticket_id = await hv.submit_for_review(
        {"a": {"old": 0.3, "new": 0.4, "delta": 0.1}},
        {"a": {"avg_score": 0.8, "count": 100}},
    )
    success_1 = await hv.approve(ticket_id, approver="alice")
    success_2 = await hv.approve(ticket_id, approver="bob")
    assert success_1 is True
    assert success_2 is False
    await hv.stop()


@pytest.mark.asyncio
async def test_reject_records_reason_field(mock_pool):
    """reject 正确记录 reason 字段到 _pending。"""
    hv = HumanInTheLoopValidator(mock_pool, timeout_seconds=86400)
    ticket_id = await hv.submit_for_review(
        {"a": {"old": 0.3, "new": 0.4, "delta": 0.1}},
        {"a": {"avg_score": 0.8, "count": 100}},
    )
    await hv.reject(ticket_id, approver="bob", reason="not confident in eval")
    assert hv._pending[ticket_id]["status"] == "rejected"
    assert hv._pending[ticket_id]["approver"] == "bob"
    assert hv._pending[ticket_id]["reason"] == "not confident in eval"
    await hv.stop()


@pytest.mark.asyncio
async def test_expire_review_no_op_for_missing_ticket(mock_pool):
    """_pending 中无该 ticket 时,_expire_review 不抛错。"""
    hv = HumanInTheLoopValidator(mock_pool, timeout_seconds=86400)
    # 直接调用 _expire_review(模拟后台 task 触发)
    await hv._expire_review("qr-review-never-existed")
    # _pending 不应被污染
    assert "qr-review-never-existed" not in hv._pending
    await hv.stop()