-- SPMA 基础设施初始化 — PGVector (仅 ES + PGVector 部署用)
-- 由 PGVector 容器的 initdb 机制在首次启动时自动执行

-- 启用扩展
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- chunk embeddings 表 (匹配 config/spma.yaml)
-- embedding_model: BAAI/bge-m3, dimension: 1024
-- distance: cosine, index: HNSW (m=16, ef_construction=200)
CREATE TABLE IF NOT EXISTS chunk_embeddings (
    id          SERIAL PRIMARY KEY,
    chunk_id    TEXT NOT NULL UNIQUE,
    source_id   TEXT,
    source_type TEXT,
    content     TEXT,
    embedding   vector(1024),
    metadata    JSONB DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- HNSW 向量索引
CREATE INDEX IF NOT EXISTS chunk_embedding_hnsw_idx
    ON chunk_embeddings
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 200);

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
