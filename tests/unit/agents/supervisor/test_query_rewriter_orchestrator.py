"""验证 _do_rewrite_pipeline 接受 strategy_orchestrator / fallback_manager 参数。"""
import inspect
import pytest

from spma.agents.supervisor import query_rewriter


def test_do_rewrite_pipeline_signature_accepts_orchestrator():
    """新参数必须存在 + 默认 None + 是 keyword-only(防止 positional 误用)。"""
    sig = inspect.signature(query_rewriter._do_rewrite_pipeline)
    # 参数存在
    assert "strategy_orchestrator" in sig.parameters
    assert "fallback_manager" in sig.parameters
    # 默认 None
    assert sig.parameters["strategy_orchestrator"].default is None
    assert sig.parameters["fallback_manager"].default is None
    # keyword-only(P2 设计:防止未来参数膨胀时 positional 顺序歧义)
    assert sig.parameters["strategy_orchestrator"].kind == inspect.Parameter.KEYWORD_ONLY
    assert sig.parameters["fallback_manager"].kind == inspect.Parameter.KEYWORD_ONLY
    # 旧参数保持 POSITIONAL_OR_KEYWORD
    for name in ("query", "classification", "entities", "llm", "synonym_map", "conversation_history"):
        assert sig.parameters[name].kind == inspect.Parameter.POSITIONAL_OR_KEYWORD, \
            f"{name} 应该保持 POSITIONAL_OR_KEYWORD"


@pytest.mark.asyncio
async def test_do_rewrite_pipeline_works_without_orchestrator():
    """不注入编排器时,走原串行路径(向后兼容)。

    is_cross_source 未设置 → 走 else 分支,只写 original/normalized/resolved/expanded/doc。
    若 is_cross_source=True 且 sources 多于 1 个,会按 source 拆分 result[source] 而非写 sub_queries。
    """
    result = await query_rewriter._do_rewrite_pipeline(
        query="测试",
        classification={"query_type": "search", "sources": ["doc"]},
        entities={},
        llm=None,  # 无 LLM,各步直接 return
        synonym_map=None,
        conversation_history="",
    )
    assert "original" in result
    assert "normalized" in result
    assert "resolved" in result
    assert "expanded" in result
    assert "doc" in result, "单 source 走 else 分支应写入 result['doc']"
    # 精确行为契约:else 分支下 result[source] 应等于 expanded 后的 query
    assert result["doc"] == result["expanded"], \
        f"else 分支:result['doc'] 应等于 expanded, got {result['doc']!r} vs {result['expanded']!r}"


@pytest.mark.asyncio
async def test_do_rewrite_pipeline_works_with_orchestrator():
    """P2 占位:注入 mock orchestrator/fallback 应不崩溃且行为与 None 路径一致。

    P3-5 将真正使用编排器替换各阶段。本测试确保:
    1. 注入契约正确(不被 _validate_injected_components 拒绝)
    2. P2 阶段行为与 None 路径完全一致(尚未真正使用,仅占位)
    3. 防止 P3-5 集成时接口签名改变 → 此测试作为契约锚点
    """
    # duck-typed mock:只要有 execute_parallel / execute_with_fallback 方法即可
    class MockOrchestrator:
        async def execute_parallel(self, strategies, *args, **kwargs):
            return []

    class MockFallback:
        async def execute_with_fallback(self, query, strategies, *args, **kwargs):
            return query, "rule_only"

    result = await query_rewriter._do_rewrite_pipeline(
        query="测试",
        classification={"query_type": "search", "sources": ["doc"]},
        entities={},
        llm=None,
        synonym_map=None,
        conversation_history="",
        strategy_orchestrator=MockOrchestrator(),
        fallback_manager=MockFallback(),
    )
    # P2 阶段:行为与不注入时一致(else 分支)
    assert "doc" in result
    assert "expanded" in result


def test_validate_injected_components_rejects_wrong_types():
    """_validate_injected_components 拒绝错误类型 → 防 P3-5 静默失效。"""
    # strategy_orchestrator 没有 execute_parallel 方法
    class NotOrchestrator:
        pass

    # fallback_manager 没有 execute_with_fallback 方法
    class NotFallback:
        pass

    with pytest.raises(TypeError, match="execute_parallel"):
        query_rewriter._validate_injected_components(NotOrchestrator(), None)

    with pytest.raises(TypeError, match="execute_with_fallback"):
        query_rewriter._validate_injected_components(None, NotFallback())

    # None / 正确类型不报错
    query_rewriter._validate_injected_components(None, None)  # 不抛错

    # Mock 正确类型
    class ValidOrchestrator:
        async def execute_parallel(self, *a, **kw): pass
    class ValidFallback:
        async def execute_with_fallback(self, *a, **kw): pass

    query_rewriter._validate_injected_components(ValidOrchestrator(), ValidFallback())
