"""SemanticVoter 单测。"""
import pytest

from spma.agents.supervisor.semantic_voter import SemanticVoter


class FakeEmbedder:
    """测试用:返回一个稳定可预测的向量(基于字符串 hash)。"""
    async def embed_query(self, text):
        return [hash(text) % 100 / 100.0, 0.5, 0.0]

    async def embed_documents(self, texts):
        return [await self.embed_query(t) for t in texts]


class DeterministicEmbedder:
    """可控向量 embedder,用于精确测试共识度权重行为。

    接受一个 dict: text -> vector。query/original 用 embed_query 取 text 对应的向量。
    """
    def __init__(self, vectors: dict):
        self._vectors = vectors

    async def embed_query(self, text):
        return self._vectors[text]

    async def embed_documents(self, texts):
        return [self._vectors[t] for t in texts]


@pytest.mark.asyncio
async def test_vote_returns_first_when_only_one_candidate():
    voter = SemanticVoter(FakeEmbedder(), alpha=0.4)
    result = await voter.vote_best("original", ["only-candidate"])
    assert result == "only-candidate"


@pytest.mark.asyncio
async def test_vote_returns_original_when_empty():
    voter = SemanticVoter(FakeEmbedder(), alpha=0.4)
    result = await voter.vote_best("original", [])
    assert result == "original"


@pytest.mark.asyncio
async def test_vote_without_embedder_returns_first():
    """无 embedder 时退化为"取第一个"。"""
    voter = SemanticVoter(None, alpha=0.4)
    result = await voter.vote_best("original", ["a", "b"])
    assert result == "a"


@pytest.mark.asyncio
async def test_vote_uses_consensus_not_just_similarity():
    """Smoke test:共识度起作用(具体谁赢取决于 hash,但不应该是 outlier)。

    详细确定性行为由 test_vote_consensus_prefers_aligned_candidate_deterministic
    和 test_vote_orig_similarity_prefers_when_alpha_high_deterministic 覆盖。
    """
    voter = SemanticVoter(FakeEmbedder(), alpha=0.4)
    candidates = [
        "alpha-rewrite-by-rule",
        "alpha-rewrite-by-llm",
        "different-completely",
    ]
    result = await voter.vote_best("original", candidates)
    assert result in candidates


@pytest.mark.asyncio
async def test_vote_handles_embedder_failure():
    """embedder 抛异常时退化。"""
    class BrokenEmbedder:
        async def embed_query(self, text): raise RuntimeError("boom")
        async def embed_documents(self, texts): raise RuntimeError("boom")

    voter = SemanticVoter(BrokenEmbedder(), alpha=0.4)
    result = await voter.vote_best("original", ["a", "b"])
    assert result == "a"  # 退化到第一个


# --- alpha 范围校验 ---

@pytest.mark.asyncio
async def test_vote_rejects_invalid_alpha():
    """alpha 越界应抛 ValueError(防权重反转 bug)。"""
    emb = FakeEmbedder()
    with pytest.raises(ValueError, match="alpha must be in"):
        SemanticVoter(emb, alpha=-0.1)
    with pytest.raises(ValueError, match="alpha must be in"):
        SemanticVoter(emb, alpha=1.5)
    # 边界值 0.0 / 1.0 应允许
    SemanticVoter(emb, alpha=0.0)  # 不抛错
    SemanticVoter(emb, alpha=1.0)  # 不抛错


# --- 确定性行为测试 ---

@pytest.mark.asyncio
async def test_vote_consensus_prefers_aligned_candidate_deterministic():
    """确定性测试:共识度最高的 candidate 胜出(即使 orig_sim 较低)。

    构造场景:
      original = "orig"
      A = "A"    orig_sim(A) = 0.5  consensus(A, B) = 0.9 (与 B 一致)
      B = "B"    orig_sim(B) = 0.5  consensus(B, A) = 0.9 (与 A 一致)
      C = "C"    orig_sim(C) = 0.9  consensus(C, X) = 0.3 (与 A/B 都不同)

    用 2D 向量便于手算 cosine:
      orig = (1, 0)
      A    = (0.5, 0.5)    orig_sim = cos((1,0),(0.5,0.5)) = 0.5/(1*0.707) = 0.707
      B    = (0.4, 0.6)    orig_sim = cos((1,0),(0.4,0.6)) = 0.4/0.721 = 0.555
      C    = (0.9, 0.1)    orig_sim = cos((1,0),(0.9,0.1)) = 0.9/0.906 = 0.994

    pairwise:
      cos(A, B) = (0.5*0.4 + 0.5*0.6) / (0.707 * 0.721) = 0.5 / 0.510 = 0.981
      cos(A, C) = (0.5*0.9 + 0.5*0.1) / (0.707 * 0.906) = 0.5 / 0.641 = 0.780
      cos(B, C) = (0.4*0.9 + 0.6*0.1) / (0.721 * 0.906) = 0.42 / 0.653 = 0.643

    score(alpha=0.4):
      A = 0.4 * 0.707 + 0.6 * ((0.981 + 0.780)/2) = 0.283 + 0.528 = 0.811
      B = 0.4 * 0.555 + 0.6 * ((0.981 + 0.643)/2) = 0.222 + 0.487 = 0.709
      C = 0.4 * 0.994 + 0.6 * ((0.780 + 0.643)/2) = 0.398 + 0.427 = 0.825

    在 alpha=0.4 下 C 略胜(0.825 vs 0.811),因为 orig_sim 权重虽然小但 C 极高。
    改为 alpha=0.1:
      A = 0.1 * 0.707 + 0.9 * 0.881 = 0.071 + 0.793 = 0.864
      B = 0.1 * 0.555 + 0.9 * 0.812 = 0.056 + 0.731 = 0.787
      C = 0.1 * 0.994 + 0.9 * 0.712 = 0.099 + 0.641 = 0.740
    alpha=0.1 时 A 胜(共识度主导)。

    本测试用 alpha=0.1 验证共识度优先行为:A 胜出(虽 orig_sim 不是最高,但与 B 共识最强)。
    """
    emb = DeterministicEmbedder({
        "orig": [1.0, 0.0],
        "A":    [0.5, 0.5],
        "B":    [0.4, 0.6],
        "C":    [0.9, 0.1],
    })
    voter = SemanticVoter(emb, alpha=0.1)
    result = await voter.vote_best("orig", ["A", "B", "C"])
    assert result == "A", f"consensus 主导下 A 应胜出(alpha=0.1), got {result!r}"


@pytest.mark.asyncio
async def test_vote_orig_similarity_prefers_when_alpha_high_deterministic():
    """确定性测试:alpha 高时 orig_sim 主导。

    与上例同向量,alpha=0.9:
      A = 0.9 * 0.707 + 0.1 * 0.881 = 0.636 + 0.088 = 0.724
      B = 0.9 * 0.555 + 0.1 * 0.812 = 0.500 + 0.081 = 0.581
      C = 0.9 * 0.994 + 0.1 * 0.712 = 0.895 + 0.071 = 0.966
    C 胜出(orig_sim 主导)。
    """
    emb = DeterministicEmbedder({
        "orig": [1.0, 0.0],
        "A":    [0.5, 0.5],
        "B":    [0.4, 0.6],
        "C":    [0.9, 0.1],
    })
    voter = SemanticVoter(emb, alpha=0.9)
    result = await voter.vote_best("orig", ["A", "B", "C"])
    assert result == "C", f"orig_sim 主导下 C 应胜出(alpha=0.9), got {result!r}"


@pytest.mark.asyncio
async def test_vote_dimension_mismatch_falls_back():
    """embedder 返回不同维度向量 → voter 应退化(不抛错)。"""
    emb = DeterministicEmbedder({
        "orig": [1.0, 0.0],       # 2D
        "A":    [0.5, 0.5],       # 2D
        "B":    [0.4, 0.6, 0.1],  # 3D — 故意不一致
    })
    voter = SemanticVoter(emb, alpha=0.4)
    # embed_query 返回 orig(2D),embed_documents 返回 A(2D) + B(3D)
    # 计算 cos(orig, A) OK,cos(A, B) 抛 ValueError → 整体退化
    result = await voter.vote_best("orig", ["A", "B"])
    assert result == "A"  # 退化到第一