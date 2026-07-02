-- ============================================================================
-- Migration 010: fpe_cash_transactions — Canonical Cash Transaction Table
-- ============================================================================
-- Owner Directive (2026-06-29):
--   `fpe_cash_transactions` is the ONLY canonical cash transaction table.
--   `wbom_cash_transactions` becomes legacy archive only.
--   All cash/payment transactions (WhatsApp, manual, operator, employee draft,
--   NL advance, escort, correction/reversal, frontend) MUST end here.
--
-- PHILOSOPHY: Additive only. No columns dropped, no data deleted, no renames.
-- All statements use ADD COLUMN IF NOT EXISTS / CREATE INDEX IF NOT EXISTS.
-- ============================================================================

-- ── 1. Source tracking ──────────────────────────────────────────────────────
-- `source`            : high-level origin of the transaction row
--                       'whatsapp' | 'manual' | 'operator' | 'employee_draft'
--                       | 'nl_advance' | 'escort' | 'correction' | 'migration'
--                       | 'admin_api' | 'frontend'
-- Why needed          : required by Owner report; lets dashboard/payroll/reports
--                       filter by origin and audit every transaction back to source.
ALTER TABLE fpe_cash_transactions
    ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'whatsapp';

-- `source_channel`    : finer-grained channel within the source
--                       e.g. 'bridge1' | 'bridge2' | 'meta' | 'web' | 'api'
-- Why needed          : distinguishes WhatsApp bridges and web/API submissions
--                       for debugging and reconciliation.
ALTER TABLE fpe_cash_transactions
    ADD COLUMN IF NOT EXISTS source_channel TEXT;

-- `source_message_id` : the original external message identifier
--                       (wa_message_id for WhatsApp, draft id for drafts,
--                        operator pending id for operator submissions)
-- Why needed          : idempotency and traceability; prevents duplicate
--                       transaction creation from the same external message.
ALTER TABLE fpe_cash_transactions
    ADD COLUMN IF NOT EXISTS source_message_id TEXT;

-- ── 2. Legacy bridge ────────────────────────────────────────────────────────
-- `legacy_wbom_transaction_id` : if this row was migrated from wbom_cash_transactions,
--                                store the original WBOM id here.
-- Why needed          : required by Owner for idempotent historical migration
--                       and to keep WBOM as a legacy archive/source reference.
ALTER TABLE fpe_cash_transactions
    ADD COLUMN IF NOT EXISTS legacy_wbom_transaction_id BIGINT;

-- ── 3. Employee identity enrichment ───────────────────────────────────────────
-- `employee_id_phone` : the phone number used as employee identifier in the
--                       original message (e.g. "ID: 01795122311").
-- Why needed          : WhatsApp Admin → Accountant parser uses this field;
--                       payout_phone can differ from employee id phone.
ALTER TABLE fpe_cash_transactions
    ADD COLUMN IF NOT EXISTS employee_id_phone TEXT;

-- `employee_phone`    : canonical primary phone of the resolved employee.
-- Why needed          : speeds up employee lookups and reports without joining
--                       fpe_employees for every read path.
ALTER TABLE fpe_cash_transactions
    ADD COLUMN IF NOT EXISTS employee_phone TEXT;

-- ── 4. Operational context ────────────────────────────────────────────────────
-- `program_id`        : escort program / roster entry this payment relates to.
-- Why needed          : escort payment drafts already reference escort programs;
--                       storing program_id on the transaction enables payroll and
--                       escort reports to read directly from fpe_cash_transactions.
ALTER TABLE fpe_cash_transactions
    ADD COLUMN IF NOT EXISTS program_id BIGINT;

-- `original_payload`  : raw structured payload that produced this transaction.
-- Why needed          : audit/replay/debug; stores the Payment Event as JSON
--                       before it was normalized into columns.
ALTER TABLE fpe_cash_transactions
    ADD COLUMN IF NOT EXISTS original_payload JSONB;

-- `metadata`          : extensible key/value bag for source-specific fields.
-- Why needed          : avoids future schema churn for per-source attributes
--                       (e.g. bKash trx_id, Nagad trx_id, operator notes).
ALTER TABLE fpe_cash_transactions
    ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb;

-- ── 5. Approval lifecycle ─────────────────────────────────────────────────────
-- `transaction_status` : current lifecycle state of the transaction
--                        'final' | 'pending' | 'reversed' | 'corrected'
-- Why needed           : operator/frontend submissions start as 'pending' and
--                        become 'final' only after admin approval; reversals
--                        are marked 'reversed'.
ALTER TABLE fpe_cash_transactions
    ADD COLUMN IF NOT EXISTS transaction_status TEXT NOT NULL DEFAULT 'final';

-- `approval_status`    : approval state for non-final submissions
--                        'approved' | 'rejected' | 'pending_review'
-- Why needed           : required by Owner rule: operator/user submissions stay
--                        pending until admin approval.
ALTER TABLE fpe_cash_transactions
    ADD COLUMN IF NOT EXISTS approval_status TEXT;

-- `approved_by`        : who approved the transaction (admin phone / user id).
-- Why needed           : audit trail for approved operator/employee-draft payments.
ALTER TABLE fpe_cash_transactions
    ADD COLUMN IF NOT EXISTS approved_by TEXT;

-- `approved_at`        : timestamp of approval.
-- Why needed           : audit + reporting.
ALTER TABLE fpe_cash_transactions
    ADD COLUMN IF NOT EXISTS approved_at TIMESTAMPTZ;

-- `review_status`      : review queue state (mirrors fpe_unmatched_messages concept)
--                        'pending' | 'reviewed' | 'auto_resolved' | 'dismissed'
-- Why needed           : operator/user submissions may need human review before
--                        becoming final transactions.
ALTER TABLE fpe_cash_transactions
    ADD COLUMN IF NOT EXISTS review_status TEXT;

-- `submitted_by`       : who originally submitted the transaction
--                        (operator phone, frontend user id, admin phone).
-- Why needed           : audit + accountability.
ALTER TABLE fpe_cash_transactions
    ADD COLUMN IF NOT EXISTS submitted_by TEXT;

-- `submitted_at`       : when the transaction was originally submitted.
-- Why needed           : separates submission time from final creation/approval time.
ALTER TABLE fpe_cash_transactions
    ADD COLUMN IF NOT EXISTS submitted_at TIMESTAMPTZ;

-- ── 6. Soft-delete / mutation tracking (from Sprint-C0C canonical schema) ───────
-- `deleted_at`         : soft-delete timestamp.
-- Why needed           : Owner forbids hard delete; reversals/corrections keep
--                        original rows immutable.
ALTER TABLE fpe_cash_transactions
    ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;

-- `deleted_by`         : who soft-deleted the row.
-- Why needed           : audit.
ALTER TABLE fpe_cash_transactions
    ADD COLUMN IF NOT EXISTS deleted_by TEXT;

-- `updated_at`         : last mutation timestamp.
-- Why needed           : required by canonical schema; only soft-delete metadata
--                        is updated — transaction financial data stays immutable.
ALTER TABLE fpe_cash_transactions
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

-- ── 7. Indexes for new read patterns ──────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_fpe_txn_source
    ON fpe_cash_transactions(source, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_fpe_txn_source_message
    ON fpe_cash_transactions(source_message_id)
    WHERE source_message_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_fpe_txn_legacy_wbom
    ON fpe_cash_transactions(legacy_wbom_transaction_id)
    WHERE legacy_wbom_transaction_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_fpe_txn_status
    ON fpe_cash_transactions(transaction_status, approval_status, review_status);

CREATE INDEX IF NOT EXISTS idx_fpe_txn_program
    ON fpe_cash_transactions(program_id)
    WHERE program_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_fpe_txn_not_deleted
    ON fpe_cash_transactions(id)
    WHERE deleted_at IS NULL;

-- ── 8. Trigger to keep updated_at current ───────────────────────────────────
CREATE OR REPLACE FUNCTION fpe_txn_set_updated_at()
    RETURNS trigger LANGUAGE plpgsql AS
$function$ BEGIN NEW.updated_at = NOW(); RETURN NEW; END; $function$;

DROP TRIGGER IF EXISTS fpe_txn_updated_at_trig ON fpe_cash_transactions;
CREATE TRIGGER fpe_txn_updated_at_trig
    BEFORE UPDATE ON fpe_cash_transactions
    FOR EACH ROW EXECUTE FUNCTION fpe_txn_set_updated_at();

-- ── 9. Backfill safe defaults for existing rows ─────────────────────────────
-- Existing rows came from the WhatsApp pipeline via create_transaction(),
-- so source='whatsapp' and transaction_status='final' are the correct defaults.
-- All other new columns remain NULL for historical rows unless backfilled later.
UPDATE fpe_cash_transactions
SET    source = COALESCE(source, 'whatsapp'),
       transaction_status = COALESCE(transaction_status, 'final'),
       metadata = COALESCE(metadata, '{}'::jsonb),
       updated_at = COALESCE(updated_at, created_at)
WHERE  source IS NULL
    OR transaction_status IS NULL
    OR metadata IS NULL
    OR updated_at IS NULL;
