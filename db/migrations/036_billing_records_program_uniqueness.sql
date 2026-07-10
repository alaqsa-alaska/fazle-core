-- Migration 036: Backfill wbom_billing_records + one-bill-per-program (Phase 2B, Commit C)
--
-- Part A — governance backfill (discovered while building Commit C):
-- wbom_billing_records exists live in production but was never created by
-- ANY governed migration file (db/migrations/, modules/fazle_payroll_engine/
-- migrations/, or the legacy migrations/ tree) -- it is the "4th ungoverned
-- schema path via inline DDL" flagged in the System Consolidation Plan
-- (2026-07-08). CREATE TABLE IF NOT EXISTS below is a no-op in production
-- (table already exists there, verified via pg_dump --schema-only before
-- writing this) and backfills the table for the test harness / any future
-- fresh-environment rebuild, matching the exact live production schema.
--
-- Part B — Owner Decision Required #2 (docs/PHASE2B_READINESS_REPORT.md),
-- flagged during the original Phase 2B readiness audit and never
-- explicitly resolved by any later Owner decision: nothing prevented the
-- same program_id from being billed twice. Default applied here (disclosed,
-- not silently assumed): under normal operation an escort program is billed
-- exactly once on completion; no credit-note/multi-bill/re-bill workflow
-- has been designed or requested anywhere in this system. A UNIQUE
-- constraint is the safe, reversible default -- it can be dropped later if
-- the Owner decides multi-billing per program is required.
--
-- Safe to apply: wbom_billing_records has 0 rows in production (confirmed
-- by audit immediately before this migration was written), so there is no
-- possibility of an existing duplicate blocking the constraint.

CREATE TABLE IF NOT EXISTS wbom_billing_records (
    bill_id         SERIAL PRIMARY KEY,
    program_id      INTEGER NOT NULL,
    contact_id      INTEGER NOT NULL REFERENCES wbom_contacts(contact_id),
    bill_date       DATE NOT NULL,
    bill_number     VARCHAR(50) UNIQUE,
    service_charge  NUMERIC(10,2),
    other_charges   NUMERIC(10,2) DEFAULT 0,
    total_amount    NUMERIC(10,2),
    payment_status  VARCHAR(20) DEFAULT 'Pending',
    payment_date    DATE,
    remarks         TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT wbom_billing_records_program_id_fkey
        FOREIGN KEY (program_id) REFERENCES wbom_escort_programs(program_id)
);

CREATE INDEX IF NOT EXISTS idx_wbom_billing_contact ON wbom_billing_records (contact_id);
CREATE INDEX IF NOT EXISTS idx_wbom_billing_number ON wbom_billing_records (bill_number);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'wbom_billing_records_program_id_key'
    ) THEN
        ALTER TABLE wbom_billing_records
            ADD CONSTRAINT wbom_billing_records_program_id_key UNIQUE (program_id);
    END IF;
END $$;
