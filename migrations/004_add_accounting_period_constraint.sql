-- Migration: Add accounting_period format constraint to fpe_cash_transactions
-- Purpose: Enforce YYYY-MM format for accounting_period to prevent data corruption
-- Date: 2026-06-26

-- Add check constraint for accounting_period format on fpe_cash_transactions
ALTER TABLE fpe_cash_transactions
ADD CONSTRAINT check_cash_accounting_period_format 
  CHECK (accounting_period ~ '^\d{4}-\d{2}$');

-- Add check constraint for accounting_period format on fpe_income_transactions if it exists
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'fpe_income_transactions') THEN
    ALTER TABLE fpe_income_transactions
    ADD CONSTRAINT check_income_accounting_period_format 
      CHECK (accounting_period ~ '^\d{4}-\d{2}$');
  END IF;
END $$;

-- Verify constraints added
SELECT constraint_name, table_name, constraint_type
FROM information_schema.table_constraints
WHERE table_name IN ('fpe_cash_transactions', 'fpe_income_transactions')
  AND constraint_name LIKE 'check_%accounting_period%';
