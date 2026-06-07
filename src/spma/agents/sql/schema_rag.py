"""Schema RAG——检索相关表的 DDL + 列注释 + 业务元数据。

增强注入: 列的业务含义、枚举值映射、外键关系、常见查询

检索策略:
- 路径 A (精确命中): entities.table_names 非空 → PGVector 按 table_name 精确查询
- 路径 B (语义搜索): entities.table_names 为空 → BGE-M3 embedding → PGVector HNSW top_k=5

设计依据: SPMA-design-04 §3.1 业务元数据注入
"""

from spma.agents.sql.state import SchemaHit
from spma.models.entities import WorkerEntities


async def search_schema(
    query: str,
    entities: WorkerEntities | None = None,
    top_k: int = 5,
    vector_store=None,
    embedding_client=None,
) -> list[SchemaHit]:
    """检索相关表的 Schema 信息。"""
    table_names = entities.get("table_names", []) if entities else []

    if table_names:
        return await _exact_match_search(table_names, vector_store)

    return await _semantic_search(query, top_k, vector_store, embedding_client)


async def _exact_match_search(
    table_names: list[str],
    vector_store=None,
) -> list[SchemaHit]:
    """路径 A: 按表名精确查询 PGVector。"""
    if vector_store is None:
        return []
    hits = []
    for table_name in table_names:
        row = await vector_store.get_by_table_name(table_name)
        if row:
            hits.append(_row_to_schema_hit(row, relevance_score=1.0))
    return hits


async def _semantic_search(
    query: str,
    top_k: int,
    vector_store=None,
    embedding_client=None,
) -> list[SchemaHit]:
    """路径 B: 语义搜索。"""
    if vector_store is None or embedding_client is None:
        return []

    query_vector = await embedding_client.embed(query)
    rows = await vector_store.similarity_search(query_vector, top_k=top_k)
    return [_row_to_schema_hit(row, relevance_score=row.get("relevance_score", 0.0)) for row in rows]


def _row_to_schema_hit(row: dict, relevance_score: float) -> SchemaHit:
    """将 PGVector 查询行转化为 SchemaHit。"""
    return SchemaHit(
        table_name=row["table_name"],
        ddl=row.get("ddl", ""),
        columns=row.get("columns_meta", []),
        foreign_keys=row.get("foreign_keys", []),
        business_description=row.get("business_description", ""),
        few_shot_queries=row.get("few_shot_queries", []),
        relevance_score=relevance_score,
    )
