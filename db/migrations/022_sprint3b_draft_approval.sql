-- ============================================================
-- Sprint-3B — Draft Approval → Accountant Forward → Canonical Transaction → Ledger
-- File: db/migrations/022_sprint3b_draft_approval.sql
--
-- Additive-only migration.  No existing column is dropped or renamed.
-- Adds the columns needed to track the post-approval financial lifecycle:
--   • transaction_id   — FK to fpe_cash_transactions.id (set on APPROVED)
--   • txn_ref          — canonical transaction reference (denormalised for fast lookup)
--   • rejected_reason  — free-text reason (set on REJECT)
--   • reviewed_by      — admin phone who made the decision
--   • reviewed_at      — timestamp of the decision
--   • version          — edit version counter (0 = original, increments on EDIT)
--   • before_state     — JSONB snapshot of the draft before the last EDIT (audit)
--   • after_state      — JSONB snapshot of the draft after the last EDIT (audit)
--   • editor           — admin phone who performed the last EDIT
--   • completed_at     — timestamp when status became 'completed'
-- ============================================================

ALTER TABLE fazle_payment_drafts
    ADD COLUMN IF NOT EXISTS transaction_id BIGINT;
ALTER TABLE fazle_payment_drafts
    ADD COLUMN IF NOT EXISTS txn_ref TEXT;
ALTER TABLE fazle_payment_drafts
    ADD COLUMN IF NOT EXISTS rejected_reason TEXT;
ALTER TABLE fazle_payment_drafts
    ADD COLUMN IF NOT EXISTS reviewed_by TEXT;
ALTER TABLE fazle_payment_drafts
    ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ;
ALTER TABLE fazle_payment_drafts
    ADD COLUMN IF NOT EXISTS version INT NOT NULL DEFAULT 0;
ALTER TABLE fazle_payment_drafts
    ADD COLUMN IF NOT EXISTS before_state JSONB;
ALTER TABLE fazle_payment_drafts
    ADD COLUMN IF NOT EXISTS after_state JSONB;
ALTER TABLE fazle_payment_drafts
    ADD COLUMN IF NOT EXISTS editor TEXT;
ALTER TABLE fazle_payment_drafts
    ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ;

-- Index for fast idempotency lookup: "does this draft already have a transaction?"
CREATE INDEX IF NOT EXISTS idx_payment_drafts_txn_ref
    ON fazle_payment_drafts (txn_ref)
    WHERE txn_ref IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_payment_drafts_transaction_id
    ON fazle_payment_drafts (transaction_id)
    WHERE transaction_id IS NOT NULL;

-- ── Audit table for Sprint-3B draft lifecycle events ─────────────────────────
-- Reuses the existing fpe_accounting_audit_logs table for transaction/ledger
-- events.  This new table captures draft-specific lifecycle events that are
-- NOT accounting events (created, edited, approved, rejected, expired).
CREATE TABLE IF NOT EXISTS fazle_draft_audit_log (
    id              BIGSERIAL    PRIMARY KEY,
    draft_id        INT          NOT NULL REFERENCES fazle_payment_drafts(id),
    event           TEXT         NOT NULL,
    -- created | edited | approved | rejected | expired | transaction_created |
    -- ledger_updated | accountant_forwarded
    before_state    JSONB,
    after_state     JSONB,
    performed_by    TEXT,
    reason          TEXT,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_draft_audit_draft
    ON fazle_draft_audit_log (draft_id, created_at);
CREATE INDEX IF NOT EXISTS idx_draft_audit_event
    ON fazle_draft_audit_log (event, created_at);

-- ── Verification ─────────────────────────────────────────────────────────────
-- Confirm no financial tables were touched.
-- (This is a comment-only guard; the migration is purely additive to
--  fazle_payment_drafts and creates one new audit table.)