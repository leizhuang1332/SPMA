import pytest
from unittest.mock import AsyncMock, MagicMock
from spma.agents.supervisor.query_rewriter import _evaluate_quality


class TestEvaluateQuality:
    """质量评估测试"""

    @pytest.mark.asyncio
    async def test_evaluate_quality_high_similarity(self):
        """语义完全一致时应返回 >= 0.9"""
        llm = AsyncMock()
        llm.ainvoke.return_value = MagicMock(content="0.9")
        score = await _evaluate_quality("用户登录", "用户登录 authentication login", llm)
        assert score >= 0.9

    @pytest.mark.asyncio
    async def test_evaluate_quality_low_similarity(self):
        """语义严重偏差时应返回 < 0.5"""
        llm = AsyncMock()
        llm.ainvoke.return_value = MagicMock(content="0.3")
        score = await _evaluate_quality("用户登录", "商品列表查询", llm)
        assert score < 0.5

    @pytest.mark.asyncio
    async def test_evaluate_quality_invalid_response(self):
        """LLM 返回无效值时默认返回 0.5"""
        llm = AsyncMock()
        llm.ainvoke.return_value = MagicMock(content="invalid")
        score = await _evaluate_quality("用户登录", "用户登录功能", llm)
        assert score == 0.5