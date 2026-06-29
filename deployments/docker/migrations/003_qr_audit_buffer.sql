-- Migration 003: qr_audit_buffer fallback table for audit subsystem
-- Used by QrAuditBuffer when direct INSERT to qr_request_audit fails.
-- This implements spec §3.1 — fallback persistence for lost audit batches.
-- (qr_audit_buffer intentionally UNLOGGED — flush is best-effort by design)
CREATE UNLOGGED TABLE IF NOT EXISTS qr_audit_buffer (
    audit_id        BIGSERIAL PRIMARY KEY,
    payload         JSONB NOT NULL,
    enqueued_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_qr_audit_buffer_enqueued ON qr_audit_buffer (enqueued_at);