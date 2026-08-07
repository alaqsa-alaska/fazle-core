-- Migration 060: Hermes Kernel Scaffolding (Stage C0.2, 2026-08-07)
--
-- Creates two append-only shadow tables for the Hermes AI Operating System
-- Kernel. These tables have no foreign keys to any business table and are
-- never read by any existing production query. Rollback = set feature flags
-- to false; these tables can be left empty with zero production impact.
--
-- hermes_decision_audit_log: one row per process_message() turn when
--   HERMES_DECISION_AUDIT_ENABLED=true. Required by
--   HERMES_AI_DECISION_CONTRACT_2026-08-06.md Section 6.
--
-- hermes_event_log: one row per standard kernel event (EVT-001 to EVT-015)
--   when HERMES_EVENT_LOG_ENABLED=true. Required by
--   HERMES_EVENT_MODEL_2026-08-06.md.
--
-- Both tables are append-only per the Architectural Principles document.
-- No DELETE or UPDATE is ever issued against them.

CREATE TABLE IF NOT EXISTS hermes_decision_audit_log (
    id                   BIGSERIAL    PRIMARY KEY,
    trace_id             UUID         NOT NULL,
    timestamp            TIMESTAMPTZ  NOT NULL DEFAULT now(),
    sender_phone         VARCHAR(30)  NOT NULL,
    identity_role        VARCHAR(30),
    identity_confidence  REAL,        -- 0.0-1.0; populated after KS-001 upgrade
    intent               VARCHAR(50),
    intent_method        VARCHAR(20), -- regex | keyword | llm | unknown
    evidence_coverage    REAL,        -- 0.0-1.0; populated after KS-005 upgrade
    confidence_score     REAL,        -- overall trust score; populated in C1+
    confidence_band      VARCHAR(20), -- verified | partial | inference | unknown
    chosen_action        VARCHAR(20), -- reply | draft | clarification | escalation | no_action
    explanation          TEXT,
    escalation_reason    TEXT,
    provider_used        VARCHAR(50),
    source               VARCHAR(20), -- bridge1 | bridge2 | bridge3 | webhook
    raw_input_text       TEXT,
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_hermes_dal_trace
    ON hermes_decision_audit_log (trace_id);
CREATE INDEX IF NOT EXISTS idx_hermes_dal_sender
    ON hermes_decision_audit_log (sender_phone, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_hermes_dal_action
    ON hermes_decision_audit_log (chosen_action, created_at DESC);

-- ──────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS hermes_event_log (
    id          BIGSERIAL    PRIMARY KEY,
    event_id    UUID         NOT NULL UNIQUE,
    trace_id    UUID         NOT NULL,
    event_name  VARCHAR(50)  NOT NULL, -- EVT-001 message.received, etc.
    timestamp   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    payload     JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_hermes_event_log_trace
    ON hermes_event_log (trace_id);
CREATE INDEX IF NOT EXISTS idx_hermes_event_log_name
    ON hermes_event_log (event_name, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_hermes_event_log_ts
    ON hermes_event_log (timestamp DESC);
