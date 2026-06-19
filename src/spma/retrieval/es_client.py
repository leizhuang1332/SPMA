"""Elasticsearch 异步客户端——BM25 文本检索。

封装索引 CRUD + 健康检查，通过 BM25Interface Protocol 适配。
设计决策: ES 为文本权威源，存储完整 chunk 文本 + 元数据。
"""

from typing import Any

from elasticsearch import AsyncElasticsearch

from spma.infrastructure.circuit_breaker import circuit_breaker


class ESClient:
    """Elasticsearch 异步客户端，实现 BM25Interface Protocol。"""

    def __init__(
        self,
        hosts: list[str] | None = None,
        index_name: str = "spma_docs",
    ):
        hosts = hosts or ["http://localhost:9200"]
        self._client = AsyncElasticsearch(hosts)
        self.index_name = index_name

    @circuit_breaker("elasticsearch")
    async def search(
        self,
        query: str,
        top_k: int = 20,
        filters: dict | None = None,
    ) -> list[dict]:
        """BM25 关键词搜索。

        Args:
            query: 搜索文本
            top_k: 返回数量
            filters: 可选的 term 过滤条件，如 {"req_ids": ["REQ-187"]}

        Returns:
            [{chunk_id, source_id, source_type, req_ids, content, score, ...}, ...]
        """
        must_clauses: list[dict] = [
            {"match": {"content": query}},
        ]

        filter_clauses: list[dict] = []
        if filters:
            for field, value in filters.items():
                if isinstance(value, list):
                    filter_clauses.append({"terms": {field: value}})
                else:
                    filter_clauses.append({"term": {field: value}})

        body: dict[str, Any] = {
            "size": top_k,
            "query": {
                "bool": {
                    "must": must_clauses,
                }
            },
        }
        if filter_clauses:
            body["query"]["bool"]["filter"] = filter_clauses

        resp = await self._client.search(index=self.index_name, body=body)

        results = []
        for hit in resp["hits"]["hits"]:
            source = hit["_source"]
            source["score"] = float(hit["_score"])
            source["chunk_id"] = source.get("chunk_id", hit["_id"])
            results.append(source)

        return results

    async def index_chunks(self, chunks: list[dict]) -> int:
        """批量索引文档 chunk。

        Returns:
            成功索引的 chunk 数量
        """
        if not chunks:
            return 0

        operations = []
        for chunk in chunks:
            operations.append({"index": {"_index": self.index_name, "_id": chunk["chunk_id"]}})
            operations.append(chunk)

        resp = await self._client.bulk(operations=operations, refresh=True)
        if not resp.get("errors"):
            return len(chunks)
        error_count = sum(1 for item in resp.get("items", []) if "error" in item.get("index", {}))
        return len(chunks) - error_count

    async def delete_by_source(self, source_id: str) -> int:
        """按 source_id 删除所有关联 chunk。

        Returns:
            删除的 chunk 数量
        """
        resp = await self._client.delete_by_query(
            index=self.index_name,
            body={"query": {"term": {"source_id": source_id}}},
            refresh=True,
        )
        return resp.get("deleted", 0)

    async def get_chunks(self, chunk_ids: list[str]) -> list[dict]:
        """批量获取 chunk 完整内容（mget）。"""
        if not chunk_ids:
            return []

        docs = [{"_index": self.index_name, "_id": cid} for cid in chunk_ids]
        resp = await self._client.mget(body={"docs": docs})
        results = []
        for doc in resp["docs"]:
            if doc.get("found"):
                source = doc["_source"]
                source["chunk_id"] = source.get("chunk_id", doc["_id"])
                results.append(source)
        return results

    async def create_index(self, mapping: dict | None = None) -> None:
        """创建索引（如不存在）。"""
        exists = await self._client.indices.exists(index=self.index_name)
        if not exists:
            await self._client.indices.create(index=self.index_name, body=mapping)

    async def delete_index(self) -> None:
        """删除索引。"""
        await self._client.indices.delete(index=self.index_name, ignore=[404])

    async def health_check(self) -> bool:
        """检查 ES 集群是否可用。"""
        try:
            health = await self._client.cluster.health()
            return health.get("status") in ("green", "yellow")
        except Exception:
            return False

    async def close(self) -> None:
        """关闭连接。"""
        await self._client.close()
