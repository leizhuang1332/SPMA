"""基于语义聚类的一致性校验器(主文件 ADR-004,零 LLM)。"""
import logging
import math

logger = logging.getLogger(__name__)


class SemanticConsensusChecker:
    """多路分解结果间找共识子查询。"""

    def __init__(self, embedder, sim_threshold: float = 0.3):
        """sim_threshold: 共识评分下限,低于此值 fallback 到 original(避免低质量候选污染)。

        注:默认 0.3 而非 0.6——加权评分(0.6*orig_sim + 0.4*consensus)后,多数候选
        实际得分都低于 0.6,会导致频繁触发 fallback;0.3 更贴合实际分布。
        """
        self._embedder = embedder
        self._threshold = sim_threshold

    async def pick_best_per_source(
        self, original: str, results: list[list[dict]], sources: list[str],
    ) -> list[dict]:
        """对每个 source,从所有策略结果中挑共识最高的子查询。"""
        # 输入健全性校验(None 输入降级)
        if results is None:
            results = []
        if sources is None:
            sources = []

        final = []
        for source in sources:
            candidates = [
                sq["query"]
                for sub_list in results
                if sub_list is not None  # 防御 None sub_list
                for sq in sub_list
                if sq.get("target") == source
            ]
            if not candidates:
                final.append({"query": original, "target": source})
                continue
            if len(candidates) == 1:
                final.append({"query": candidates[0], "target": source})
                continue
            if not self._embedder:
                final.append({"query": candidates[0], "target": source})
                continue

            try:
                embs = await self._embedder.embed_documents(candidates)
                orig_emb = await self._embedder.embed_query(original)
            except Exception as e:
                # PII 安全: %s + type(e).__name__ + exc_info=True
                logger.warning(
                    "consensus_check: embedder failed: %s",
                    type(e).__name__,
                    exc_info=True,
                )
                final.append({"query": candidates[0], "target": source})
                continue

            # embedder 返回向量数量校验(防御 embedder bug)
            if len(embs) != len(candidates):
                logger.warning(
                    "consensus_check: embedder returned %d vectors for %d candidates, taking first",
                    len(embs), len(candidates),
                )
                final.append({"query": candidates[0], "target": source})
                continue

            scored = []
            for i, cand in enumerate(candidates):
                orig_sim = self._cosine(embs[i], orig_emb)
                other_sims = [
                    self._cosine(embs[i], embs[j])
                    for j in range(len(candidates)) if j != i
                ]
                consensus = sum(other_sims) / len(other_sims) if other_sims else 0.0
                scored.append((cand, orig_sim * 0.6 + consensus * 0.4))

            # 阈值过滤:最高分低于阈值 → fallback 到 original(避免低质量候选污染)
            best_cand, best_score = max(scored, key=lambda x: x[1])
            if best_score < self._threshold:
                final.append({"query": original, "target": source})
            else:
                final.append({"query": best_cand, "target": source})
        return final

    @staticmethod
    def _cosine(a, b) -> float:
        # 维度校验(应用 P3 semantic_voter 修复模式)
        if len(a) != len(b):
            raise ValueError(f"vector dim mismatch: {len(a)} vs {len(b)}")
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a)) + 1e-10
        nb = math.sqrt(sum(x * x for x in b)) + 1e-10
        return dot / (na * nb)