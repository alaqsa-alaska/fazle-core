-- Migration C0D-003: Add missing indexes to fpe_cash_transactions
-- Sprint-C0D: Migration Repair
-- Purpose: Document indexes that exist in production but not in tracked migrations.
-- These indexes already exist in production. This migration is IDEMPOTENT.
--
-- Indexes added:
--   idx_fpe_txn_emp_name_raw_trgm  — GIN trigram index on lower(employee_name_raw) for fuzzy search
--   idx_fpe_txn_raw_collapsed_trgm — GIN trigram index on collapsed employee_name_raw for fuzzy search
--   idx_fpe_txn_phone              — B-tree index on payout_phone for phone lookups
--
-- Requires: pg_trgm extension
--
-- Rollback:
--   DROP INDEX IF EXISTS idx_fpe_txn_emp_name_raw_trgm;
--   DROP INDEX IF EXISTS idx_fpe_txn_raw_collapsed_trgm;
--   DROP INDEX IF EXISTS idx_fpe_txn_phone;

-- Ensure pg_trgm extension exists
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Trigram index for fuzzy name search (idempotent)
CREATE INDEX IF NOT EXISTS idx_fpe_txn_emp_name_raw_trgm
    ON fpe_cash_transactions USING gin (lower(employee_name_raw) gin_trgm_ops);

-- Trigram index for collapsed name search (idempotent)
CREATE INDEX IF NOT EXISTS idx_fpe_txn_raw_collapsed_trgm
    ON fpe_cash_transactions
    USING gin (regexp_replace(lower(COALESCE(employee_name_raw, '')), '[^a-z0-9]+', '', 'g') gin_trgm_ops);

-- Phone lookup index (idempotent)
CREATE INDEX IF NOT EXISTS idx_fpe_txn_phone
    ON fpe_cash_transactions USING btree (payout_phone);