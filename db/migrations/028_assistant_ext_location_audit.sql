-- Phase 2 / FG1: LocationWhere read-adapter audit ledger (additive only)

CREATE TABLE IF NOT EXISTS assistant_ext_location_audit (
    id BIGSERIAL PRIMARY KEY,
    event_type TEXT NOT NULL,
    target_key TEXT NOT NULL,
    request_path TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    cache_hit BOOLEAN NOT NULL DEFAULT FALSE,
    success BOOLEAN NOT NULL DEFAULT FALSE,
    http_status INTEGER NULL,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    circuit_state TEXT NOT NULL DEFAULT 'closed',
    error_text TEXT NULL,
    response_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by TEXT NOT NULL DEFAULT 'system',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_location_audit_event_created
    ON assistant_ext_location_audit (event_type, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_location_audit_target_created
    ON assistant_ext_location_audit (target_key, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_location_audit_success_created
    ON assistant_ext_location_audit (success, created_at DESC);