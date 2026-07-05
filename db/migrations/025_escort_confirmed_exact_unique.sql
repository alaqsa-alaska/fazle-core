-- Migration 025: enforce exact confirmed-key uniqueness for escort programs
-- Key: COALESCE(start_date, program_date) + mother_vessel + lighter_vessel + escort_name
-- Prevents duplicate confirmed rows created by backfill/replay or repeated admin confirmations.

CREATE UNIQUE INDEX IF NOT EXISTS uq_wbom_escort_confirmed_exact
ON wbom_escort_programs (
    COALESCE(start_date, program_date),
    UPPER(TRIM(mother_vessel)),
    UPPER(TRIM(lighter_vessel)),
    UPPER(TRIM(escort_name))
)
WHERE status = 'confirmed'
  AND COALESCE(TRIM(mother_vessel), '') <> ''
  AND COALESCE(TRIM(lighter_vessel), '') <> ''
  AND COALESCE(TRIM(escort_name), '') <> '';
