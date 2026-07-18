-- Migration 050: Dispatch Phase 2A.3 -- escort-replacement segment linkage
--
-- Owner-approved (2026-07-19). Adds a single nullable self-referential FK
-- so a later replacement escort-duty segment can point back to the earlier
-- segment it replaced for the same vessel operation:
--
--   new_program.replaces_program_id = earlier_program.program_id
--
-- Confirmed (Dispatch Phase 2A.3 Decision Report) that no existing column
-- on wbom_escort_programs serves this purpose -- this is a genuinely new
-- relationship, not an overload of an existing field.
--
-- Purely additive: nullable, no default change, no existing row rewritten.
-- Zero rows will have replaces_program_id set immediately after this
-- applies -- application code (Stage 3) is what starts writing it.
--
-- Self-replacement (program_id = replaces_program_id) is rejected at the
-- database level as defense-in-depth; application code (modules.dispatch)
-- also validates this before ever constructing the INSERT. Full cycle
-- prevention (A replaces B replaces ... replaces A) is not practical to
-- express as a table-level CHECK/index in Postgres and is instead enforced
-- at the application layer: replacement links are only ever created one at
-- a time, inside a transaction that freshly re-reads the target row's
-- current replacement state before writing (see
-- modules.dispatch.create_replacement_segment()).
--
-- Uniqueness: at most one currently-active (non-terminal) row may claim to
-- replace the same earlier program at any moment -- multiple *historical*
-- (terminal) replacement attempts pointing at the same predecessor are
-- allowed to coexist (e.g. a replacement that was itself later changed
-- again), matching the Owner instruction not to prevent valid historical
-- coexistence. Enforced via a partial unique index, not a plain UNIQUE
-- constraint, so terminal rows are excluded from the uniqueness check
-- entirely.

BEGIN;

ALTER TABLE wbom_escort_programs
    ADD COLUMN replaces_program_id INTEGER NULL
    REFERENCES wbom_escort_programs(program_id);

ALTER TABLE wbom_escort_programs
    ADD CONSTRAINT wbom_escort_programs_no_self_replacement
    CHECK (replaces_program_id IS NULL OR replaces_program_id <> program_id);

CREATE INDEX idx_wbom_escort_programs_replaces
    ON wbom_escort_programs (replaces_program_id)
    WHERE replaces_program_id IS NOT NULL;

-- At most one non-terminal (still-active) replacement per predecessor.
CREATE UNIQUE INDEX idx_wbom_escort_programs_replaces_active_unique
    ON wbom_escort_programs (replaces_program_id)
    WHERE replaces_program_id IS NOT NULL
      AND status NOT IN ('completed', 'cancelled', 'expired', 'escort_changed');

COMMIT;

-- Downgrade (manual, run only after confirming replaces_program_id is
-- fully cleared / no approved replacement links exist that must be
-- preserved -- clear the column contents first, then):
--   BEGIN;
--   DROP INDEX IF EXISTS idx_wbom_escort_programs_replaces_active_unique;
--   DROP INDEX IF EXISTS idx_wbom_escort_programs_replaces;
--   ALTER TABLE wbom_escort_programs DROP CONSTRAINT IF EXISTS wbom_escort_programs_no_self_replacement;
--   ALTER TABLE wbom_escort_programs DROP COLUMN IF EXISTS replaces_program_id;
--   COMMIT;
