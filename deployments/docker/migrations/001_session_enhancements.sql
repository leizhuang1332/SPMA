-- Migration 001: 增强 sessions 表 + 添加 agent_traces / agent_rounds 表
-- 用于已有数据库的增量升级
-- 兼容旧表结构 (id TEXT PK) 和新表结构 (session_id TEXT, 加 id SERIAL PK)

-- 1a. 旧表有 id TEXT PK → 重命名为 session_id
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='sessions' AND column_name='id'
          AND data_type = 'text'
    ) THEN
        ALTER TABLE sessions RENAME COLUMN id TO session_id;
    END IF;
END $$;

-- 1b. 添加 title 列
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS title TEXT;

-- 1c. 添加自增主键 id SERIAL（如果还没有）
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS id SERIAL;

-- 1d. 如果旧主键是 session_id，删除并改为 id
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE table_name='sessions' AND constraint_type='PRIMARY KEY'
          AND constraint_name = 'sessions_pkey'
    ) THEN
        ALTER TABLE sessions DROP CONSTRAINT sessions_pkey;
    END IF;
END $$;

-- 1e. 设置 id 为主键（如果还未设置）
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE table_name='sessions' AND constraint_type='PRIMARY KEY'
    ) THEN
        ALTER TABLE sessions ADD PRIMARY KEY (id);
    END IF;
END $$;

-- 1f. 给 session_id 添加 UNIQUE 约束
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE table_name='sessions' AND constraint_name='sessions_session_id_key'
    ) THEN
        ALTER TABLE sessions ADD CONSTRAINT sessions_session_id_key UNIQUE (session_id);
    END IF;
END $$;

-- 1g. session_id 非空约束（如果允许空）
ALTER TABLE sessions ALTER COLUMN session_id SET NOT NULL;

-- 2. agent_traces 表：每次查询的完整记录
CREATE TABLE IF NOT EXISTS agent_traces (
    query_id            TEXT PRIMARY KEY,
    session_id          TEXT NOT NULL,
    original_query      TEXT NOT NULL,
    answer              TEXT DEFAULT '',
    classification      JSONB DEFAULT '{}',
    entities            JSONB DEFAULT '{}',
    worker_outputs      JSONB DEFAULT '[]',
    quality_scores      JSONB DEFAULT '{}',
    reschedule_count    INTEGER DEFAULT 0,
    total_llm_calls     INTEGER DEFAULT 0,
    total_tokens        INTEGER DEFAULT 0,
    convergence_reason  TEXT DEFAULT '',
    latency_ms          INTEGER DEFAULT 0,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_agent_traces_session ON agent_traces(session_id);
CREATE INDEX IF NOT EXISTS idx_agent_traces_created ON agent_traces(created_at);

-- 3. agent_rounds 表：Agent 内部轮次记录
CREATE TABLE IF NOT EXISTS agent_rounds (
    id              BIGSERIAL PRIMARY KEY,
    query_id        TEXT NOT NULL,
    agent_type      TEXT NOT NULL,
    round_num       INTEGER NOT NULL,
    action          TEXT DEFAULT '',
    results_summary TEXT DEFAULT '',
    assessment      TEXT DEFAULT '',
    confidence      REAL DEFAULT 0,
    latency_ms      INTEGER DEFAULT 0,
    llm_calls       INTEGER DEFAULT 0,
    tokens_used     INTEGER DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_agent_rounds_query ON agent_rounds(query_id);
