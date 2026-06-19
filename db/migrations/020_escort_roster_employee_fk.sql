-- Migration 020: Add escort_employee_id FK to escort_roster_entries
-- Applied live: 2026-06-19 (VPS direct ALTER — column was missing, causing
--   asyncpg.exceptions.UndefinedColumnError that generated 29 MB of error logs)
-- Root cause: escort_roster_entries stored escort_name/escort_mobile as text
--   but had no FK back to wbom_employees; payroll and lifecycle modules expected
--   the FK to exist.

ALTER TABLE escort_roster_entries
    ADD COLUMN IF NOT EXISTS escort_employee_id INTEGER
        REFERENCES wbom_employees(employee_id)
        ON DELETE SET NULL ON UPDATE CASCADE;

CREATE INDEX IF NOT EXISTS idx_ere_escort_employee
    ON escort_roster_entries(escort_employee_id);

-- Backfill from wbom_escort_programs for rows where the program had a linked employee.
-- 136 of 140 rows updated; 4 remain NULL (escorts identified by name/mobile only).
UPDATE escort_roster_entries r
SET escort_employee_id = p.escort_employee_id
FROM wbom_escort_programs p
WHERE r.program_id = p.program_id
  AND p.escort_employee_id IS NOT NULL;
