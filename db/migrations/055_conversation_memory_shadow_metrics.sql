-- Migration 055: Phase B Stage 4 — shadow observation metrics support.
--
-- Adds processing_latency_ms to fazle_conversation_shadow_comparisons
-- (Section 6's "shadow exceptions and processing latency" requirement)
-- and widens grouping_action's practical value set to include exclusion/
-- replay events ('unauthorized_excluded', 'system_generated_excluded',
-- 'duplicate_replay') alongside the existing grouping decisions
-- ('merge_existing', 'new_thread', 'needs_review') -- no CHECK
-- constraint exists on that column (VARCHAR(30) free text), so this is
-- purely an application-level convention change, not a schema change,
-- but documented here since it's part of the same Stage 4 metrics work.

ALTER TABLE fazle_conversation_shadow_comparisons
    ADD COLUMN IF NOT EXISTS processing_latency_ms INTEGER;

CREATE INDEX IF NOT EXISTS idx_conv_shadow_cmp_grouping_action
    ON fazle_conversation_shadow_comparisons(grouping_action);
