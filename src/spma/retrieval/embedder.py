"""BGE-M3 Embedder——基于 ModelScope 下载 + sentence-transformers 本地推理。

使用方式:
    embedder = await BGEM3Embedder.create()
    vectors = await embedder.embed(["文本1", "文本2"])
    # → [[0.12, -0.34, ...], [0.56, 0.78, ...]]  # 1024 维
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

MODEL_ID = "BAAI/bge-m3"
EMBEDDING_DIM = 1024


class BGEM3Embedder:
    """BGE-M3 本地嵌入服务。

    - 下载源: ModelScope (modelscope.cn)
    - 推理引擎: sentence-transformers
    - 维度: 1024
    - 后端: CPU（开发用），GPU 可通过 device 参数切换
    """

    def __init__(self, model, pool):
        self._model = model
        self._pool = pool

    @classmethod
    async def create(cls, device: str = "cpu") -> "BGEM3Embedder":
        """异步工厂——从 ModelScope 下载 BGE-M3 并加载。

        Args:
            device: "cpu" | "cuda" | "mps"
        """
        logger.info("正在从 ModelScope 加载 %s (%s)...", MODEL_ID, device)

        pool = ThreadPoolExecutor(max_workers=1)

        def _load():
            import os
            os.environ.setdefault("SENTENCE_TRANSFORMERS_HOME", os.path.expanduser("~/.cache/modelscope/hub"))

            from sentence_transformers import SentenceTransformer

            cache_root = os.path.expanduser("~/.cache/modelscope/hub")
            model_dir = os.path.join(cache_root, MODEL_ID)

            if os.path.isdir(model_dir) and os.listdir(model_dir):
                logger.info("BGE-M3 已在本地缓存，跳过下载: %s", model_dir)
                return SentenceTransformer(model_dir, device=device)

            logger.info("本地缓存未命中，从 ModelScope 下载 BGE-M3...")
            import logging as _logging
            _logging.getLogger("modelscope").setLevel(_logging.WARNING)

            from modelscope import snapshot_download
            local_path = snapshot_download(MODEL_ID, cache_dir=cache_root)
            return SentenceTransformer(local_path, device=device)

        model = await asyncio.get_event_loop().run_in_executor(pool, _load)
        logger.info("BGE-M3 加载完成，维度: %d", model.get_embedding_dimension())
        return cls(model, pool)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """异步批量文本嵌入。

        Args:
            texts: 文本列表

        Returns:
            embedding 向量列表，每个向量 1024 维
        """
        def _encode():
            return self._model.encode(
                texts,
                normalize_embeddings=True,
                show_progress_bar=False,
            )

        embeddings = await asyncio.get_event_loop().run_in_executor(self._pool, _encode)
        return [emb.tolist() for emb in embeddings]

    @property
    def dim(self) -> int:
        return EMBEDDING_DIM
