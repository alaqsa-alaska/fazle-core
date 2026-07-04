-- Migration C0D-004: Ensure fpe_income_transactions CHECK constraint
-- Sprint-C0D: Migration Repair
-- Purpose: Document the CHECK constraint that exists in production.
-- Constraint: amount > 0 (income transactions must have positive amounts)
--
-- This migration is IDEMPOTENT — drops and recreates the constraint.
--
-- Rollback:
--   ALTER TABLE fpe_income_transactions DROP CONSTRAINT IF EXISTS fpe_income_transactions_amount_check;

ALTER TABLE fpe_income_transactions
    DROP CONSTRAINT IF EXISTS fpe_income_transactions_amount_check;

ALTER TABLE fpe_income_transactions
    ADD CONSTRAINT fpe_income_transactions_amount_check CHECK (amount > 0);