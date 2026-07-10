-- Migration 030: Reconcile documented schema drift on fazle_payment_drafts / fazle_draft_replies
--
-- Context: Production Certification (Phase 1.5, 2026-07-09) ran a full
-- information_schema diff between live production and the tracked migration
-- set (db/migrations/ + modules/fazle_payroll_engine/migrations/) and found
-- these columns live in production, read/written by production code
-- (modules/payment_workflow, modules/admin_commands), with no corresponding
-- migration anywhere in the repo. They were most likely added via an
-- untracked manual ALTER TABLE at some point. This migration only documents
-- and reproduces that already-live state — it does not change production
-- behavior (all columns already exist there) and does not add new features.
--
-- This is a narrow, evidence-verified fix for the specific columns found to
-- block the test suite. The certification report documents a much larger
-- set of undocumented prod-vs-migration drift (~150 tables, ~88 columns)
-- that is out of scope for this migration and is queued separately.

ALTER TABLE fazle_payment_drafts
    ADD COLUMN IF NOT EXISTS approved_at TIMESTAMPTZ;

ALTER TABLE fazle_draft_replies
    ADD COLUMN IF NOT EXISTS sent_at    TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS error_text TEXT,
    ADD COLUMN IF NOT EXISTS edited_at  TIMESTAMPTZ;

-- modules/payment_ingest/fpe_bridge.py's INSERT ... ON CONFLICT (primary_phone)
-- WHERE primary_phone IS NOT NULL requires this exact partial unique index.
-- It exists in production (confirmed via pg_indexes) with no migration record.
CREATE UNIQUE INDEX IF NOT EXISTS idx_fpe_emp_phone_unique
    ON fpe_employees (primary_phone) WHERE (primary_phone IS NOT NULL);
