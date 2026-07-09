-- Migration 020 (FG4): Preflight guard control-plane tables
-- Safety: additive only. No existing table is altered, dropped, or renamed.

CREATE TABLE IF NOT EXISTS assistant_release_gates (
    id SERIAL PRIMARY KEY,
    release_id TEXT NOT NULL,
    phase INTEGER NOT NULL,
    feature_group TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS assistant_preflight_reports (
    id SERIAL PRIMARY KEY,
    release_id TEXT NOT NULL,
    run_at TIMESTAMPTZ DEFAULT NOW(),
    status TEXT NOT NULL,
    path_check TEXT NOT NULL,
    table_check TEXT NOT NULL,
    blocked_paths JSONB DEFAULT '[]'::jsonb,
    blocked_tables JSONB DEFAULT '[]'::jsonb,
    raw_report JSONB,
    created_by TEXT DEFAULT 'system'
);
