-- Phase 3 targeted routing settings registry. Additive only; NOT applied to
-- production by this change. Secret values are deliberately not stored here.
CREATE TABLE IF NOT EXISTS phase3_ai_routes (
    route_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    priority INTEGER NOT NULL CHECK (priority >= 0),
    transport TEXT NOT NULL CHECK (transport IN ('omniroute_gateway', 'direct_provider', 'local_ollama', 'hermes_runner_adapter')),
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    base_url TEXT NOT NULL DEFAULT '',
    credential_type TEXT NOT NULL CHECK (credential_type IN ('api_key', 'bearer_token', 'session_cookie', 'local_runtime', 'none')),
    credential_ref TEXT NOT NULL,
    allowed_workloads TEXT[] NOT NULL,
    capabilities TEXT[] NOT NULL,
    modalities TEXT[] NOT NULL DEFAULT ARRAY['text']::TEXT[],
    privacy_classes TEXT[] NOT NULL,
    cost_class TEXT NOT NULL CHECK (cost_class IN ('low', 'standard', 'high')),
    latency_classes TEXT[] NOT NULL,
    context_limit INTEGER NOT NULL CHECK (context_limit > 0),
    structured_output BOOLEAN NOT NULL DEFAULT FALSE,
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    environments TEXT[] NOT NULL DEFAULT ARRAY['development', 'test']::TEXT[],
    max_attempts INTEGER NOT NULL DEFAULT 1 CHECK (max_attempts BETWEEN 1 AND 3),
    retry_backoff_seconds DOUBLE PRECISION NOT NULL DEFAULT 0 CHECK (retry_backoff_seconds BETWEEN 0 AND 30),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by TEXT NOT NULL DEFAULT 'system'
);

CREATE INDEX IF NOT EXISTS idx_phase3_ai_routes_priority
    ON phase3_ai_routes(priority, route_id);

CREATE TABLE IF NOT EXISTS phase3_ai_route_audit (
    id BIGSERIAL PRIMARY KEY,
    route_id TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('create', 'update', 'delete', 'reorder')),
    actor_ref TEXT NOT NULL,
    before_state JSONB,
    after_state JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_phase3_ai_route_audit_route_time
    ON phase3_ai_route_audit(route_id, created_at DESC);

