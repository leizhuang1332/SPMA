"""BM25 抽象接口（Protocol）。

通过 Protocol 解耦具体实现:
- PGtsvectorBM25: Phase 1-2 使用
- ElasticsearchBM25: Phase 3 切换时零 Agent 代码改动
"""

from typing import Protocol


class BM25Interface(Protocol):
    """BM25 检索引擎的抽象接口。"""

    async def search(self, query: str, top_k: int, filters: dict | None = None) -> list[dict]:
        """BM25 关键词搜索。"""
        ...

    async def index(self, documents: list[dict]) -> None:
        """索引文档。"""
        ...

    async def delete(self, doc_ids: list[str]) -> None:
        """删除文档索引。"""
        ...

    async def health_check(self) -> bool:
        """检查引擎是否可用。"""
        ...
