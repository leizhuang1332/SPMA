"""查询扩展多路策略单测。"""
import pytest

from spma.agents.supervisor.expansion_strategies import (
    intent_aware, synonym_based, entity_injection, context_aware,
)


@pytest.mark.asyncio
async def test_intent_aware_adds_search_related_words():
    """query_type=search → 附加'相关文档/涉及'(最多 2 个)。"""
    result = await intent_aware(
        query="订单系统",
        classification={"query_type": "search"},
        entities={},
    )
    assert result is not None
    assert "订单系统" in result
    # 添加了相关词
    assert ("相关文档" in result) or ("涉及" in result)


@pytest.mark.asyncio
async def test_intent_aware_returns_none_for_unsupported_type():
    """不支持的 query_type → 返回 None。"""
    result = await intent_aware(
        query="订单",
        classification={"query_type": "chitchat"},
        entities={},
    )
    assert result is None


@pytest.mark.asyncio
async def test_intent_aware_does_not_duplicate_existing_words():
    """如果原 query 已含相关词,不重复添加。"""
    result = await intent_aware(
        query="订单相关文档",
        classification={"query_type": "search"},
        entities={},
    )
    # '相关文档' 已存在 → 不应重复
    assert result.count("相关文档") == 1


@pytest.mark.asyncio
async def test_synonym_based_expands_with_canonical():
    """命中 user_term → 追加 canonical_term。"""
    result = await synonym_based(
        query="买啥",
        classification={"query_type": "search"},
        entities={},
        synonym_map={"买啥": ["商品列表"]},
    )
    assert "买啥" in result
    assert "商品列表" in result


@pytest.mark.asyncio
async def test_synonym_based_returns_none_without_synonym_map():
    """无 synonym_map → 返回 None。"""
    result = await synonym_based(
        query="买啥",
        classification={},
        entities={},
        synonym_map=None,
    )
    assert result is None


@pytest.mark.asyncio
async def test_entity_injection_appends_entities():
    """把 entity 追加到 query。"""
    result = await entity_injection(
        query="字段信息",
        classification={"query_type": "data_query"},
        entities={"table_names": ["t_user"], "column_names": ["user_id"]},
    )
    assert "字段信息" in result
    assert "t_user" in result
    assert "user_id" in result


@pytest.mark.asyncio
async def test_context_aware_returns_none_without_llm():
    """无 LLM → 返回 None(早退)。"""
    result = await context_aware(
        query="订单",
        classification={"query_type": "search"},
        entities={},
        llm=None,
    )
    assert result is None


@pytest.mark.asyncio
async def test_context_aware_rejects_overlong_output():
    """LLM 输出超长 → 返回 None(防 prompt 注入)。"""
    class FakeLLM:
        async def ainvoke(self, prompt):
            class Resp:
                content = "x" * 10000
            return Resp()

    result = await context_aware(
        query="短问",
        classification={"query_type": "search"},
        entities={},
        llm=FakeLLM(),
    )
    assert result is None


@pytest.mark.asyncio
async def test_synonym_based_returns_none_for_non_dict_synonym_map():
    """synonym_map 是非 dict 类型 → 返回 None + warning。"""
    result = await synonym_based(
        query="买啥",
        classification={},
        entities={},
        synonym_map=["买啥"],  # 故意传 list
    )
    assert result is None


@pytest.mark.asyncio
async def test_entity_injection_filters_none_and_non_string():
    """entity 是 None / 非字符串 → 跳过,不污染 query。"""
    result = await entity_injection(
        query="字段",
        classification={"query_type": "data_query"},
        entities={
            "table_names": ["t_user", None],  # 含 None
            "column_names": [123, "user_id"],  # 含数字
        },
    )
    assert result == "字段 t_user user_id"  # None 和 123 被过滤


@pytest.mark.asyncio
async def test_context_aware_returns_none_on_llm_exception():
    """LLM 抛错 → 返回 None + warning。"""
    class BrokenLLM:
        async def ainvoke(self, prompt):
            raise RuntimeError("LLM client timeout")

    result = await context_aware(
        query="订单",
        classification={"query_type": "search"},
        entities={},
        llm=BrokenLLM(),
    )
    assert result is None
