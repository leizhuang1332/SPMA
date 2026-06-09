"""PRD 文档摄入主流程。

Parser → SemanticChunker → BGE-M3 embedding → ES + PGVector 双写
"""

import logging
from datetime import datetime, timezone

from spma.ingestion.chunkers.semantic_chunker import SemanticChunker, DocChunk
from spma.retrieval.es_client import ESClient

logger = logging.getLogger(__name__)


class DocIngestionPipeline:
    """PRD 文档摄入管道——解析→分块→嵌入→双写。"""

    def __init__(
        self,
        es_client: ESClient,
        vector_store,  # PGVector client (Phase 1 提供的接口)
        embedder,       # BGE-M3 embedding 客户端
        chunker: SemanticChunker | None = None,
    ):
        self.es = es_client
        self.vector_store = vector_store
        self.embedder = embedder
        self.chunker = chunker or SemanticChunker()

    async def ingest_document(
        self,
        text: str,
        source_id: str,
        source_type: str = "confluence",
        req_ids: list[str] | None = None,
        doc_type: str = "prd",
        version: str = "",
        page_title: str = "",
    ) -> int:
        """摄入单个文档——全流程。

        Returns:
            成功写入的 chunk 数量
        """
        chunks = self.chunker.split(
            text=text,
            source_id=source_id,
            source_type=source_type,
            req_ids=req_ids,
            doc_type=doc_type,
            version=version,
            updated_at=datetime.now(timezone.utc).isoformat(),
            page_title=page_title,
        )

        if not chunks:
            logger.warning(f"文档 {source_id} 分块后无内容")
            return 0

        chunk_dicts = [self._chunk_to_dict(c) for c in chunks]

        # 并行写入 ES + PGVector
        es_count = await self.es.index_chunks(chunk_dicts)

        try:
            embeddings = await self.embedder.embed([c.content for c in chunks])
            pg_count = await self.vector_store.upsert(
                [(c.chunk_id, emb, c.source_id) for c, emb in zip(chunks, embeddings)],
                table="chunk_embeddings",
            )
        except Exception as e:
            logger.error(f"PGVector 写入失败 (source={source_id}): {e}")
            pg_count = 0

        logger.info(
            f"摄入完成: source={source_id}, chunks={len(chunks)}, "
            f"es={es_count}, pgvector={pg_count}"
        )
        return len(chunks)

    async def update_document(
        self,
        text: str,
        source_id: str,
        source_type: str = "confluence",
        req_ids: list[str] | None = None,
        doc_type: str = "prd",
        version: str = "",
        page_title: str = "",
    ) -> int:
        """更新文档——删旧写新。"""
        deleted_es = await self.es.delete_by_source(source_id)
        deleted_pg = await self.vector_store.delete_by_source(source_id)
        logger.info(f"删除旧 chunks: es={deleted_es}, pgvector={deleted_pg}")

        return await self.ingest_document(
            text=text,
            source_id=source_id,
            source_type=source_type,
            req_ids=req_ids,
            doc_type=doc_type,
            version=version,
            page_title=page_title,
        )

    async def delete_document(self, source_id: str) -> tuple[int, int]:
        """删除文档——ES + PGVector 并行删除。"""
        deleted_es = await self.es.delete_by_source(source_id)
        deleted_pg = await self.vector_store.delete_by_source(source_id)
        return deleted_es, deleted_pg

    @staticmethod
    def _chunk_to_dict(chunk: DocChunk) -> dict:
        return {
            "chunk_id": chunk.chunk_id,
            "source_id": chunk.source_id,
            "source_type": "prd",
            "req_ids": chunk.req_ids,
            "content": chunk.content,
            "doc_type": chunk.doc_type,
            "version": chunk.version,
            "updated_at": chunk.updated_at,
            "chunk_index": chunk.chunk_index,
            "page_title": chunk.page_title,
        }
