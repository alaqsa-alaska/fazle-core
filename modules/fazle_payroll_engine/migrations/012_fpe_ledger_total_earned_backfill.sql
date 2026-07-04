-- ============================================================================
-- Migration 012: Backfill ledger placeholder earnings
-- ============================================================================
-- Context:
--   Duty-day based earnings fields are not yet available in schema.
--   Until that model is implemented, ledger uses a safe placeholder:
--     total_earned = total_paid (for historical rows)
--
-- This migration is additive and idempotent.

-- Backfill placeholder earned value for rows that still show zero.
UPDATE fpe_employee_ledger
SET total_earned = total_paid
WHERE total_earned = 0;

-- Recompute closing balance from current ledger columns.
UPDATE fpe_employee_ledger
SET closing_balance = opening_balance + total_earned - total_paid - total_advance,
    last_updated = NOW();
