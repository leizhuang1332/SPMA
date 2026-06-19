-- SPMA 基础设施初始化 — PGVector (仅 ES + PGVector 部署用)
-- 由 PGVector 容器的 initdb 机制在首次启动时自动执行

-- 启用扩展
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- data_chunk_embeddings 表 (LlamaIndex PGVectorStore 标准 schema)
-- embedding_model: BAAI/bge-m3, dimension: 1024
-- distance: cosine, index: HNSW (m=16, ef_construction=200)
CREATE TABLE IF NOT EXISTS data_chunk_embeddings (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    text            VARCHAR NOT NULL,
    metadata_       JSONB,
    node_id         VARCHAR,
    embedding       vector(1024),
    text_search_tsv TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', "text")) STORED
);

-- HNSW 向量索引
CREATE INDEX IF NOT EXISTS data_chunk_embeddings_embedding_idx
    ON data_chunk_embeddings
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 200);

-- GIN 全文检索索引（hybrid search 需要）
CREATE INDEX IF NOT EXISTS data_chunk_embeddings_text_search_idx
    ON data_chunk_embeddings
    USING gin (text_search_tsv);

-- 元数据表
CREATE TABLE IF NOT EXISTS feature_flags (
    id          SERIAL PRIMARY KEY,
    flag_key    TEXT NOT NULL UNIQUE,
    flag_value  BOOLEAN NOT NULL DEFAULT false,
    description TEXT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS audit_log (
    id          SERIAL PRIMARY KEY,
    event_type  TEXT NOT NULL,
    level       TEXT,
    details     JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    user_id     TEXT,
    metadata    JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
