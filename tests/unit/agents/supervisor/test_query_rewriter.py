import pytest
from unittest.mock import AsyncMock, MagicMock
from spma.agents.supervisor.query_rewriter import _evaluate_quality, _normalize_with_synonyms


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


class TestNormalizeWithSynonyms:
    """同义词标准化测试"""

    @pytest.mark.asyncio
    async def test_normalize_with_synonyms_empty_map(self):
        """synonym_map 为空时直接返回原查询"""
        result = await _normalize_with_synonyms("用户登录查询", None, {})
        assert result == "用户登录查询"

    @pytest.mark.asyncio
    async def test_normalize_with_synonyms_basic(self):
        """基本同义词替换"""
        synonym_map = {"用户": ["user", "账号"], "登录": ["login", "authentication"]}
        entities = {}
        result = await _normalize_with_synonyms("用户登录", synonym_map, entities)
        assert "user" in result
        assert "login" in result

    @pytest.mark.asyncio
    async def test_normalize_with_synonyms_with_entities(self):
        """基于实体的精确映射"""
        synonym_map = {"用户": ["user"]}
        entities = {"req_ids": ["REQ-001", "REQ-002"], "table_names": ["users"]}
        result = await _normalize_with_synonyms("用户查询", synonym_map, entities)
        assert "REQ-001" in result
        assert "REQ-002" in result
        assert "users" in result