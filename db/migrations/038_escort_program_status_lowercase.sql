-- Migration 038: Lowercase wbom_escort_programs.status vocabulary (Owner decision, 2026-07-10)
--
-- Context: knowledge_base/00_governance/CANONICAL_BUSINESS_RULES.md's
-- "Naming Rule -- escort program status" section documented significant,
-- unenforced casing/vocabulary drift ('Completed'/'completed', 'confirmed'
-- lowercase but 'Assigned'/'Running' capitalized) and recommended picking
-- one convention + a CHECK constraint, following escort_roster_change_
-- requests's existing pattern. The Owner has now decided: lowercase, with
-- this exact business meaning per state:
--   draft     -- order received, no escort assigned yet (INSERT default in
--                practice; already what modules/escort/__init__.py inserts)
--   confirmed -- an escort has been assigned to the program/vessel/lighter
--   running   -- the escort is actively on duty, before release/completion
--   completed -- the escort has been released / duty has ended
-- Two auxiliary values already live in real data / already read-filtered
-- elsewhere are kept (lowercased, not removed): 'cancelled', 'expired'.
--
-- Data backfill BEFORE adding the CHECK constraint, so no existing row can
-- violate it. Confirmed via audit immediately before writing this migration
-- (production distinct values): confirmed=175, Running=75, Completed=64,
-- expired=27, Assigned=1 (342 total). 'Assigned' maps to 'confirmed', not
-- 'draft' -- rows in this state already had an escort assigned via the
-- ESCORTCONFIRM command (modules/admin_commands/__init__.py::_cmd_escort_
-- confirm, which set status='Assigned' precisely at the assignment step;
-- that write-site is updated by this same change-set to write 'confirmed'
-- going forward).
--
-- The column's DEFAULT ('Assigned') is also corrected to 'draft' here --
-- application code has always explicitly inserted 'draft' at creation
-- (modules/escort/__init__.py), so the stale capitalized default was never
-- actually relied upon; this just brings the schema in line with reality.

UPDATE wbom_escort_programs SET status = 'confirmed' WHERE status = 'Assigned';
UPDATE wbom_escort_programs SET status = 'running'   WHERE status = 'Running';
UPDATE wbom_escort_programs SET status = 'completed' WHERE status = 'Completed';
UPDATE wbom_escort_programs SET status = 'cancelled' WHERE status = 'Cancelled';

ALTER TABLE wbom_escort_programs ALTER COLUMN status SET DEFAULT 'draft';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'wbom_escort_programs_status_check'
    ) THEN
        ALTER TABLE wbom_escort_programs
            ADD CONSTRAINT wbom_escort_programs_status_check
            CHECK (status IN ('draft', 'confirmed', 'running', 'completed', 'cancelled', 'expired'));
    END IF;
END $$;
