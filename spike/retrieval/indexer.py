"""BM25 + embedding 索引构建。"""

import pickle
from pathlib import Path

import numpy as np
from rank_bm25 import BM25Okapi
from sentence_transformers import SentenceTransformer

from spike.retrieval.corpus_loader import Document


class HybridIndexer:
    """构建 BM25 和 embedding 双索引。"""

    def __init__(self, embedding_model_name: str = "all-MiniLM-L6-v2") -> None:
        self.embedding_model = SentenceTransformer(embedding_model_name)
        self.bm25: BM25Okapi | None = None
        self.embeddings: np.ndarray | None = None  # shape: (n_docs, dim)
        self.documents: list[Document] = []

    def index(self, documents: list[Document]) -> None:
        """对文档列表构建双索引。

        Args:
            documents: 待索引的文档列表
        """
        self.documents = documents
        contents = [doc.content for doc in documents]

        # BM25 索引
        tokenized = [content.split() for content in contents]
        self.bm25 = BM25Okapi(tokenized)

        # Embedding 索引
        self.embeddings = self.embedding_model.encode(
            contents, show_progress_bar=True, convert_to_numpy=True
        )

    def save(self, dir_path: str | Path) -> None:
        """持久化索引到磁盘。"""
        dir_path = Path(dir_path)
        dir_path.mkdir(parents=True, exist_ok=True)

        with open(dir_path / "bm25.pkl", "wb") as f:
            pickle.dump(self.bm25, f)

        np.save(dir_path / "embeddings.npy", self.embeddings)

        with open(dir_path / "documents.pkl", "wb") as f:
            pickle.dump(self.documents, f)

    def load(self, dir_path: str | Path) -> None:
        """从磁盘加载索引。"""
        dir_path = Path(dir_path)

        with open(dir_path / "bm25.pkl", "rb") as f:
            self.bm25 = pickle.load(f)

        self.embeddings = np.load(dir_path / "embeddings.npy")

        with open(dir_path / "documents.pkl", "rb") as f:
            self.documents = pickle.load(f)
