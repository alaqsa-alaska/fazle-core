-- ==========================================================================
-- Migration 014: Income soft-delete metadata
-- ==========================================================================
-- Goal:
--   Add reversible, auditable soft-delete metadata to fpe_income_transactions.
--
-- Notes:
--   - Non-destructive schema update (no hard delete).
--   - Listing APIs should filter deleted_at IS NULL.

ALTER TABLE fpe_income_transactions
    ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS deleted_by TEXT;

CREATE INDEX IF NOT EXISTS idx_fpe_income_not_deleted
    ON fpe_income_transactions(created_at DESC)
    WHERE deleted_at IS NULL;
