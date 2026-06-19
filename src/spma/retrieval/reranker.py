"""重排序——RRF 等权/加权融合 + BGE-Reranker v2 M3 Cross-encoder。

Phase 1-2: RRF 等权融合（k=60）
Phase 2: 按 query_type 分层权重 (precise/semantic/hybrid)
Phase 3: BGE-Reranker v2 M3 对 RRF Top-20 精排

设计依据: SPMA-design-02 §1.5 混合检索权重确定
"""

import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from typing import List

logger = logging.getLogger(__name__)

# 模块级占位：让 unittest.mock.patch 能够定位到这些属性。
CrossEncoder = None  # type: ignore[assignment]
snapshot_download = None  # type: ignore[assignment]

MODEL_ID = "BAAI/bge-reranker-v2-m3"


class BGEReranker:
    """BGE Cross-Encoder 精排器。

    - 下载源: ModelScope (modelscope.cn)
    - 推理引擎: sentence-transformers CrossEncoder
    - 后端: CPU（开发用），GPU 可通过 device 参数切换
    """

    MODEL_ID = MODEL_ID

    def __init__(self, device: str = "cpu"):
        """同步初始化：下载（如需要）并加载 CrossEncoder 模型。

        Raises:
            Exception: 模型下载或加载失败时向上传播，由调用方处理降级。
        """
        self._pool = ThreadPoolExecutor(max_workers=1)
        self._model = self._load_model(device)

    def shutdown(self):
        """关闭线程池，释放资源。"""
        self._pool.shutdown(wait=True)

    def _load_model(self, device: str):
        """同步加载：缓存检查 → ModelScope 下载 → CrossEncoder 初始化。"""
        os.environ.setdefault(
            "SENTENCE_TRANSFORMERS_HOME",
            os.path.expanduser("~/.cache/modelscope/hub"),
        )

        cache_root = os.path.expanduser("~/.cache/modelscope/hub")
        model_dir = os.path.join(cache_root, self.MODEL_ID)

        if CrossEncoder is None:
            from sentence_transformers import CrossEncoder as _CE
            globals()["CrossEncoder"] = _CE

        if os.path.isdir(model_dir) and os.listdir(model_dir):
            logger.info("BGE Reranker 已在本地缓存，跳过下载: %s", model_dir)
            return CrossEncoder(model_dir, device=device)

        logger.info("本地缓存未命中，从 ModelScope 下载 BGE Reranker...")

        if snapshot_download is None:
            from modelscope import snapshot_download as _SD
            globals()["snapshot_download"] = _SD

        logging.getLogger("modelscope").setLevel(logging.WARNING)

        local_path = snapshot_download(self.MODEL_ID, cache_dir=cache_root)
        logger.info("BGE Reranker 下载完成: %s", local_path)
        return CrossEncoder(local_path, device=device)

    async def rerank(
        self,
        nodes: List,
        query_bundle,
        top_n: int = 10,
    ) -> List:
        """异步精排——offload CrossEncoder.predict 到线程池。

        Args:
            nodes: List[NodeWithScore] — 待重排节点
            query_bundle: QueryBundle — 查询
            top_n: 返回数量

        Returns:
            List[NodeWithScore] — 按 CrossEncoder 分数降序排列的 top_n 个节点
        """
        if not nodes:
            return []

        query_str = query_bundle.query_str
        pairs = [(query_str, n.node.get_content()) for n in nodes]

        def _predict():
            return self._model.predict(
                pairs,
                show_progress_bar=False,
            )

        loop = asyncio.get_running_loop()
        scores = await loop.run_in_executor(self._pool, _predict)

        # 将 CrossEncoder 分数写入 node.score，按降序排列
        for node, score in zip(nodes, scores):
            node.score = float(score)

        nodes_sorted = sorted(nodes, key=lambda n: n.score, reverse=True)
        return nodes_sorted[:top_n]
