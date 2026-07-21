-- Migration 057: Route B conversation-handoff audit table.
--
-- Owner decision (2026-07-22): Route B (modules.route_b, Ollama-only chat
-- for escort/security_guard/candidate/unknown senders) may hand a
-- conversation off to an existing canonical workflow (recruitment intake,
-- an employee-claim admin review, or -- Stage 3, not yet activated -- an
-- escort-order draft) once identity/intent becomes sufficiently clear.
-- Ollama only ever recommends a category; modules.route_b_handoff's Python
-- logic makes the actual decision and never lets Ollama write a role,
-- create an employee, link a phone, or write a final business row.
--
-- fazle_route_b_handoffs is the required auditable record of every handoff
-- decision (including "declined", so a null-result is also traceable, not
-- just successes) and doubles as the idempotency/cooldown substrate: the
-- same message_id must never produce two rows, and a repeated ambiguous
-- claim from the same phone within the cooldown window must not create a
-- second admin draft (see modules.route_b_handoff._recent_handoff()).
--
-- Feature-flagged off by default (ROUTE_B_HANDOFF_ENABLED=false,
-- ROUTE_B_ESCORT_HANDOFF_ENABLED=false, see app/config.py) -- this table
-- exists from this migration onward regardless of the flag, purely
-- additive, safe to leave in place even if the feature is never enabled.

CREATE TABLE IF NOT EXISTS fazle_route_b_handoffs (
    id            BIGSERIAL   PRIMARY KEY,
    phone         TEXT        NOT NULL,
    message_id    TEXT,
    source_route  TEXT        NOT NULL DEFAULT 'route_b',
    destination   TEXT        NOT NULL,   -- 'recruitment' | 'employee_claim_review' | 'escort_order' | 'none'
    reason        TEXT        NOT NULL,   -- e.g. 'detector_entity', 'ollama_suggested+history_corroborated', 'strict_order_complete'
    confidence    TEXT,                   -- free text: detector layer name, or 'ollama_only', or 'phone_verified', or 'unverified'
    outcome       TEXT        NOT NULL,   -- 'handed_off' | 'admin_review' | 'declined'
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_route_b_handoffs_phone_recent
    ON fazle_route_b_handoffs (phone, created_at DESC);

-- Idempotency: the same inbound message must never be recorded twice, when
-- the bridge supplies a message_id (webhook paths always do; some SQLite
-- bridge-poller paths may not, so this is a partial unique index rather
-- than a NOT NULL + unique constraint).
CREATE UNIQUE INDEX IF NOT EXISTS idx_route_b_handoffs_message_id_unique
    ON fazle_route_b_handoffs (message_id)
    WHERE message_id IS NOT NULL;
