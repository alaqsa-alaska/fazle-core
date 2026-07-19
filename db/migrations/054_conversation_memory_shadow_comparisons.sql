-- Migration 054: Phase B — Stage 2 shadow comparison records.
--
-- One row per authorized escort-client message observed while
-- MULTI_MESSAGE_ESCORT_PARSER_ENABLED is on (Part 9 of the Stage 2 spec).
-- Records the current single-message-parser result side by side with the
-- new conversation-aware (multi-message, grouping-aware) result, plus
-- provenance/completion/conflict/grouping metadata -- structured data
-- only, no free-text reasoning. Aggregate metrics (Section 9's required
-- counts: conversations observed, partial threads, completed candidates,
-- conflicts, duplicates, unauthorized-excluded, parser-missed-candidate,
-- parser-would-have-merged-incorrectly) are computed on demand via GROUP
-- BY over this table rather than a separate running-counters table, so
-- they're always exact and never drift from the underlying rows.
--
-- Never written to by anything that creates a business row -- this table
-- has no FK from wbom_escort_programs and nothing reads it to make a
-- production decision. thread_id is nullable because a "needs_review"
-- (Case C, ambiguous) observation may not resolve to any single thread.

CREATE TABLE IF NOT EXISTS fazle_conversation_shadow_comparisons (
    id                              BIGSERIAL PRIMARY KEY,

    thread_id                       BIGINT REFERENCES fazle_conversation_threads(id),
    bridge_id                       VARCHAR(30) NOT NULL,
    counterparty_phone              VARCHAR(30) NOT NULL,
    business_domain                 VARCHAR(30) NOT NULL,
    message_id                      VARCHAR(100),

    current_parser_result           JSONB NOT NULL DEFAULT '{}'::jsonb,
    conversation_aware_result       JSONB NOT NULL DEFAULT '{}'::jsonb,
    field_provenance                JSONB NOT NULL DEFAULT '{}'::jsonb,

    completion_state                VARCHAR(30) NOT NULL,
    grouping_action                 VARCHAR(30) NOT NULL,   -- merge_existing | new_thread | needs_review
    grouping_reason                 VARCHAR(60) NOT NULL,

    conflicts                       JSONB NOT NULL DEFAULT '[]'::jsonb,
    would_create_draft              BOOLEAN NOT NULL DEFAULT FALSE,
    reason                          VARCHAR(80) NOT NULL,

    existing_draft_relation         VARCHAR(30),   -- no_candidate | one_likely | several_ambiguous | likely_duplicate | likely_correction
    existing_draft_relation_detail  JSONB,

    parser_missed_candidate         BOOLEAN NOT NULL DEFAULT FALSE,
    parser_would_have_merged_incorrectly BOOLEAN NOT NULL DEFAULT FALSE,

    created_at                      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_conv_shadow_cmp_thread ON fazle_conversation_shadow_comparisons(thread_id);
CREATE INDEX IF NOT EXISTS idx_conv_shadow_cmp_created ON fazle_conversation_shadow_comparisons(created_at);
CREATE INDEX IF NOT EXISTS idx_conv_shadow_cmp_domain ON fazle_conversation_shadow_comparisons(business_domain);
