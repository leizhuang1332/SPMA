-- Migration 002: Query Rewriter 缓存 / 状态 / 审计基础表
-- 依赖: PG 16 + pgvector 0.7+

-- 1) 权重历史快照
CREATE TABLE IF NOT EXISTS qr_weights_history (
    weights_set_id  BIGSERIAL PRIMARY KEY,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source          TEXT NOT NULL CHECK (source IN ('ema','manual','rollback','init')),
    applied_at      TIMESTAMPTZ,
    approver        TEXT,
    payload         JSONB NOT NULL,
    is_active       BOOLEAN NOT NULL DEFAULT FALSE,
    CONSTRAINT qr_weights_only_one_active EXCLUDE USING btree (is_active WITH =) WHERE (is_active = true)
);

-- 2) 单行状态元数据(权重版本号 + synonym 版本号)
CREATE TABLE IF NOT EXISTS qr_state_meta (
    state_id        INT PRIMARY KEY DEFAULT 1,
    weights_version BIGINT NOT NULL DEFAULT 1,
    synonym_version BIGINT NOT NULL DEFAULT 1,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT qr_state_single_row CHECK (state_id = 1)
);
INSERT INTO qr_state_meta (state_id) VALUES (1) ON CONFLICT DO NOTHING;

-- 3) L2 语义缓存
CREATE TABLE IF NOT EXISTS qr_cache_entries (
    cache_id        BIGSERIAL PRIMARY KEY,
    query_hash      TEXT NOT NULL,
    weights_version BIGINT NOT NULL,
    synonym_version BIGINT NOT NULL,
    embedding       vector(1024) NOT NULL,
    payload         JSONB NOT NULL,
    ttl_ts          TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    query_preview   TEXT,
    UNIQUE (query_hash, weights_version, synonym_version)
);

CREATE INDEX IF NOT EXISTS idx_qr_cache_hnsw ON qr_cache_entries
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

CREATE INDEX IF NOT EXISTS idx_qr_cache_ttl ON qr_cache_entries (ttl_ts);

-- 4) 请求审计(unlogged + 按月分区,本期只建 default partition)
CREATE UNLOGGED TABLE IF NOT EXISTS qr_request_audit (
    request_id      UUID NOT NULL,
    ts              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    query_hash      TEXT NOT NULL,
    rewritten_hash  TEXT,
    pii_types       TEXT[],
    stage           TEXT NOT NULL,
    strategy_weights JSONB,
    weights_version BIGINT,
    synonym_version BIGINT,
    latency_ms      INT,
    cache_hit_l1    BOOLEAN,
    cache_hit_l2    BOOLEAN,
    cache_layer     TEXT,
    error_stage     TEXT,
    fallback_level  TEXT
) PARTITION BY RANGE (ts);
CREATE TABLE IF NOT EXISTS qr_request_audit_default PARTITION OF qr_request_audit DEFAULT;