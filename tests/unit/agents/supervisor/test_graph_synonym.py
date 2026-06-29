"""验证 graph.rewrite_node 不再硬编码 synonym_map=None,并能正确从 DB 加载。"""
import inspect
import pytest


def test_rewrite_node_does_not_hardcode_none():
    """源码不应包含 'synonym_map = None' 硬编码。"""
    from spma.agents.supervisor.graph import build_supervisor_graph
    source = inspect.getsource(build_supervisor_graph)
    # 允许注释中提及 None,但赋值必须是 dict
    lines = [
        l for l in source.splitlines()
        if "synonym_map = None" in l and not l.strip().startswith("#")
    ]
    assert lines == [], f"found hardcoded None: {lines}"


@pytest.mark.asyncio
async def test_rewrite_node_loads_synonym_map_from_db(monkeypatch):
    """_load_synonym_map 应调用 SynonymMap.query() 并组装 user_term -> [canonical_term,...] 映射。"""
    from spma.agents.supervisor import graph as graph_mod

    # Mock SynonymMap.query
    class FakeSynMap:
        def __init__(self, pool): pass
        async def query(self, status, limit):
            return {
                "total": 2,
                "entries": [
                    {"user_term": "买啥", "canonical_term": "商品列表"},
                    {"user_term": "咋付钱", "canonical_term": "支付流程"},
                ],
            }

    monkeypatch.setattr(graph_mod, "SynonymMap", FakeSynMap)
    monkeypatch.setattr(graph_mod, "get_db_pool", lambda: object())

    result = await graph_mod._load_synonym_map()

    assert result == {
        "买啥": ["商品列表"],
        "咋付钱": ["支付流程"],
    }


@pytest.mark.asyncio
async def test_load_synonym_map_returns_empty_on_postgres_error(monkeypatch):
    """DB 异常时降级到空 dict,不吞掉编程错误。"""
    import asyncio

    from spma.agents.supervisor import graph as graph_mod

    class FakeSynMap:
        def __init__(self, pool): pass
        async def query(self, status, limit):
            raise OSError("db down")

    monkeypatch.setattr(graph_mod, "SynonymMap", FakeSynMap)
    monkeypatch.setattr(graph_mod, "get_db_pool", lambda: object())

    assert await graph_mod._load_synonym_map() == {}


@pytest.mark.asyncio
async def test_load_synonym_map_propagates_programming_errors(monkeypatch):
    """非 DB 类异常(KeyError 等)必须正常抛出,以便测试发现真问题。"""
    from spma.agents.supervisor import graph as graph_mod

    class FakeSynMap:
        def __init__(self, pool): pass
        async def query(self, status, limit):
            return {"entries": [{"user_term": "x"}]}  # 缺 canonical_term → KeyError

    monkeypatch.setattr(graph_mod, "SynonymMap", FakeSynMap)
    monkeypatch.setattr(graph_mod, "get_db_pool", lambda: object())

    with pytest.raises(KeyError):
        await graph_mod._load_synonym_map()