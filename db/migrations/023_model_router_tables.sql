-- ============================================================
-- Phase 4 / FG3: Model Router Governance tables
-- File: db/migrations/023_model_router_tables.sql
-- Additive-only migration.
-- ============================================================

CREATE TABLE IF NOT EXISTS assistant_model_decisions (
    id BIGSERIAL PRIMARY KEY,
    request_id TEXT,
    task_class TEXT NOT NULL,
    selected_model TEXT NOT NULL,
    provider_chain JSONB NOT NULL DEFAULT '[]'::jsonb,
    reason TEXT NOT NULL,
    requested_model TEXT,
    decision_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_model_decisions_created_at
    ON assistant_model_decisions (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_model_decisions_task_created
    ON assistant_model_decisions (task_class, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_model_decisions_request
    ON assistant_model_decisions (request_id)
    WHERE request_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS assistant_model_eval_samples (
    id BIGSERIAL PRIMARY KEY,
    sample_key TEXT NOT NULL,
    task_class TEXT NOT NULL,
    prompt_text TEXT NOT NULL,
    expected_model TEXT,
    expected_provider_chain JSONB NOT NULL DEFAULT '[]'::jsonb,
    expected_reason TEXT,
    evaluation_notes TEXT,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_by TEXT NOT NULL DEFAULT 'system',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (sample_key)
);

CREATE INDEX IF NOT EXISTS idx_model_eval_samples_task_active
    ON assistant_model_eval_samples (task_class, active);

CREATE INDEX IF NOT EXISTS idx_model_eval_samples_created_at
    ON assistant_model_eval_samples (created_at DESC);
