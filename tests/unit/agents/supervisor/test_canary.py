"""CanaryRelease 单测(主文件 ADR-010)。"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from spma.agents.supervisor.canary import CanaryRelease, CANARY_STAGES


@pytest.fixture
def canary_ff():
    ff = MagicMock()
    ff.set_rollout = AsyncMock(return_value=None)
    return ff


@pytest.fixture
def canary_audit():
    audit = MagicMock()
    audit.log = AsyncMock(return_value=None)
    return audit


@pytest.mark.asyncio
async def test_advance_shadow_sets_rollout_0(canary_ff, canary_audit):
    cr = CanaryRelease(canary_ff, canary_audit)
    await cr.advance("new_strategy", "shadow", operator="alice")
    canary_ff.set_rollout.assert_called_with("new_strategy", rollout_pct=0, enabled=True)


@pytest.mark.asyncio
async def test_advance_one_percent_sets_rollout_1(canary_ff, canary_audit):
    cr = CanaryRelease(canary_ff, canary_audit)
    await cr.advance("new_strategy", "one_percent", operator="alice")
    canary_ff.set_rollout.assert_called_with("new_strategy", rollout_pct=1, enabled=True)


@pytest.mark.asyncio
async def test_advance_ten_percent(canary_ff, canary_audit):
    cr = CanaryRelease(canary_ff, canary_audit)
    await cr.advance("s", "ten_percent", operator="alice")
    canary_ff.set_rollout.assert_called_with("s", rollout_pct=10, enabled=True)


@pytest.mark.asyncio
async def test_advance_unknown_stage_raises(canary_ff, canary_audit):
    cr = CanaryRelease(canary_ff, canary_audit)
    with pytest.raises(ValueError, match="Unknown stage"):
        await cr.advance("s", "nonexistent", operator="alice")


@pytest.mark.asyncio
async def test_halt_disables_strategy(canary_ff, canary_audit):
    cr = CanaryRelease(canary_ff, canary_audit)
    await cr.halt("new_strategy", operator="bob", reason="quality regression")
    canary_ff.set_rollout.assert_called_with("new_strategy", rollout_pct=0, enabled=False)
    # 审计调用
    assert canary_audit.log.called
