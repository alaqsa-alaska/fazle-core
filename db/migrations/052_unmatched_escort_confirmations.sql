-- Migration 052: wbom_unmatched_confirmations (Draft-Confirmation Invariant, Workstream 2)
--
-- Enforces: one escort order draft -> one canonical wbom_escort_programs row ->
-- later admin confirmation updates that same row. Today, when
-- handle_admin_escort_completion()'s fuzzy match (_find_pending_program(),
-- modules/escort/__init__.py) fails to find a confident draft, it silently
-- calls _create_confirmed_from_admin() and inserts a second, unlinked
-- confirmed program row (with only a post-hoc same-lighter-vessel cleanup
-- pass as a safety net). This table replaces that silent auto-create with a
-- durable admin-review queue: no-match confirmations land here instead,
-- and a human either links the existing draft, confirms it really is a
-- separate duty segment, or rejects/defers.
--
-- Dedup identity: this repo already has two independent dedup tables for
-- admin-confirmation ingestion -- processed_bridge_messages (inbound
-- webhook/poller race guard, modules/bridge_poller/__init__.py) and
-- processed_outgoing_escort_messages (the bridge-poller's own outgoing-scan
-- guard, same file) -- but they guard two *different* ingestion paths
-- independently, so the same physical admin confirmation can still reach
-- handle_admin_escort_completion() twice across paths (once via the webhook
-- route, once via the SQLite outgoing scan). The partial unique index below
-- closes that specific cross-path gap for queue admission. source_bridge/
-- source_message_id carry the same message-id identity already used by
-- those tables (event.get("id") on the webhook path, omsg["id"] on the
-- poller path) -- no new identifier invented.
--
-- Retention: deliberately no TTL/hard-delete here, unlike escort drafts'
-- 24h DRAFT_TTL_HOURS sweep (modules/escort_roster/db.py::
-- expire_stale_drafts()) -- an unmatched admin confirmation is authoritative
-- operational evidence, not a speculative draft. Any future archive policy
-- is a separate Owner decision.

CREATE TABLE IF NOT EXISTS wbom_unmatched_confirmations (
    id                      SERIAL PRIMARY KEY,

    source_bridge           VARCHAR(30),
    source_message_id       VARCHAR(100),
    source_chat_jid         VARCHAR(120),
    source                  VARCHAR(30) NOT NULL,

    admin_phone             VARCHAR(20),
    admin_jid               VARCHAR(120),
    client_phone            VARCHAR(20),

    raw_confirmation_text   TEXT NOT NULL,
    parsed_confirmation     JSONB NOT NULL,
    candidate_matches       JSONB NOT NULL DEFAULT '[]'::jsonb,

    best_candidate_score    NUMERIC(5,2),

    status                  VARCHAR(30) NOT NULL DEFAULT 'pending',
    resolved_program_id     INTEGER
                            REFERENCES wbom_escort_programs(program_id),

    review_reason           TEXT,
    reviewed_by             VARCHAR(100),
    reviewed_at             TIMESTAMPTZ,

    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT wbom_unmatched_confirmations_status_check
      CHECK (
        status IN (
          'pending',
          'linked_existing',
          'confirmed_new',
          'rejected',
          'deferred'
        )
      )
);

-- Same partial-unique-on-nullable-message-id pattern already used for
-- wbom_cash_transactions' own whatsapp-message dedup index
-- (idx_wbom_transactions_wa_msg_dedup, migration 029) -- not a new pattern.
CREATE UNIQUE INDEX IF NOT EXISTS
    uq_wbom_unmatched_confirmations_source_message
    ON wbom_unmatched_confirmations (source_bridge, source_message_id)
    WHERE source_message_id IS NOT NULL AND source_message_id <> '';

CREATE INDEX IF NOT EXISTS idx_wbom_unmatched_confirmations_status
    ON wbom_unmatched_confirmations (status);

CREATE INDEX IF NOT EXISTS idx_wbom_unmatched_confirmations_created_at
    ON wbom_unmatched_confirmations (created_at DESC);
