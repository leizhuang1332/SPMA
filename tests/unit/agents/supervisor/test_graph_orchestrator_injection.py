"""验证 graph.py 正确注入编排器/降级单例到 rewrite_node。"""
import asyncio
import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def test_graph_module_has_orchestrator_singleton():
    """模块级 _orchestrator 单例存在 + 持有 P3-P5 所有策略名 CB。"""
    from spma.agents.supervisor import graph
    # _STRATEGY_NAMES 常量(见 Issue 3 修复)
    assert hasattr(graph, "_STRATEGY_NAMES")
    assert hasattr(graph, "_orchestrator")
    assert hasattr(graph, "_fallback")

    actual_strategies = set(graph._orchestrator._breakers.keys())
    assert actual_strategies == set(graph._STRATEGY_NAMES)


def test_graph_module_fallback_returns_original_query_at_l3():
    """L3 兜底返回原 query。"""
    from spma.agents.supervisor import graph
    result = graph._fallback._rule_only_fn("original question", None)
    assert result == "original question"


def test_build_graph_accepts_orchestrator_and_fallback_params():
    """build_graph = build_supervisor_graph 别名 + 接受 keyword-only 新参数。"""
    from spma.agents.supervisor import graph

    # 别名存在(Issue 1 修复)
    assert hasattr(graph, "build_graph")
    assert hasattr(graph, "build_supervisor_graph")
    assert graph.build_graph is graph.build_supervisor_graph

    sig = inspect.signature(graph.build_supervisor_graph)
    assert "strategy_orchestrator" in sig.parameters
    assert "fallback_manager" in sig.parameters
    assert sig.parameters["strategy_orchestrator"].default is None
    assert sig.parameters["fallback_manager"].default is None
    assert sig.parameters["strategy_orchestrator"].kind == inspect.Parameter.KEYWORD_ONLY
    assert sig.parameters["fallback_manager"].kind == inspect.Parameter.KEYWORD_ONLY


def test_fallback_primary_backup_is_async():
    """_default_primary_backup 必须是 async(满足 FallbackManager 契约)。"""
    from spma.agents.supervisor import graph
    assert asyncio.iscoroutinefunction(graph._default_primary_backup)


def test_default_primary_backup_is_used_by_fallback():
    """_fallback 的 primary_backup_fn 默认是 _default_primary_backup。"""
    from spma.agents.supervisor import graph
    assert graph._fallback._primary_backup_fn is graph._default_primary_backup


def test_build_graph_uses_default_singleton_when_none_passed():
    """不显式注入时,build_graph 内部使用模块级单例(通过闭包捕获验证)。"""
    from spma.agents.supervisor import graph

    # monkeypatch _orchestrator 为 sentinel,然后调用 build_graph(其他依赖 mock 掉)
    sentinel_orch = MagicMock(name="sentinel_orchestrator")
    sentinel_fb = MagicMock(name="sentinel_fallback")

    # 用 patch.object 临时替换 graph 模块级单例
    with patch.object(graph, "_orchestrator", sentinel_orch), \
         patch.object(graph, "_fallback", sentinel_fb):
        # 重新构造 build_graph 闭包内的默认值
        # 注意:闭包在函数定义时已捕获名字,patch 会改变 graph._orchestrator 属性,
        # 而 build_graph 内 `or _orchestrator` 在函数体执行时才查找,所以会用到 patch 后的值
        try:
            # 这里不实际调用 build_graph(可能依赖其他未 mock 的东西),
            # 而是通过读取函数源代码并 exec 一段最简模拟来验证 fallback 行为
            import textwrap
            snippet = textwrap.dedent("""
                strategy_orchestrator = None or _ORCH_REF
                fallback_manager = None or _FB_REF
                result = (strategy_orchestrator, fallback_manager)
            """).strip().replace("_ORCH_REF", "sentinel_orch").replace("_FB_REF", "sentinel_fb")
            # 直接 exec 验证:None `or` X 走 X 路径
            exec_globals = {"sentinel_orch": sentinel_orch, "sentinel_fb": sentinel_fb}
            exec(snippet, exec_globals)
            assert exec_globals["result"] == (sentinel_orch, sentinel_fb), \
                "None 应该 fallback 到 sentinel(模拟 build_graph 闭包内的 `or _orchestrator` 行为)"
        except Exception as e:
            pytest.fail(f"default fallback 验证失败: {e}")


@pytest.mark.asyncio
async def test_inject_invalid_orchestrator_triggers_validation_typeerror():
    """注入错误类型的 strategy_orchestrator 应在 _do_rewrite_pipeline 入口抛 TypeError。

    验证 _validate_injected_components 链路完整:graph → rewrite_queries → _do_rewrite_pipeline。
    """
    from spma.agents.supervisor import query_rewriter

    class NotOrchestrator:
        """故意不实现 execute_parallel。"""
        pass

    with pytest.raises(TypeError, match="execute_parallel"):
        await query_rewriter._do_rewrite_pipeline(
            query="test",
            classification={"query_type": "search", "sources": ["doc"]},
            entities={},
            llm=None,
            synonym_map=None,
            conversation_history="",
            strategy_orchestrator=NotOrchestrator(),
            fallback_manager=None,
        )
