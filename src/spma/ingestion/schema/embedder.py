"""BGE-M3 批量 embedding + PGVector 写入（LlamaIndex data_chunk_embeddings 标准 schema）。"""


async def embed_and_upsert(
    chunks: list[dict],
    vector_store=None,
    embedding_client=None,
    batch_size: int = 32,
) -> int:
    """批量生成 embedding 并 upsert 到 PGVector。

    Args:
        chunks: SchemaChunk 列表，每个含 table_name, business_description, ddl, columns, foreign_keys
        vector_store: PGVector 客户端
        embedding_client: BGE-M3 embedding 客户端
        batch_size: 批处理大小

    Returns:
        成功写入的 chunk 数量
    """
    if vector_store is None or embedding_client is None:
        return 0

    written = 0
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i:i + batch_size]
        descriptions = [c["business_description"] for c in batch]
        vectors = await embedding_client.embed_batch(descriptions)

        for chunk, vector in zip(batch, vectors):
            await vector_store.upsert(
                node_id=f"schema:{chunk['table_name']}",
                text=chunk["business_description"],
                embedding=vector,
                metadata={
                    "table_name": chunk["table_name"],
                    "ddl": chunk["ddl"],
                    "columns": chunk["columns"],
                    "foreign_keys": chunk["foreign_keys"],
                    "few_shot_queries": chunk.get("few_shot_queries", []),
                },
            )
            written += 1

    return written
