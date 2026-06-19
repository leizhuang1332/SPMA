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
        source_handlers: dict | None = None,  # {source_type: SourceHandler}
    ):
        self.es = es_client
        self.vector_store = vector_store
        self.embedder = embedder
        self.chunker = chunker or SemanticChunker()
        self._handlers = source_handlers or {}

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

        pg_count = 0
        try:
            embeddings = await self.embedder.embed([c.content for c in chunks])
            batch = [
                {
                    "node_id": chunk.chunk_id,
                    "text": chunk.content,
                    "embedding": emb,
                    "metadata": {
                        "source_id": chunk.source_id,
                        "source_type": chunk.source_type,
                        "req_ids": chunk.req_ids,
                        "doc_type": chunk.doc_type,
                        "version": chunk.version,
                        "updated_at": chunk.updated_at,
                        "chunk_index": chunk.chunk_index,
                        "page_title": chunk.page_title,
                    },
                }
                for chunk, emb in zip(chunks, embeddings)
            ]
            await self.vector_store.upsert_batch(batch)
            pg_count = len(batch)
        except Exception as e:
            logger.error(f"PGVector 写入失败 (source={source_id}): {e}")

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

    async def run(self, request) -> "IngestionResult":
        """Execute document ingestion based on the request source.

        Dispatches to the appropriate SourceHandler, then ingests each
        yielded SourceDocument via ingest_document() or update_document().
        """
        from spma.api.schemas.ingestion import IngestionResult

        handler = self._handlers.get(request.source.value)
        if not handler:
            return IngestionResult(
                status="failed",
                errors=[{"error": f"Unsupported source: {request.source.value}"}],
                stats={},
            )

        stats = {"files_processed": 0, "chunks_generated": 0, "errors": 0}
        errors: list[dict] = []

        should_full_reindex = (
            request.mode == "full" or request.options.force_full_reindex
        )

        async for doc in handler.fetch_documents(request):
            try:
                if should_full_reindex:
                    chunks = await self.update_document(
                        text=doc.text,
                        source_id=doc.source_id,
                        source_type=doc.source_type,
                        page_title=doc.page_title,
                        req_ids=doc.req_ids,
                        doc_type=doc.doc_type,
                        version=doc.version,
                    )
                else:
                    chunks = await self.ingest_document(
                        text=doc.text,
                        source_id=doc.source_id,
                        source_type=doc.source_type,
                        page_title=doc.page_title,
                        req_ids=doc.req_ids,
                        doc_type=doc.doc_type,
                        version=doc.version,
                    )
                stats["files_processed"] += 1
                stats["chunks_generated"] += chunks
            except Exception as e:
                logger.error("Failed to ingest %s: %s", doc.source_id, e)
                errors.append({"source_id": doc.source_id, "error": str(e)})
                stats["errors"] += 1

        return IngestionResult(
            status="completed" if not errors else "completed_with_errors",
            stats=stats,
            errors=errors,
        )

    @staticmethod
    def _chunk_to_dict(chunk: DocChunk) -> dict:
        return {
            "chunk_id": chunk.chunk_id,
            "source_id": chunk.source_id,
            "source_type": chunk.source_type,
            "req_ids": chunk.req_ids,
            "content": chunk.content,
            "doc_type": chunk.doc_type,
            "version": chunk.version,
            "updated_at": chunk.updated_at,
            "chunk_index": chunk.chunk_index,
            "page_title": chunk.page_title,
        }
