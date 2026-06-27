-- Migration: Add indexes to FPE tables for performance optimization
-- Purpose: Optimize frequently-searched columns on fpe_employees table
-- Date: 2026-06-26

-- Add index on employee_code for fast lookups
CREATE INDEX IF NOT EXISTS idx_fpe_employees_code 
  ON fpe_employees(employee_code);

-- Add index on primary_phone for fast lookups during income/cash entry
CREATE INDEX IF NOT EXISTS idx_fpe_employees_phone 
  ON fpe_employees(primary_phone);

-- Add index on fpe_income_transactions for fast lookups by date
CREATE INDEX IF NOT EXISTS idx_fpe_income_txn_date 
  ON fpe_income_transactions(txn_date);

-- Add index on fpe_cash_transactions for fast lookups by date
CREATE INDEX IF NOT EXISTS idx_fpe_cash_txn_date 
  ON fpe_cash_transactions(txn_date);

-- Verify indexes created
SELECT indexname, tablename 
FROM pg_indexes 
WHERE tablename IN ('fpe_employees', 'fpe_income_transactions', 'fpe_cash_transactions')
  AND indexname LIKE 'idx_fpe%'
ORDER BY tablename, indexname;
