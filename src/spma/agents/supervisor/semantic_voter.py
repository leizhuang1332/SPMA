"""语义投票器——基于共识度选最优(主文件 ADR-004,零 LLM)。

alpha:与原始的语义保持度权重;1-alpha:候选间共识度权重。
共识度优先于"最相似"——多个独立策略收敛的结果最可靠(避免被单个异常策略带偏)。
"""
import logging
import math

logger = logging.getLogger(__name__)


class SemanticVoter:
    """多候选投票器。"""

    def __init__(self, embedder, alpha: float = 0.4):
        if not 0.0 <= alpha <= 1.0:
            raise ValueError(f"alpha must be in [0, 1], got {alpha}")
        self._embedder = embedder
        self._alpha = alpha

    async def vote_best(self, original: str, candidates: list[str]) -> str:
        if not candidates:
            return original
        if len(candidates) == 1:
            return candidates[0]
        if not self._embedder:
            return candidates[0]  # 退化

        try:
            embeddings = await self._embedder.embed_documents(candidates)
            original_emb = await self._embedder.embed_query(original)
            best, best_score = candidates[0], -1.0
            for i, cand in enumerate(candidates):
                orig_sim = self._cosine(embeddings[i], original_emb)
                other_sims = [
                    self._cosine(embeddings[i], embeddings[j])
                    for j in range(len(candidates)) if j != i
                ]
                consensus = sum(other_sims) / len(other_sims) if other_sims else 0.0
                score = self._alpha * orig_sim + (1 - self._alpha) * consensus
                if score > best_score:
                    best, best_score = cand, score
        except Exception as e:
            logger.warning(
                "SemanticVoter: embedder failed (%s: %s), falling back to first",
                type(e).__name__,
                str(e)[:200],  # 截断防 PII/堆栈/URL 泄露
            )
            return candidates[0]
        return best

    @staticmethod
    def _cosine(a, b) -> float:
        if len(a) != len(b):
            raise ValueError(f"vector dim mismatch: {len(a)} vs {len(b)}")
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a)) + 1e-10
        nb = math.sqrt(sum(x * x for x in b)) + 1e-10
        return dot / (na * nb)
