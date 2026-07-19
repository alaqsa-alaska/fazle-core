-- Migration 053: Phase B — Conversation Memory Engine (Stage 1)
--
-- Reuses wbom_whatsapp_messages as the raw message-event source (Part 5.1
-- of the Phase B spec) -- it already has whatsapp_msg_id, direction,
-- sender_number, received_at, identity_role, metadata_json,
-- related_program_id. No new message-event table. system_generated is
-- computed on read via the already-deployed modules.bridge_poller.
-- _is_system_generated_notification() (Root Cause 7 fix, commit
-- 10ee387/0dc2e78), not stored -- it's a pure function of text + the
-- existing _AUTOMATED_SUFFIX_ANCHOR marker; storing it would just be a
-- second source of truth to drift.
--
-- One new state table, not five (the spec's own suggested list) --
-- accumulated fields stored as structured JSONB (value/source_message_id/
-- confidence/extraction_method per field, matching the spec's Part 4.6
-- example exactly) rather than a separate per-field table, matching the
-- JSONB-structured-field convention already used in this codebase
-- (wbom_unmatched_confirmations.candidate_matches/parsed_confirmation,
-- migration 052).
--
-- Idempotency dedup table follows the exact existing pattern already used
-- twice (processed_bridge_messages, processed_outgoing_escort_messages,
-- both modules/bridge_poller/__init__.py) rather than a new mechanism.
--
-- Scope decision (Owner-approved 2026-07-19): coexists with, does not
-- replace, fazle_recruitment_sessions/intake_message() -- that mechanism
-- already works for recruitment and is unrelated to the problem that
-- motivated this migration. This table is used by escort (and any future
-- domain that opts in) only, for now.

CREATE TABLE IF NOT EXISTS fazle_conversation_threads (
    id                    BIGSERIAL PRIMARY KEY,

    platform              VARCHAR(20) NOT NULL,   -- whatsapp, meta, messenger
    bridge_id             VARCHAR(30) NOT NULL,   -- bridge1, bridge2, meta
    counterparty_phone    VARCHAR(30) NOT NULL,   -- normalized
    sender_role_at_open   VARCHAR(30),            -- identity_role snapshot at first message

    business_domain       VARCHAR(30) NOT NULL,   -- escort | recruitment | general
    business_intent       VARCHAR(50),

    state                 VARCHAR(30) NOT NULL DEFAULT 'open',
    -- open | partial | complete | draft_created | awaiting_confirmation |
    -- conflicted | expired | resolved | abandoned

    fields                JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- {"mother_vessel": {"value": "MV Horda", "source_message_id": "...",
    --   "confidence": 0.94, "extraction_method": "bangla_label"}, ...}

    first_message_id      VARCHAR(100),
    first_message_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_message_id       VARCHAR(100),
    last_message_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at            TIMESTAMPTZ NOT NULL,

    last_system_action    VARCHAR(50),

    related_object_type   VARCHAR(30),  -- e.g. 'wbom_escort_programs', 'wbom_unmatched_confirmations'
    related_object_id     INTEGER,

    conflict_flags        JSONB NOT NULL DEFAULT '[]'::jsonb,
    ambiguity_reason      TEXT,

    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT fazle_conversation_threads_state_check
        CHECK (state IN (
            'open', 'partial', 'complete', 'draft_created',
            'awaiting_confirmation', 'conflicted', 'expired',
            'resolved', 'abandoned'
        ))
);

CREATE INDEX IF NOT EXISTS idx_fazle_conversation_threads_bridge_counterparty
    ON fazle_conversation_threads (bridge_id, counterparty_phone);
CREATE INDEX IF NOT EXISTS idx_fazle_conversation_threads_open_state
    ON fazle_conversation_threads (state)
    WHERE state IN ('open', 'partial', 'conflicted');
CREATE INDEX IF NOT EXISTS idx_fazle_conversation_threads_expires_at
    ON fazle_conversation_threads (expires_at)
    WHERE state IN ('open', 'partial');
CREATE INDEX IF NOT EXISTS idx_fazle_conversation_threads_related_object
    ON fazle_conversation_threads (related_object_type, related_object_id)
    WHERE related_object_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_fazle_conversation_threads_domain_state
    ON fazle_conversation_threads (business_domain, state);

-- Idempotency: which raw messages have already been folded into a
-- thread's fields, per (message_id, bridge) -- same shape as
-- processed_bridge_messages / processed_outgoing_escort_messages.
CREATE TABLE IF NOT EXISTS processed_conversation_messages (
    message_id   TEXT NOT NULL,
    bridge       TEXT NOT NULL,
    thread_id    BIGINT REFERENCES fazle_conversation_threads(id),
    processed_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (message_id, bridge)
);
