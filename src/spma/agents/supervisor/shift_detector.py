"""分布漂移检测——MMD + 采样相似度分布(主文件 ADR-005)。

替代 v3.0 错误的"embedding 均值差"。
"""
import asyncio
import logging
import random

import numpy as np

logger = logging.getLogger(__name__)


class DistributionShiftDetector:
    """MMD 漂移检测。"""

    def __init__(self, embedder, mmd_threshold: float = 0.05, sample_size: int = 200):
        self._embedder = embedder
        self._mmd_threshold = mmd_threshold
        self._sample_size = sample_size
        self._baseline: np.ndarray | None = None

    async def fit_baseline(self, queries: list[str]):
        if not queries:
            logger.warning("shift_detector.fit_baseline: empty queries, baseline not set")
            return
        if len(queries) > self._sample_size:
            queries = random.sample(queries, self._sample_size)
        try:
            embs = await self._embedder.embed_documents(queries)
        except Exception as e:
            logger.warning(
                "shift_detector.fit_baseline: embedder failed: %s",
                type(e).__name__,
                exc_info=True,
            )
            return
        self._baseline = np.array(embs)
        logger.info("DistributionShiftDetector: baseline fitted (n=%d)", len(embs))

    async def detect_shift(self, queries: list[str]) -> dict:
        if self._baseline is None:
            return {"is_shifted": False, "reason": "no_baseline"}
        if len(self._baseline) < 2:
            return {"is_shifted": False, "reason": "insufficient_baseline"}
        if not queries:
            return {"is_shifted": False, "reason": "no_queries"}

        if len(queries) > self._sample_size:
            queries = random.sample(queries, self._sample_size)
        try:
            current = np.array(await self._embedder.embed_documents(queries))
        except Exception as e:
            logger.warning(
                "shift_detector.detect_shift: embedder failed: %s",
                type(e).__name__,
                exc_info=True,
            )
            return {"is_shifted": False, "reason": "embedder_failed"}
        if len(current) < 2:
            return {"is_shifted": False, "reason": "insufficient_current"}

        try:
            mmd = self._compute_mmd(self._baseline, current)
            baseline_sim = self._pairwise_sim(self._baseline)
            current_sim = self._pairwise_sim(current)
            sim_shift = abs(float(np.mean(baseline_sim) - np.mean(current_sim)))
        except Exception as e:
            logger.warning(
                "shift_detector.detect_shift: compute failed: %s",
                type(e).__name__,
                exc_info=True,
            )
            return {"is_shifted": False, "reason": "embedder_failed"}

        is_shifted = mmd > self._mmd_threshold or sim_shift > 0.1
        return {
            "is_shifted": is_shifted,
            "mmd_score": float(mmd),
            "sim_dist_shift": sim_shift,
            "recommendation": "re_evaluate_weights" if is_shifted else "keep_current",
        }

    def _compute_mmd(self, X, Y, sigma: float = 1.0) -> float:
        if len(X) < 2 or len(Y) < 2:
            logger.warning(
                "_compute_mmd: insufficient samples X=%d Y=%d",
                len(X), len(Y),
            )
            return 0.0
        def gaussian(A, B):
            d = np.sum(A ** 2, axis=1)[:, None] + np.sum(B ** 2, axis=1)[None, :] - 2 * A @ B.T
            return np.exp(-d / (2 * sigma ** 2))
        Kxx = gaussian(X, X)
        Kyy = gaussian(Y, Y)
        Kxy = gaussian(X, Y)
        np.fill_diagonal(Kxx, 0)
        np.fill_diagonal(Kyy, 0)
        n, m = len(X), len(Y)
        return float(
            Kxx.sum() / (n * (n - 1)) + Kyy.sum() / (m * (m - 1)) - 2 * Kxy.mean()
        )

    def _pairwise_sim(self, embs: np.ndarray) -> np.ndarray:
        n = min(len(embs), 100)
        idx = np.random.choice(len(embs), n, replace=False)
        sampled = embs[idx]
        normed = sampled / (np.linalg.norm(sampled, axis=1, keepdims=True) + 1e-10)
        sims = normed @ normed.T
        return sims[np.triu_indices(n, k=1)]