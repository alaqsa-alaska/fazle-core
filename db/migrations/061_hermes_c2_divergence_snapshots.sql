-- Migration 061: Hermes C2 — Divergence Snapshot Table (2026-08-07)
--
-- Records per-turn comparison results between the Hermes shadow decisions
-- and the ground-truth routing outcome observed in wbom_whatsapp_messages.
-- Append-only per Architectural Principles §12. No FK to any business table.
-- Rollback: DROP TABLE hermes_divergence_snapshots; — zero business impact.

CREATE TABLE IF NOT EXISTS hermes_divergence_snapshots (
    id                  BIGSERIAL    PRIMARY KEY,
    trace_id            UUID         NOT NULL,
    analysis_date       DATE         NOT NULL DEFAULT CURRENT_DATE,
    sender_phone        VARCHAR(30),
    shadow_role         VARCHAR(30),   -- identity_role from hermes_decision_audit_log
    shadow_intent       VARCHAR(50),   -- intent from hermes_decision_audit_log
    shadow_action       VARCHAR(20),   -- chosen_action from hermes_decision_audit_log
    shadow_explanation  TEXT,          -- routing branch label
    shadow_intent_method VARCHAR(20),  -- rule | llm | null
    shadow_provider     VARCHAR(50),   -- provider_used if populated
    wbom_role           VARCHAR(30),   -- identity_role from wbom_whatsapp_messages
    wbom_direction      VARCHAR(20),   -- inbound | outbound
    wbom_received_at    TIMESTAMPTZ,
    is_role_match       BOOLEAN,       -- shadow_role == wbom_role
    is_regression       BOOLEAN        NOT NULL DEFAULT false,
    regression_reason   TEXT,
    created_at          TIMESTAMPTZ    NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_hdivs_trace    ON hermes_divergence_snapshots (trace_id);
CREATE INDEX IF NOT EXISTS idx_hdivs_date     ON hermes_divergence_snapshots (analysis_date DESC);
CREATE INDEX IF NOT EXISTS idx_hdivs_regress  ON hermes_divergence_snapshots (is_regression, analysis_date DESC);
CREATE INDEX IF NOT EXISTS idx_hdivs_role     ON hermes_divergence_snapshots (shadow_role, analysis_date DESC);
