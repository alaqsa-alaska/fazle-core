-- Migration 049: Dispatch Phase 2A.3 -- add 'escort_changed' lifecycle status
--
-- Owner-approved (2026-07-19). Widens the existing status CHECK constraint
-- on wbom_escort_programs to allow one new value: 'escort_changed'.
--
-- Meaning: the earlier escort duty segment for a vessel operation was
-- replaced by a later escort before normal completion (Bangla label,
-- application-side only: এস্কর্ট চেঞ্জ). This is a lifecycle status, not a
-- correction marker -- it means "this segment ended because a different
-- escort took over," distinct from 'completed' (ended normally) and
-- 'cancelled' (never happened / voided).
--
-- Purely additive: no default change, no existing row is rewritten by this
-- migration. Zero rows will have status='escort_changed' immediately after
-- this applies -- application code (Dispatch Phase 2A.3, deployed
-- separately in Stage 2/3) is what starts writing the new value.
--
-- Every application-side reader that currently excludes 'completed'/
-- 'cancelled' from "active/blocking" queries has been separately updated
-- (see knowledge_base/00_governance/management_decisions.md -> "Dispatch
-- Phase 2A.3 Stage 1") to also exclude 'escort_changed' -- it must never be
-- treated as bookability-blocking, attendance-eligible, or a valid release
-- target, but must remain visible in history/audit exactly like
-- 'completed'/'cancelled' already are.
--
-- Rollback: safe only while zero rows carry status='escort_changed' (an
-- ALTER ... CHECK re-narrowing the allowed set will fail against any
-- existing row using the value being removed). See migration 049 rollback
-- note in management_decisions.md.

BEGIN;

ALTER TABLE wbom_escort_programs
    DROP CONSTRAINT wbom_escort_programs_status_check;

ALTER TABLE wbom_escort_programs
    ADD CONSTRAINT wbom_escort_programs_status_check
    CHECK (status IN (
        'draft', 'confirmed', 'running', 'completed', 'cancelled', 'expired',
        'escort_changed'
    ));

COMMIT;

-- Downgrade (manual, run only after confirming zero escort_changed rows):
--   BEGIN;
--   ALTER TABLE wbom_escort_programs DROP CONSTRAINT wbom_escort_programs_status_check;
--   ALTER TABLE wbom_escort_programs ADD CONSTRAINT wbom_escort_programs_status_check
--       CHECK (status IN ('draft','confirmed','running','completed','cancelled','expired'));
--   COMMIT;
