-- Additive, data-preserving columns for the atomic release/settlement workflow.
ALTER TABLE wbom_escort_programs
    ADD COLUMN IF NOT EXISTS food_bill NUMERIC(10,2) NOT NULL DEFAULT 0;

ALTER TABLE fazle_payment_drafts
    ADD COLUMN IF NOT EXISTS gross_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS food_bill NUMERIC(10,2) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS conveyance NUMERIC(10,2) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS advance_deduction NUMERIC(12,2) NOT NULL DEFAULT 0;

ALTER TABLE escort_roster_entries
    ADD COLUMN IF NOT EXISTS food_bill NUMERIC(10,2) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS advance_deduction NUMERIC(12,2) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS net_payable NUMERIC(12,2) NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_payment_drafts_program_type
    ON fazle_payment_drafts (escort_program_id, draft_type);

ALTER TABLE fazle_outbound_queue
    ADD COLUMN IF NOT EXISTS fallback_channel TEXT,
    ADD COLUMN IF NOT EXISTS external_id TEXT,
    ADD COLUMN IF NOT EXISTS locked_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
