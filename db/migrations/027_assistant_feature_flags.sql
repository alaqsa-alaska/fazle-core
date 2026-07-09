-- Phase 1 Step 6 — Feature flags and kill-switch control plane
-- Additive-only migration.

CREATE TABLE IF NOT EXISTS assistant_feature_flags (
    id SERIAL PRIMARY KEY,
    feature_name TEXT NOT NULL UNIQUE,
    is_active BOOLEAN NOT NULL DEFAULT FALSE,
    kill_switch_active BOOLEAN NOT NULL DEFAULT FALSE,
    updated_by TEXT DEFAULT 'system',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_assistant_feature_flags_feature_name
    ON assistant_feature_flags(feature_name);