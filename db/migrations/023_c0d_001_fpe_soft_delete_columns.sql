-- Migration C0D-001: Add soft-delete columns to fpe_cash_transactions
-- Sprint-C0D: Migration Repair
-- Purpose: Document the 3 phantom columns that exist in production but not in tracked migrations.
-- These columns already exist in production (added manually without tracked migration).
-- This migration is IDEMPOTENT — safe to run on production (columns already exist).
--
-- Columns added:
--   deleted_at TIMESTAMPTZ — used by admin_transactions.soft_delete_transaction()
--   deleted_by TEXT         — used by admin_transactions.soft_delete_transaction()
--   updated_at TIMESTAMPTZ DEFAULT NOW() — used by optimistic locking (X-If-Match-Updated-At)
--
-- Production evidence: 33 rows have deleted_at/deleted_by, 2491 rows have updated_at.
--
-- Rollback:
--   ALTER TABLE fpe_cash_transactions DROP COLUMN IF EXISTS deleted_at, DROP COLUMN IF EXISTS deleted_by, DROP COLUMN IF EXISTS updated_at;
--   DROP TRIGGER IF EXISTS fpe_txn_updated_at_trig ON fpe_cash_transactions;
--   DROP FUNCTION IF EXISTS fpe_txn_set_updated_at();

-- 1. Add columns (idempotent)
ALTER TABLE fpe_cash_transactions
    ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS deleted_by TEXT,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW();

-- 2. Create trigger function for auto-updating updated_at on UPDATE
CREATE OR REPLACE FUNCTION fpe_txn_set_updated_at()
    RETURNS trigger LANGUAGE plpgsql AS
    $function$ BEGIN NEW.updated_at = now(); RETURN NEW; END; $function$;

-- 3. Create trigger (idempotent — drop if exists first)
DROP TRIGGER IF EXISTS fpe_txn_updated_at_trig ON fpe_cash_transactions;
CREATE TRIGGER fpe_txn_updated_at_trig
    BEFORE UPDATE ON fpe_cash_transactions
    FOR EACH ROW EXECUTE FUNCTION fpe_txn_set_updated_at();

-- 4. Create index for filtering non-deleted transactions (idempotent)
CREATE INDEX IF NOT EXISTS idx_fpe_txn_not_deleted
    ON fpe_cash_transactions USING btree (id) WHERE (deleted_at IS NULL);