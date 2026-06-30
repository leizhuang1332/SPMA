-- Migration 005: repo_registry 表（design-13 §3.2 + design-03 §3.6 落地）
-- 依赖: PostgreSQL 16 + pg_trgm 扩展

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS repo_registry (
    id              SERIAL PRIMARY KEY,
    repo_name       VARCHAR(255) NOT NULL UNIQUE,
    display_name    VARCHAR(255) NOT NULL,
    description     TEXT NOT NULL,
    tags            TEXT[] NOT NULL DEFAULT '{}',
    repo_url        TEXT,
    local_path      TEXT,
    languages       JSONB NOT NULL DEFAULT '[]',
    last_indexed_at TIMESTAMPTZ,
    enabled         BOOLEAN NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_repo_registry_enabled
    ON repo_registry (enabled) WHERE enabled = true;

CREATE INDEX IF NOT EXISTS idx_repo_registry_name_trgm
    ON repo_registry USING GIN (repo_name gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_repo_registry_display_name_trgm
    ON repo_registry USING GIN (display_name gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_repo_registry_description_trgm
    ON repo_registry USING GIN (description gin_trgm_ops);

COMMENT ON TABLE repo_registry IS '仓库元数据唯一真相源（design-13 §3.2 + design-03 §3.6）';
