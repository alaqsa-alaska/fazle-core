-- Migration 037: Constrain wbom_billing_records.payment_status (Phase 2B, Commit D)
--
-- Owner decision (2026-07-10): payment_status has exactly two values --
-- 'Pending' and 'Paid'. No 'Partial' (partial-payment tracking is not
-- needed this phase). Payment is never 'Cancelled' -- only the underlying
-- escort program/duty itself can be Cancelled; that is a wholly separate
-- status (wbom_escort_programs.status), never conflated with a bill's
-- payment_status.
--
-- Lookup table vs. CHECK constraint: with only two fixed values and no
-- foreseeable need for admin-editable additions (unlike client_tier/
-- client_status, which the Owner deliberately made lookup tables because
-- new tiers/statuses were a real possibility), a lookup table here would be
-- unnecessary indirection for a fact that will not change. A CHECK
-- constraint gives the same guarantee (an invalid string can never be
-- written) with far less machinery.
--
-- Safe to apply: wbom_billing_records has 0 rows (confirmed by audit
-- immediately before this migration was written), and the column's
-- existing DEFAULT ('Pending') already satisfies the constraint.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'wbom_billing_records_payment_status_check'
    ) THEN
        ALTER TABLE wbom_billing_records
            ADD CONSTRAINT wbom_billing_records_payment_status_check
            CHECK (payment_status IN ('Pending', 'Paid'));
    END IF;
END $$;
