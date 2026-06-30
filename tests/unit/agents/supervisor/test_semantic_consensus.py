"""SemanticConsensusChecker 单测。"""
import pytest

from spma.agents.supervisor.semantic_consensus import SemanticConsensusChecker


class FakeEmbedder:
    async def embed_query(self, text): return [0.5, 0.5, 0.5]
    async def embed_documents(self, texts): return [[0.5, 0.5, 0.5] for _ in texts]


class DeterministicEmbedder:
    """可控向量 embedder,用于精确测试 consensus 评分选择行为。"""

    def __init__(self, vectors: dict[str, list[float]]):
        self._vectors = vectors

    async def embed_query(self, text):
        return self._vectors.get(text, [0.5, 0.5, 0.5])

    async def embed_documents(self, texts):
        return [self._vectors.get(t, [0.5, 0.5, 0.5]) for t in texts]


@pytest.mark.asyncio
async def test_consensus_no_candidate_falls_back_to_original():
    """某 source 无候选 → 用原 query。"""
    checker = SemanticConsensusChecker(FakeEmbedder())
    results = await checker.pick_best_per_source(
        original="订单",
        results=[[{"query": "订单表", "target": "database"}]],  # 只 database 有
        sources=["database", "code"],
    )
    code_query = next(r for r in results if r["target"] == "code")
    assert code_query["query"] == "订单"  # fallback


@pytest.mark.asyncio
async def test_consensus_single_candidate_kept():
    """单候选 → 直接用。"""
    checker = SemanticConsensusChecker(FakeEmbedder())
    results = await checker.pick_best_per_source(
        original="订单",
        results=[[{"query": "订单表", "target": "database"}]],
        sources=["database"],
    )
    assert results[0]["query"] == "订单表"


@pytest.mark.asyncio
async def test_consensus_without_embedder_takes_first():
    """无 embedder → 取第一个候选。"""
    checker = SemanticConsensusChecker(None)
    results = await checker.pick_best_per_source(
        original="订单",
        results=[[
            {"query": "candidate-A", "target": "database"},
            {"query": "candidate-B", "target": "database"},
        ]],
        sources=["database"],
    )
    assert results[0]["query"] == "candidate-A"


@pytest.mark.asyncio
async def test_consensus_handles_embedder_failure():
    """embedder 抛错 → 退化为取第一个。"""
    class BrokenEmbedder:
        async def embed_query(self, text): raise RuntimeError("boom")
        async def embed_documents(self, texts): raise RuntimeError("boom")

    checker = SemanticConsensusChecker(BrokenEmbedder())
    results = await checker.pick_best_per_source(
        original="订单",
        results=[[
            {"query": "A", "target": "database"},
            {"query": "B", "target": "database"},
        ]],
        sources=["database"],
    )
    assert results[0]["query"] == "A"


@pytest.mark.asyncio
async def test_consensus_picks_highest_score_with_deterministic_vectors():
    """确定向量测试:consensus 评分选最优。

    构造场景:
    - original = "x"  → embed = [1, 0, 0]
    - A = "AAA"       → embed = [1, 0, 0]  (与 original 完全相同)
    - B = "BBB"       → embed = [0, 1, 0]  (与 original 正交)
    - C = "CCC"       → embed = [0, 0, 1]  (与 original 正交)

    pairwise cosine:
    cos(A, B) = 0, cos(A, C) = 0, cos(B, C) = 0

    评分(0.6*orig_sim + 0.4*consensus):
    A: orig_sim = 1.0, consensus(avg of B, C) = 0.0 → 0.6
    B: orig_sim = 0.0, consensus(avg of A, C) = 0.0 → 0.0
    C: orig_sim = 0.0, consensus(avg of A, B) = 0.0 → 0.0

    A 胜出(与 original 完全相同,score 0.6 > threshold 0.3)。
    """
    emb = DeterministicEmbedder({
        "x":   [1.0, 0.0, 0.0],
        "AAA": [1.0, 0.0, 0.0],
        "BBB": [0.0, 1.0, 0.0],
        "CCC": [0.0, 0.0, 1.0],
    })
    checker = SemanticConsensusChecker(emb)
    results = await checker.pick_best_per_source(
        original="x",
        results=[[
            {"query": "AAA", "target": "database"},
            {"query": "BBB", "target": "database"},
            {"query": "CCC", "target": "database"},
        ]],
        sources=["database"],
    )
    assert results[0]["query"] == "AAA"


@pytest.mark.asyncio
async def test_consensus_results_none_returns_per_source_originals():
    """results=None → 返回每个 source 的 original(降级)。"""
    checker = SemanticConsensusChecker(FakeEmbedder())
    results = await checker.pick_best_per_source(
        original="订单",
        results=None,  # type: ignore[arg-type]
        sources=["database", "code"],
    )
    assert len(results) == 2
    assert all(r["query"] == "订单" for r in results)


@pytest.mark.asyncio
async def test_consensus_sources_none_returns_empty():
    """sources=None → 返回空列表(无 source 可分配)。"""
    checker = SemanticConsensusChecker(FakeEmbedder())
    results = await checker.pick_best_per_source(
        original="订单",
        results=[[{"query": "A", "target": "database"}]],
        sources=None,  # type: ignore[arg-type]
    )
    assert results == []


@pytest.mark.asyncio
async def test_consensus_return_length_matches_sources():
    """返回列表长度 == len(sources)(不变量)。"""
    checker = SemanticConsensusChecker(FakeEmbedder())
    results = await checker.pick_best_per_source(
        original="x",
        results=[[{"query": "A", "target": "database"}]],
        sources=["database", "code", "doc"],
    )
    assert len(results) == 3


@pytest.mark.asyncio
async def test_consensus_handles_none_sublist():
    """results 列表中含 None 元素 → 跳过 None,正常处理非 None 子列表。"""
    checker = SemanticConsensusChecker(FakeEmbedder())
    results = await checker.pick_best_per_source(
        original="x",
        results=[None, [{"query": "A", "target": "database"}], None],  # type: ignore[list-item]
        sources=["database"],
    )
    assert len(results) == 1
    assert results[0]["query"] == "A"