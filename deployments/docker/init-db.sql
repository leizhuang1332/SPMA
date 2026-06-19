-- SPMA PostgreSQL 初始化脚本
-- 由 docker compose postgres 服务的 initdb 机制自动执行

-- 1. 创建向量数据库
CREATE DATABASE spma_vector;
GRANT ALL PRIVILEGES ON DATABASE spma_vector TO spma;

-- 2. 在主库启用扩展
-- \c spma

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- 3. 中文分词 (需要 zhparser 扩展已安装)
-- 如果 zhparser 不可用会报错，暂时注释掉，需要时手动安装
-- CREATE EXTENSION IF NOT EXISTS zhparser;
-- CREATE TEXT SEARCH CONFIGURATION chinese (PARSER = zhparser);
-- ALTER TEXT SEARCH CONFIGURATION chinese ADD MAPPING FOR n,v,a,i,e,l WITH simple;

-- 4. 在向量库启用扩展
-- \c spma_vector

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- 5. 中文分词 (向量库)
-- CREATE EXTENSION IF NOT EXISTS zhparser;
-- CREATE TEXT SEARCH CONFIGURATION chinese (PARSER = zhparser);
-- ALTER TEXT SEARCH CONFIGURATION chinese ADD MAPPING FOR n,v,a,i,e,l WITH simple;

-- 6. 元数据表 (主库 spma)
-- \c spma

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

CREATE INDEX IF NOT EXISTS idx_audit_event_type ON audit_log(event_type);
CREATE INDEX IF NOT EXISTS idx_audit_created_at ON audit_log(created_at);

CREATE TABLE IF NOT EXISTS rate_limits (
    id          SERIAL PRIMARY KEY,
    key         TEXT NOT NULL UNIQUE,
    tokens      INTEGER NOT NULL DEFAULT 0,
    reset_at    TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    user_id     TEXT,
    metadata    JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- file_path_cache: Code Agent 文件路径路由缓存
CREATE TABLE IF NOT EXISTS file_path_cache (
    id          BIGSERIAL PRIMARY KEY,
    repo_name   TEXT NOT NULL,
    file_path   TEXT NOT NULL,
    file_type   TEXT,
    updated_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(repo_name, file_path)
);

CREATE INDEX IF NOT EXISTS idx_fpc_repo ON file_path_cache (repo_name);
CREATE INDEX IF NOT EXISTS idx_fpc_path_trgm ON file_path_cache
    USING GIN (file_path gin_trgm_ops);

CREATE TABLE IF NOT EXISTS ingestion_runs (
    id                   SERIAL PRIMARY KEY,
    pipeline_run_id      TEXT NOT NULL UNIQUE,
    pipeline_type        TEXT NOT NULL,
    source               TEXT,
    mode                 TEXT NOT NULL,
    status               TEXT NOT NULL DEFAULT 'running',
    started_at           TIMESTAMPTZ NOT NULL,
    completed_at         TIMESTAMPTZ,
    estimated_completion TIMESTAMPTZ,
    stats                JSONB DEFAULT '{}',
    errors               JSONB DEFAULT '[]',
    created_by           TEXT NOT NULL,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ingestion_runs_type   ON ingestion_runs(pipeline_type);
CREATE INDEX IF NOT EXISTS idx_ingestion_runs_status ON ingestion_runs(status);
CREATE INDEX IF NOT EXISTS idx_ingestion_runs_created ON ingestion_runs(created_at);    

-- 7. 向量 store 元数据表 (向量库 spma_vector)
-- \c spma_vector

CREATE TABLE IF NOT EXISTS vector_collections (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    dimension   INTEGER NOT NULL,
    metadata    JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS search_log (
    id          SERIAL PRIMARY KEY,
    query_text  TEXT NOT NULL,
    top_k       INTEGER,
    source_type TEXT,
    elapsed_ms  INTEGER,
    hit_count   INTEGER,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_search_log_created_at ON search_log(created_at);

-- 8. data_chunk_embeddings 表 (LlamaIndex PGVectorStore 标准 schema)
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
