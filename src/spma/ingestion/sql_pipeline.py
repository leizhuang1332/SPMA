"""Schema 摄入管道——从 information_schema 到 PGVector 的全流程。

依赖: PGVector + BGE-M3 embedding 服务已就绪。
"""

from spma.ingestion.schema.introspector import introspect_schema
from spma.ingestion.schema.chunk_builder import build_business_description, build_ddl
from spma.ingestion.schema.embedder import embed_and_upsert


async def run_schema_ingestion(
    db_connection_string: str,
    vector_store=None,
    embedding_client=None,
) -> int:
    """执行一次完整的 Schema 摄入。

    Returns:
        摄入的表数量
    """
    schema = introspect_schema(db_connection_string)

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

    written = await embed_and_upsert(chunks, vector_store, embedding_client)
    return written
