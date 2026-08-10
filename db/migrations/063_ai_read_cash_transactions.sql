-- Migration 063: ai_read_cash_transactions view (Hermes Capability
-- Expansion, Level 1 — 2026-08-10)
--
-- Read-only view completing the already-Owner-approved
-- proposal_ai_read_cash_transactions_20260802.md (assistant-platform repo,
-- marked FINAL). Application-side code (fazleBridge.js's
-- GET /cash-transactions, fazleTools.js, fazle-mcp's get_cash_transactions
-- tool) was already written and deployed against this exact view name and
-- column list — this migration is the only remaining piece. Until it's
-- run, the get_cash_transactions MCP tool returns a clean "not yet
-- available" message instead of attempting the call (see fazle-mcp
-- commit e17aeec).
--
-- fpe_cash_transactions is the sole canonical cash ledger (Owner Directive
-- 2026-06-29) — never wbom_cash_transactions (legacy/archive only), same
-- rule this repo enforces everywhere else cash transactions are touched.
--
-- Deliberately excluded (matching every other ai_read_* view's pattern of
-- excluding sensitive fields at the view level): payout_phone/
-- employee_phone/payment_mobile (phone numbers), original_payload/metadata
-- (raw jsonb), approved_by/submitted_by/created_by (internal usernames),
-- source_message_id/fpe_wa_message_id (internal cross-references).
--
-- NOT RUN BY AI. KB hard constraint #7: "Production DB schema cannot be
-- changed by AI." Run this file against production Postgres as a role
-- with CREATE/GRANT on `public` (not the read-only fazle_ai_reader
-- connection) — e.g. `psql "$DATABASE_URL" -f db/migrations/063_ai_read_cash_transactions.sql`,
-- or paste the statements below directly.
--
-- Rollback: `DROP VIEW ai_read_cash_transactions;` — instant, no data
-- impact (view only ever reads). To cut AI access without dropping the
-- view: `REVOKE SELECT ON ai_read_cash_transactions FROM fazle_ai_reader;`

CREATE VIEW ai_read_cash_transactions AS
SELECT
    t.txn_ref             AS transaction_ref,
    t.txn_date             AS transaction_date,
    t.amount,
    t.txn_category         AS category,
    t.transaction_status,
    t.payout_method,
    COALESCE(e.full_name, t.employee_name_raw) AS employee_name,
    t.is_reversal
FROM fpe_cash_transactions t
LEFT JOIN fpe_employees e ON e.id = t.employee_id
WHERE t.deleted_at IS NULL
ORDER BY t.txn_date DESC;

GRANT SELECT ON ai_read_cash_transactions TO fazle_ai_reader;
