import pytest
from unittest.mock import AsyncMock, MagicMock
from spma.agents.supervisor.query_rewriter import _evaluate_quality, _normalize_with_synonyms, _resolve_references, _expand_query


class TestResolveReferences:
    """指代消解测试"""

    @pytest.mark.asyncio
    async def test_resolve_references_no_history(self):
        """无对话历史时直接返回原查询"""
        result = await _resolve_references("用户登录", "", None)
        assert result == "用户登录"

    @pytest.mark.asyncio
    async def test_resolve_references_no_reference_words(self):
        """查询中无指代性词汇时直接返回"""
        history = "之前我们讨论了用户登录问题"
        result = await _resolve_references("查询相关代码", history, None)
        assert result == "查询相关代码"

    @pytest.mark.asyncio
    async def test_resolve_references_with_llm(self):
        """有指代词汇且有 LLM 时调用消解"""
        llm = AsyncMock()
        llm.ainvoke.return_value = MagicMock(content="用户登录 authentication login 涉及哪些需求和代码")
        history = "用户登录涉及哪些需求和代码"
        result = await _resolve_references("这个问题", history, llm)
        # Verify the reference "这个问题" was replaced (result should be different from input)
        assert result == "用户登录 authentication login 涉及哪些需求和代码"
        assert "这个问题" not in result


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
        # Verify both "user" and "login" appear, and no "用户"/"登录" remain
        assert "user" in result
        assert "login" in result
        assert "用户" not in result
        assert "登录" not in result

    @pytest.mark.asyncio
    async def test_normalize_with_synonyms_with_entities(self):
        """基于实体的精确映射"""
        synonym_map = {"用户": ["user"]}
        entities = {"req_ids": ["REQ-001", "REQ-002"], "table_names": ["users"]}
        result = await _normalize_with_synonyms("用户查询", synonym_map, entities)
        assert "REQ-001" in result
        assert "REQ-002" in result
        assert "users" in result


class TestExpandQuery:
    """查询扩展测试"""

    @pytest.mark.asyncio
    async def test_expand_query_no_llm(self):
        """无 LLM 时返回原查询"""
        classification = {"query_type": "search"}
        result = await _expand_query("用户登录", classification, {}, None)
        assert result == "用户登录"

    @pytest.mark.asyncio
    async def test_expand_query_unknown_type(self):
        """未知 query_type 时返回原查询"""
        llm = AsyncMock()
        classification = {"query_type": "unknown_type"}
        result = await _expand_query("用户登录", classification, {}, llm)
        assert result == "用户登录"

    @pytest.mark.asyncio
    async def test_expand_query_search_type(self):
        """search 类型扩展"""
        llm = AsyncMock()
        # 第一次调用返回扩展结果，第二次调用返回高质量评分
        llm.ainvoke.side_effect = [
            MagicMock(content="用户登录 authentication login 涉及哪些需求和代码实现"),
            MagicMock(content="0.9")
        ]
        classification = {"query_type": "search"}
        entities = {"req_ids": ["REQ-001"]}
        result = await _expand_query("用户登录", classification, entities, llm)
        assert "用户登录" in result
        # 验证 LLM 被调用（至少一次，因为扩展和质量评估都会调用）
        assert llm.ainvoke.call_count >= 1