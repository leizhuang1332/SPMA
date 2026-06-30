"""指代消解多路策略单测。"""
import pytest

from spma.agents.supervisor.reference_strategies import (
    rule_based, entity_based, llm_semantic,
)


@pytest.mark.asyncio
async def test_rule_based_replaces_known_pattern():
    """含 '这个需求' + 有 req_id → 替换为第一个 req_id。"""
    result = await rule_based(
        query="这个需求的最新版本",
        history="",
        entities={"req_ids": ["REQ-123"]},
    )
    assert result == "REQ-123的最新版本"


@pytest.mark.asyncio
async def test_rule_based_returns_none_when_no_reference():
    """无代词 → 返回 None(早退)。"""
    result = await rule_based(
        query="今天天气如何",
        history="很长很长的历史对话",
        entities={},
    )
    assert result is None


@pytest.mark.asyncio
async def test_rule_based_returns_none_when_no_entity_match():
    """有代词但无匹配 entity → 返回 None(交给其他策略)。"""
    result = await rule_based(
        query="这个表是啥",
        history="",
        entities={},  # 无 table_names
    )
    assert result is None


@pytest.mark.asyncio
async def test_entity_based_replaces_pronouns_in_order():
    """多个代词按顺序替换为不同 entity。"""
    result = await entity_based(
        query="它的字段是啥",
        history="",
        entities={"table_names": ["t_user"], "column_names": ["user_id"]},
    )
    # "它" → t_user(第一个 entity,跨类型合并顺序: req_ids → table_names → column_names → code_refs)
    assert result == "t_user的字段是啥", f"精确匹配失败, got {result!r}"


@pytest.mark.asyncio
async def test_entity_based_replaces_multiple_pronouns_in_order():
    """多个代词同时出现,按 pronoun 列表顺序一对一替换。"""
    result = await entity_based(
        query="它和该的关系",
        history="",
        entities={"table_names": ["t_user", "t_role"]},  # 2 个 entity → 它=t_user, 该=t_role
    )
    # pronoun 列表顺序: ["它", "该", "这", "那", "其"]
    # "它" → t_user(idx 0), "该" → t_role(idx 1)
    assert result == "t_user和t_role的关系", f"精确匹配失败, got {result!r}"


@pytest.mark.asyncio
async def test_entity_based_replaces_across_entity_types():
    """entity 跨类型合并顺序: req_ids → table_names → column_names → code_refs。"""
    result = await entity_based(
        query="它的关联",
        history="",
        entities={
            "req_ids": ["REQ-1"],
            "table_names": ["t_user"],  # idx 1
        },
    )
    # all_entities = ["REQ-1", "t_user"];"它" → REQ-1(idx 0)
    assert result == "REQ-1的关联", f"跨类型合并顺序错误, got {result!r}"


@pytest.mark.asyncio
async def test_entity_based_returns_none_when_no_pronoun():
    """无代词 → 返回 None。"""
    result = await entity_based(
        query="今天天气如何",
        history="",
        entities={"table_names": ["t_user"]},
    )
    assert result is None


@pytest.mark.asyncio
async def test_llm_semantic_returns_none_without_history():
    """无 history → 返回 None(早退)。"""
    result = await llm_semantic(
        query="它的字段是啥",
        history="",
        llm=None,
    )
    assert result is None


@pytest.mark.asyncio
async def test_llm_semantic_returns_none_without_llm():
    """无 LLM → 返回 None(早退,与其他策略协同)。"""
    result = await llm_semantic(
        query="这个需求是啥",
        history="很长历史",
        llm=None,
    )
    assert result is None


@pytest.mark.asyncio
async def test_llm_semantic_rejects_overlong_output():
    """LLM 输出超长 → 返回 None(防 prompt 注入)。"""
    class FakeLLM:
        async def ainvoke(self, prompt):
            class Resp:
                content = "x" * 10000  # 远超 3x + 100
            return Resp()

    result = await llm_semantic(
        query="短问",
        history="很长很长的历史对话用于触发" * 20,
        llm=FakeLLM(),
    )
    assert result is None  # 输出超限被丢弃


@pytest.mark.asyncio
async def test_llm_semantic_respects_minimum_threshold():
    """短 query 的输出阈值至少 200 字符(避免阈值过低误丢合理回答)。"""
    class FakeLLM:
        async def ainvoke(self, prompt):
            class Resp:
                content = "x" * 250  # 短 query '短问' 原阈值 106,但下限 200 → 应丢弃
            return Resp()

    result = await llm_semantic(
        query="短问",  # 3 字符 → 原阈值 106,新阈值 max(200, 109) = 200
        history="对话" * 50,  # 提供 history 绕过无 history 早退
        llm=FakeLLM(),
    )
    assert result is None  # 250 > 200,应被丢弃


@pytest.mark.asyncio
async def test_llm_semantic_accepts_short_query_reasonable_output():
    """短 query 的合理长度回答(<= 200 字符)应被保留。"""
    class FakeLLM:
        async def ainvoke(self, prompt):
            class Resp:
                content = "它的字段是 user_id"  # ~15 字符,远低于 200 下限
            return Resp()

    result = await llm_semantic(
        query="它的字段",
        history="之前聊过用户表",
        llm=FakeLLM(),
    )
    assert result == "它的字段是 user_id"
