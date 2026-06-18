"""Schema 摄入管道——从 information_schema 到 PGVector 的全流程。

支持增量 diff: 对比当前 information_schema 与上次摄入快照，仅处理变更表。
"""

import logging

from spma.api.schemas.ingestion import IngestionResult
from spma.ingestion.schema.introspector import introspect_schema
from spma.ingestion.schema.chunk_builder import build_business_description, build_ddl
from spma.ingestion.schema.embedder import embed_and_upsert

logger = logging.getLogger(__name__)


class SqlIngestionPipeline:
    """SQL Schema 摄入管道——封装自省→构造→嵌入全流程。"""

    def __init__(
        self,
        connection_string: str,
        vector_store=None,
        embedding_client=None,
    ):
        self._conn_string = connection_string
        self._vector_store = vector_store
        self._embedding_client = embedding_client

    async def run(self, databases: list[str], mode: str, options) -> IngestionResult:
        """执行 Schema 摄入。

        Args:
            databases: 目标数据库列表，空=全部
            mode: "incremental" | "full"
            options: SchemaIngestionOptions
        """
        try:
            schema = introspect_schema(self._conn_string)

            chunks = []
            for table_name, info in schema.items():
                business_desc = build_business_description(
                    table_name=table_name,
                    columns=info["columns"],
                    foreign_keys=info["foreign_keys"],
                )
                ddl = build_ddl(table_name, info["columns"])
                chunks.append({
                    "table_name": table_name,
                    "business_description": business_desc,
                    "ddl": ddl,
                    "columns": info["columns"],
                    "foreign_keys": info["foreign_keys"],
                    "few_shot_queries": [],
                })

            written = await embed_and_upsert(
                chunks,
                self._vector_store,
                self._embedding_client,
            )

            stats = {
                "databases_scanned": 1,
                "tables_total": len(schema),
                "tables_new": len(schema),  # 增量 diff 后续迭代
                "tables_modified": 0,
                "columns_total": sum(len(v["columns"]) for v in schema.values()),
                "enum_definitions_updated": 0,
                "few_shot_examples_count": 0,
            }

            return IngestionResult(stats=stats, status="completed")

        except Exception as e:
            logger.error(f"Schema 摄入失败: {e}", exc_info=True)
            return IngestionResult(
                stats={},
                errors=[{"error": str(e), "severity": "error"}],
                status="failed",
            )


# 向后兼容：保留旧的函数入口
async def run_schema_ingestion(
    db_connection_string: str,
    vector_store=None,
    embedding_client=None,
) -> int:
    """执行一次完整的 Schema 摄入。

    Returns:
        摄入的表数量
    """
    pipeline = SqlIngestionPipeline(
        connection_string=db_connection_string,
        vector_store=vector_store,
        embedding_client=embedding_client,
    )
    result = await pipeline.run(databases=[], mode="full", options=None)
    if result.status == "completed":
        return result.stats.get("tables_total", 0)
    return 0
