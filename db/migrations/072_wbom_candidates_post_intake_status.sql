-- Migration 072: wbom_candidates.post_intake_status
--
-- Owner directive (2026-08-27, "Reuse existing architecture" implementation
-- pass, item B): a scored candidate (funnel_stage='scored') currently has
-- no field anywhere representing what happens AFTER intake -- Phase A
-- verification (2026-08-26/27) confirmed no existing column overlaps this
-- concept: wbom_candidates.available_join_date exists but is never written
-- by any live code path, and wbom_job_applications/wbom_recruitment_
-- reminders are both FK-linked but have 0 live rows. This is a genuine gap,
-- not a naming mismatch -- see the Phase A verification report.
--
-- wbom_candidates (not fazle_recruitment_sessions) is the canonical
-- candidate-level record per that same verification -- it survives session
-- expiry/re-contact and is the row every read site outside the live intake
-- conversation already queries (admin reports, AI-console tools).
--
-- Values (Owner-specified, kept to exactly this set -- no redundant states):
--   awaiting_agreement -- set automatically the moment intake completes
--   agreed              -- candidate accepted training/probation conditions
--   joining_ready        -- candidate is ready to join (documents/logistics)
--   salary_objection      -- candidate objected to training salary/allowance
--                            but has not declined outright
--   not_ready_now         -- candidate is interested but not ready yet
--   declined              -- candidate explicitly does not want the job
--   future_contact        -- candidate asked to be contacted later
--
-- NULL means "not yet reached this stage" (pre-scored, or a candidate
-- scored before this migration ran -- see the backfill note below).
-- CHECK constraint follows the same DB-level-enforcement pattern migration
-- 059 (fazle_admin_directives.category/status) already established as "the
-- model to follow for any new approval/state-style column" per
-- CANONICAL_BUSINESS_RULES.md's Approval section.
--
-- Purely additive: no ALTER on any other table, no new FK, no data rewrite
-- of any existing column. Cannot fail due to another table's state.

ALTER TABLE wbom_candidates
    ADD COLUMN IF NOT EXISTS post_intake_status TEXT
        CHECK (post_intake_status IS NULL OR post_intake_status IN (
            'awaiting_agreement', 'agreed', 'joining_ready',
            'salary_objection', 'not_ready_now', 'declined', 'future_contact'
        ));

-- Index for the read site added in this same pass
-- (modules.recruitment_flow.get_active_session()'s scored-candidate branch,
-- and modules.hermes_dispatch's post-intake session-state block) -- both
-- look this column up by candidate_id, already the table's primary key, so
-- no separate index is needed for that lookup. This index instead supports
-- an eventual admin/report query ("list candidates currently
-- awaiting_agreement") without a full-table scan; harmless if never used.
CREATE INDEX IF NOT EXISTS idx_wbom_candidates_post_intake_status
    ON wbom_candidates (post_intake_status) WHERE post_intake_status IS NOT NULL;

-- No backfill: candidates already at funnel_stage='scored' before this
-- migration simply have post_intake_status=NULL until their next inbound
-- message re-enters the post-intake path, at which point
-- modules.recruitment_flow.get_active_session() treats NULL the same as
-- 'awaiting_agreement' (see that function's own comment) -- so no existing
-- scored candidate is silently dropped or requires a manual data fix.
