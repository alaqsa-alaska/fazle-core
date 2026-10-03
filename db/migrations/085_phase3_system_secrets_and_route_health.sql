-- Shared Phase 3 system credentials and passive route-health metadata.
-- Additive and rerunnable. Secret ciphertext is never stored in route/audit rows.
CREATE TABLE IF NOT EXISTS phase3_system_secrets (
    secret_ref TEXT PRIMARY KEY,
    credential_type TEXT NOT NULL CHECK (credential_type IN ('api_key', 'bearer_token', 'session_cookie')),
    encrypted_value TEXT NOT NULL,
    allowed_providers TEXT[] NOT NULL,
    allowed_consumers TEXT[] NOT NULL,
    allowed_workloads TEXT[] NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS phase3_system_secret_audit (
    id BIGSERIAL PRIMARY KEY,
    secret_ref TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('create', 'update', 'rotate', 'delete')),
    actor_ref TEXT NOT NULL,
    credential_type TEXT NOT NULL,
    allowed_providers TEXT[] NOT NULL,
    allowed_consumers TEXT[] NOT NULL,
    allowed_workloads TEXT[] NOT NULL,
    version INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_phase3_system_secret_audit_ref_time
    ON phase3_system_secret_audit(secret_ref, created_at DESC);

ALTER TABLE phase3_ai_routes
    ADD COLUMN IF NOT EXISTS eligible_consumers TEXT[] NOT NULL DEFAULT ARRAY['core']::TEXT[],
    ADD COLUMN IF NOT EXISTS qualification_status TEXT NOT NULL DEFAULT 'unqualified',
    ADD COLUMN IF NOT EXISTS qualified_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_success_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_failure_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_failure_class TEXT;
