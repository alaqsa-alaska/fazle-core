-- Migration 044: Payment recording columns + audit log (Phase 2B, Commit D)
--
-- Adds the fields needed to record a bill's payment (mark-paid action) and
-- an append-only audit trail, mirroring wbom_payroll_approval_log's proven
-- shape (bigserial log_id, actor, from/to status, payload_json) rather than
-- inventing a new pattern -- see docs/BILL_REVISION_POLICY_DESIGN.md's own
-- recommendation to mirror it, though this table's `action` vocabulary
-- covers payment recording (Commit D), not that doc's bill-revision scope.
--
-- payment_method/payment_reference are free-form VARCHAR, matching the
-- existing looseness of payment_method columns elsewhere in this schema
-- (no CHECK constraint anywhere in this codebase enforces payment method
-- vocabulary) -- conventional values in practice are 'Bkash'/'Nagad'/'Cash'.
--
-- paid_amount always equals total_amount today (Owner Decision #3: no
-- partial payments) but is stored explicitly rather than re-derived, so a
-- future partial-payment phase would not need a schema change to read it.
--
-- Safe to apply: wbom_billing_records has 0 rows in production as of this
-- writing (confirmed before writing this migration) -- new nullable
-- columns, no backfill needed.

ALTER TABLE wbom_billing_records
    ADD COLUMN IF NOT EXISTS paid_amount NUMERIC(10, 2),
    ADD COLUMN IF NOT EXISTS payment_method VARCHAR(30),
    ADD COLUMN IF NOT EXISTS payment_reference VARCHAR(100),
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();

CREATE TABLE IF NOT EXISTS wbom_billing_payment_log (
    log_id BIGSERIAL PRIMARY KEY,
    bill_id INTEGER NOT NULL REFERENCES wbom_billing_records(bill_id),
    action VARCHAR(20) NOT NULL,           -- 'paid' today; room for 'unpaid'/'corrected' later
    actor VARCHAR(80) NOT NULL,
    from_status VARCHAR(20),
    to_status VARCHAR(20),
    reason TEXT,
    payload_json JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_wbom_billing_payment_log_bill_id ON wbom_billing_payment_log(bill_id);
CREATE INDEX IF NOT EXISTS idx_wbom_billing_payment_log_actor ON wbom_billing_payment_log(actor);
