-- Migration 004: synonym_map 表 + 索引 + 触发器
-- 依赖: PostgreSQL 16 + pgvector 0.7+
-- 修复 G1: 代码已 SELECT FROM synonym_map 但表不存在

CREATE TABLE IF NOT EXISTS synonym_map (
    id                  BIGSERIAL PRIMARY KEY,
    user_term           TEXT NOT NULL,
    canonical_term      TEXT NOT NULL,
    category            TEXT,
    source              TEXT NOT NULL,
    confidence          REAL NOT NULL DEFAULT 0.5
                        CHECK (confidence >= 0.0 AND confidence <= 1.0),
    status              TEXT NOT NULL DEFAULT 'pending_review'
                        CHECK (status IN ('active', 'pending_review', 'deprecated')),
    hits_30d            INTEGER NOT NULL DEFAULT 0,
    last_triggered_at   TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_term, canonical_term, source)
);

-- 索引:仅在 active 子集建(避免基数过大)
CREATE INDEX IF NOT EXISTS idx_synonym_user_term
    ON synonym_map (user_term) WHERE status = 'active';

-- 复合索引:支撑 query() 的 "ORDER BY confidence DESC, hits_30d DESC"
CREATE INDEX IF NOT EXISTS idx_synonym_status_confidence
    ON synonym_map (status, confidence DESC, hits_30d DESC);

-- 触发器:自动维护 updated_at
CREATE OR REPLACE FUNCTION synonym_map_touch()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_synonym_map_touch ON synonym_map;
CREATE TRIGGER trg_synonym_map_touch
    BEFORE UPDATE ON synonym_map
    FOR EACH ROW EXECUTE FUNCTION synonym_map_touch();