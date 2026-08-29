-- Migration 077: wbom_candidates.linked_employee_id
--
-- Owner directive (2026-08-29, "Final recruitment lifecycle closure",
-- GAP 1): current candidate->employee continuity is derived purely from
-- the canonical phone (wbom_candidates.phone == wbom_employees.
-- employee_mobile). That is useful but breaks if the person's phone
-- changes after joining. This adds the smallest durable link: one
-- nullable FK on the candidate row pointing at the authoritative
-- employee record.
--
-- Verification that no equivalent relationship already exists (2026-08-29
-- schema trace of production `postgres` DB):
--   wbom_candidates            -- 22 columns, no employee reference
--   wbom_candidate_conversations -- append-only audit, no employee ref
--   fazle_recruitment_sessions -- FK to wbom_candidates only
--   wbom_recruitment_reminders -- FK to wbom_candidates only
--   fpe_employees.wbom_employee_id -- links FPE<->WBOM employee, NOT candidate
-- So this is a genuine gap, not a naming mismatch.
--
-- Semantics:
--   NULL                -- candidate has not been converted to an employee
--   <employee_id>       -- this candidate became this employee, linked once
--
-- The partial UNIQUE index enforces 1:1 -- a given employee_id can be the
-- conversion target of at most one candidate row. Application code
-- (modules.recruitment_flow.link_candidate_to_employee) only ever writes
-- this column WHERE linked_employee_id IS NULL and never silently
-- repoints an already-linked candidate; the unique index is the DB-level
-- backstop for that rule, matching the "DB-level enforcement of any new
-- state/approval-style column" pattern migrations 059/072 established.
--
-- ON DELETE SET NULL: if an employee row is ever hard-deleted the
-- candidate/recruitment history is preserved (the link just goes NULL),
-- consistent with fazle_payment_drafts / escort_roster_entries FKs to the
-- same table.
--
-- Purely additive: one nullable column + one partial index. No ALTER on
-- any other table, no data rewrite, no CHECK on an existing column.
-- The `joined` business lifecycle fact is recorded as an event row in
-- wbom_candidate_conversations (step='joined'), NOT as a new
-- post_intake_status value -- the migration-072 status vocabulary stays
-- frozen exactly as the Owner locked it.

BEGIN;

ALTER TABLE wbom_candidates
    ADD COLUMN IF NOT EXISTS linked_employee_id BIGINT
        REFERENCES wbom_employees(employee_id) ON DELETE SET NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_wbom_candidates_linked_employee
    ON wbom_candidates (linked_employee_id)
    WHERE linked_employee_id IS NOT NULL;

COMMIT;

-- Downgrade (manual, run only after confirming no code path reads
-- wbom_candidates.linked_employee_id):
--   BEGIN;
--   DROP INDEX IF EXISTS uq_wbom_candidates_linked_employee;
--   ALTER TABLE wbom_candidates DROP COLUMN IF EXISTS linked_employee_id;
--   COMMIT;
