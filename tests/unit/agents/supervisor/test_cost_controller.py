"""CostController 单测(主文件 ADR-008)。"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from spma.agents.supervisor.cost_controller import (
    CostController, BudgetExhaustedError, ModelTier,
)


@pytest.fixture
def mock_router():
    router = MagicMock()
    router.call = AsyncMock(return_value="response")
    return router


@pytest.fixture
def mock_tracker():
    tracker = MagicMock()
    tracker.get_month_usage_ratio = AsyncMock(return_value=0.5)
    tracker.record_call = AsyncMock(return_value=None)
    return tracker


@pytest.mark.asyncio
async def test_select_haiku_for_easy_complexity(mock_router, mock_tracker):
    cc = CostController(mock_router, mock_tracker)
    result = await cc.call_llm("test prompt", complexity="easy")
    mock_router.call.assert_called_once()
    args, kwargs = mock_router.call.call_args
    assert args[0] == ModelTier.HAIKU


@pytest.mark.asyncio
async def test_select_sonnet_for_medium_complexity(mock_router, mock_tracker):
    cc = CostController(mock_router, mock_tracker)
    await cc.call_llm("test prompt", complexity="medium")
    args, kwargs = mock_router.call.call_args
    assert args[0] == ModelTier.SONNET


@pytest.mark.asyncio
async def test_select_opus_for_hard_complexity(mock_router, mock_tracker):
    cc = CostController(mock_router, mock_tracker)
    await cc.call_llm("test prompt", complexity="hard")
    args, kwargs = mock_router.call.call_args
    assert args[0] == ModelTier.OPUS


@pytest.mark.asyncio
async def test_budget_exhausted_raises(mock_router, mock_tracker):
    mock_tracker.get_month_usage_ratio = AsyncMock(return_value=0.97)
    cc = CostController(mock_router, mock_tracker, hard_threshold=0.95)
    with pytest.raises(BudgetExhaustedError):
        await cc.call_llm("test prompt", complexity="easy")
    mock_router.call.assert_not_called()


@pytest.mark.asyncio
async def test_soft_threshold_warning_logged(mock_router, mock_tracker, caplog):
    mock_tracker.get_month_usage_ratio = AsyncMock(return_value=0.85)
    cc = CostController(mock_router, mock_tracker, soft_threshold=0.8, hard_threshold=0.95)
    with caplog.at_level("WARNING"):
        await cc.call_llm("test prompt", complexity="easy")
    assert any("budget" in r.message.lower() for r in caplog.records)


@pytest.mark.asyncio
async def test_records_actual_cost(mock_router, mock_tracker):
    cc = CostController(mock_router, mock_tracker)
    await cc.call_llm("prompt", complexity="easy")
    mock_tracker.record_call.assert_called_once()
