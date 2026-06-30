"""查询分解多路策略单测。"""
import pytest

from spma.agents.supervisor.decomposition_strategies import (
    template_based, entity_guided, llm_based,
)


@pytest.mark.asyncio
async def test_template_based_splits_on_explicit_and_with_precise_query():
    """收紧:精确断言生成的 query 字符串。"""
    result = await template_based(
        query="涉及哪些需求和表",
        entities={},
        sources=["database", "requirements"],
    )
    assert result is not None
    assert len(result) == 2
    targets_to_queries = {r["target"]: r["query"] for r in result}
    assert targets_to_queries["database"] == "涉及哪些需求,面向database的表"
    assert targets_to_queries["requirements"] == "涉及哪些需求,面向requirements的表"


@pytest.mark.asyncio
async def test_template_based_returns_none_for_simple_query():
    """简单 query → 返回 None(交给其他策略)。"""
    result = await template_based(
        query="今天天气",
        entities={},
        sources=["doc"],
    )
    assert result is None


@pytest.mark.asyncio
async def test_template_based_broadcasts_when_multiple_entity_types():
    """多种 entity 类型存在 → 自动广播到所有 source。"""
    result = await template_based(
        query="综合查询",
        entities={"table_names": ["t_user"], "code_refs": ["auth.py"]},
        sources=["database", "codebase"],
    )
    assert result is not None
    assert len(result) == 2


@pytest.mark.asyncio
async def test_entity_guided_returns_none_when_all_sources_have_same_entities():
    """所有 source 的实体相同 → 返回 None(避免 N 个相同子查询,主文件 §3.4 ADR)。"""
    result = await entity_guided(
        query="综合",
        entities={"table_names": ["t_user"]},
        sources=["database", "doc"],
    )
    # doc 没有对应实体类型,unique_entity_sets 只有一个 → None
    assert result is None


@pytest.mark.asyncio
async def test_entity_guided_differentiates_when_sources_have_different_entities():
    """不同 source 实体不同 → 按 source 差异化生成。"""
    result = await entity_guided(
        query="查询",
        entities={"table_names": ["t_user"], "code_refs": ["auth.py"]},
        sources=["database", "codebase"],
    )
    assert result is not None
    db_query = next(r for r in result if r["target"] == "database")
    code_query = next(r for r in result if r["target"] == "codebase")
    assert "t_user" in db_query["query"]
    assert "auth.py" in code_query["query"]


@pytest.mark.asyncio
async def test_entity_guided_differentiates_with_precise_queries():
    """收紧:精确断言差异化 query 拼接。"""
    result = await entity_guided(
        query="查询",
        entities={"table_names": ["t_user"], "code_refs": ["auth.py"]},
        sources=["database", "codebase"],
    )
    assert result is not None
    db_query = next(r for r in result if r["target"] == "database")
    code_query = next(r for r in result if r["target"] == "codebase")
    assert db_query["query"] == "查询 t_user"
    assert code_query["query"] == "查询 auth.py"


@pytest.mark.asyncio
async def test_template_based_returns_none_when_sources_empty():
    """sources=[] → 返回 None(早退)。"""
    result = await template_based(query="涉及哪些需求和表", entities={}, sources=[])
    assert result is None


@pytest.mark.asyncio
async def test_entity_guided_returns_none_when_sources_empty():
    """sources=[] → 返回 None(早退)。"""
    result = await entity_guided(query="查询", entities={}, sources=[])
    assert result is None


@pytest.mark.asyncio
async def test_template_based_handles_none_entities():
    """entities=None → 不抛错,降级为 broadcast 行为。"""
    result = await template_based(
        query="综合查询",
        entities=None,  # type: ignore[arg-type]
        sources=["database"],
    )
    # entities=None → entities={} → entity_types_found = 0 → 返回 None
    assert result is None


@pytest.mark.asyncio
async def test_entity_guided_handles_none_entities():
    """entities=None → 不抛错,早退。"""
    result = await entity_guided(
        query="查询",
        entities=None,  # type: ignore[arg-type]
        sources=["database"],
    )
    assert result is None


@pytest.mark.asyncio
async def test_llm_based_handles_none_content():
    """LLM 返回 None content → 不抛错,fallback broadcast。"""
    class FakeLLMNoneContent:
        async def ainvoke(self, prompt):
            class Resp:
                content = None
            return Resp()

    result = await llm_based(
        query="查询",
        entities={},
        sources=["doc"],
        llm=FakeLLMNoneContent(),
    )
    assert result is not None
    assert result[0]["query"] == "查询"


@pytest.mark.asyncio
async def test_llm_based_overlong_at_5001_boundary():
    """5001 字符触发 fallback(> 5000 边界)。"""
    class FakeLLMLong:
        async def ainvoke(self, prompt):
            class Resp:
                content = "x" * 5001
            return Resp()

    result = await llm_based(query="短", entities={}, sources=["doc"], llm=FakeLLMLong())
    # 5001 > 5000 → fallback broadcast
    assert result is not None
    assert result[0]["query"] == "短"


@pytest.mark.asyncio
async def test_llm_based_underlong_at_5000_boundary():
    """5000 字符不触发 fallback(刚好 = 5000)。"""
    class FakeLLM5000:
        async def ainvoke(self, prompt):
            class Resp:
                content = "x" * 5000
            return Resp()

    result = await llm_based(query="短", entities={}, sources=["doc"], llm=FakeLLM5000())
    # 5000 NOT > 5000 → 走 4 步 JSON 兜底(失败)→ broadcast
    assert result is not None
    assert result[0]["query"] == "短"  # 4 步兜底返回 broadcast


@pytest.mark.asyncio
async def test_llm_based_falls_back_to_broadcast_without_llm():
    """无 LLM → 返回原 query 广播(沿用已有 fallback)。"""
    result = await llm_based(
        query="查询",
        entities={},
        sources=["doc", "code"],
        llm=None,
    )
    assert result is not None
    assert len(result) == 2
    assert all(r["query"] == "查询" for r in result)


@pytest.mark.asyncio
async def test_llm_based_rejects_overlong_output():
    """LLM 输出超 5000 字符 → 返回 None(走 fallback → broadcast)。"""
    class FakeLLM:
        async def ainvoke(self, prompt):
            class Resp:
                content = "x" * 10000
            return Resp()

    result = await llm_based(
        query="短问",
        entities={},
        sources=["doc"],
        llm=FakeLLM(),
    )
    # 输出超限被丢弃,走 fallback → broadcast
    assert result is not None
    assert result[0]["query"] == "短问"
