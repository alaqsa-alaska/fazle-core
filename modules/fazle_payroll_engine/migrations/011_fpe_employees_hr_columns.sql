-- ============================================================================
-- Migration 011: fpe_employees — HR profile columns + WBOM backfill
-- ============================================================================
-- PHILOSOPHY: Additive only. No columns dropped, no renames, no data deleted.
-- Goal: fpe_employees becomes the single canonical employee table that also
--       carries HR profile data (basic_salary, designation, joining_date).
--
-- Steps:
--   1. Add HR columns to fpe_employees
--   2. Backfill HR data from wbom_employees where wbom_employee_id link exists
--   3. Create fpe_employees rows for WBOM employees that have no FPE record
--      (primarily legacy Escort employees created before FPE existed)
--   4. For WBOM employees whose phone already matches an fpe_employees row,
--      just set wbom_employee_id + copy HR data
-- ============================================================================

-- ── 1. Add HR columns ────────────────────────────────────────────────────────
ALTER TABLE fpe_employees
    ADD COLUMN IF NOT EXISTS basic_salary  NUMERIC(12,2) NOT NULL DEFAULT 0;

ALTER TABLE fpe_employees
    ADD COLUMN IF NOT EXISTS designation   TEXT;

ALTER TABLE fpe_employees
    ADD COLUMN IF NOT EXISTS joining_date  DATE;

-- ── 2. Backfill HR data where wbom_employee_id is already set ────────────────
UPDATE fpe_employees fe
SET
    basic_salary  = COALESCE(we.basic_salary, 0),
    designation   = COALESCE(we.designation, fe.designation),
    joining_date  = COALESCE(we.joining_date, fe.joining_date)
FROM wbom_employees we
WHERE fe.wbom_employee_id = we.employee_id;

-- ── 3. For WBOM employees whose phone matches an fpe_employees row:
--       set wbom_employee_id + copy HR data (phone must be a real 11-digit BD number)
UPDATE fpe_employees fe
SET
    wbom_employee_id = we.employee_id,
    basic_salary     = COALESCE(we.basic_salary, 0),
    designation      = COALESCE(we.designation, fe.designation),
    joining_date     = COALESCE(we.joining_date, fe.joining_date)
FROM wbom_employees we
WHERE fe.wbom_employee_id IS NULL
  AND RIGHT(REGEXP_REPLACE(COALESCE(fe.primary_phone, ''), '\D', '', 'g'), 10)
      = RIGHT(REGEXP_REPLACE(we.employee_mobile, '\D', '', 'g'), 10)
  AND RIGHT(REGEXP_REPLACE(we.employee_mobile, '\D', '', 'g'), 10) <> ''
  AND LENGTH(RIGHT(REGEXP_REPLACE(we.employee_mobile, '\D', '', 'g'), 10)) = 10;

-- ── 4. Create fpe_employees rows for WBOM employees with no FPE record at all
--       (77 WBOM Escort employees confirmed by 2026-07-02 audit)
INSERT INTO fpe_employees
    (full_name, name_normalized, primary_phone, employee_id_phone,
     department, status, created_source, resolution_status, confidence_score,
     wbom_employee_id, basic_salary, designation, joining_date)
SELECT
    we.employee_name,
    LOWER(TRIM(REGEXP_REPLACE(we.employee_name, '\s+', ' ', 'g'))),
    we.employee_mobile,
    we.employee_mobile,
    we.designation,
    CASE WHEN LOWER(COALESCE(we.status, 'Active')) = 'active' THEN 'active' ELSE 'inactive' END,
    'wbom_backfill_011',
    'resolved',
    1.0,
    we.employee_id,
    COALESCE(we.basic_salary, 0),
    we.designation,
    we.joining_date
FROM wbom_employees we
WHERE
    -- No FPE record linked by wbom_employee_id
    NOT EXISTS (
        SELECT 1 FROM fpe_employees fe
        WHERE fe.wbom_employee_id = we.employee_id
    )
    -- No FPE record with matching phone
    AND NOT EXISTS (
        SELECT 1 FROM fpe_employees fe
        WHERE RIGHT(REGEXP_REPLACE(COALESCE(fe.primary_phone, ''), '\D', '', 'g'), 10)
              = RIGHT(REGEXP_REPLACE(we.employee_mobile, '\D', '', 'g'), 10)
        AND RIGHT(REGEXP_REPLACE(we.employee_mobile, '\D', '', 'g'), 10) <> ''
        AND LENGTH(RIGHT(REGEXP_REPLACE(we.employee_mobile, '\D', '', 'g'), 10)) = 10
    )
    AND we.employee_mobile IS NOT NULL
    AND TRIM(we.employee_mobile) <> '';

-- ── 5. Assign employee_code to newly created rows that lack one ──────────────
UPDATE fpe_employees
SET employee_code = 'EMP-' || LPAD(id::text, 5, '0')
WHERE employee_code IS NULL;
