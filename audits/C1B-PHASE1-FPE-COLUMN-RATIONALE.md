# C1B Phase 1 — FPE Cash Transactions Additive Column Rationale

> **Owner Directive:** `fpe_cash_transactions` is the only canonical cash transaction table. Additive migration only. No deletes/drops.
> **Migration file:** [`core/modules/fazle_payroll_engine/migrations/010_fpe_cash_transactions_canonical.sql`](core/modules/fazle_payroll_engine/migrations/010_fpe_cash_transactions_canonical.sql:1)

---

## Current `fpe_cash_transactions` columns (from [`001_fpe_schema.sql`](core/modules/fazle_payroll_engine/migrations/001_fpe_schema.sql:87))

| # | Column | Purpose |
|---|--------|---------|
| 1 | `id` | Primary key |
| 2 | `txn_ref` | Unique idempotency key |
| 3 | `fpe_wa_message_id` | FK to raw WhatsApp message |
| 4 | `employee_id` | FK to resolved FPE employee |
| 5 | `employee_name_raw` | Name as parsed from message |
| 6 | `amount` | Transaction amount |
| 7 | `payout_phone` | Phone money was sent to |
| 8 | `payout_method` | bkash / nagad / cash / rocket / bank |
| 9 | `txn_date` | Transaction date |
| 10 | `txn_category` | salary / advance / bonus / deduction / correction |
| 11 | `source_message_text` | Original message text |
| 12 | `is_reversal` | True if this is a reversal row |
| 13 | `reversed_txn_id` | FK to original transaction |
| 14 | `accounting_period` | YYYY-MM period |
| 15 | `created_at` | Row creation timestamp |
| 16 | `created_by` | Actor who created the row |

---

## New columns added by Migration 010

### 1. Source tracking

| Column | Type | Default | Why needed |
|--------|------|---------|------------|
| `source` | `TEXT NOT NULL` | `'whatsapp'` | Required by Owner: every transaction must record its origin (whatsapp, manual, operator, employee_draft, nl_advance, escort, correction, migration, admin_api, frontend). Enables filtering and audit. |
| `source_channel` | `TEXT` | `NULL` | Distinguishes WhatsApp bridges (`bridge1`, `bridge2`, `meta`) and web/API channels for debugging and reconciliation. |
| `source_message_id` | `TEXT` | `NULL` | External message/draft/pending id used for idempotency and traceability (e.g. wa_message_id, draft id, operator pending id). |

### 2. Legacy bridge

| Column | Type | Why needed |
|--------|------|------------|
| `legacy_wbom_transaction_id` | `BIGINT` | Required by Owner: idempotent historical migration from `wbom_cash_transactions`. Stores original WBOM id so WBOM remains a legacy archive/source reference. |

### 3. Employee identity enrichment

| Column | Type | Why needed |
|--------|------|------------|
| `employee_id_phone` | `TEXT` | The phone number used as the employee identifier in the WhatsApp message (e.g. `ID: 01795122311`). `payout_phone` can differ from this. Required by parser format `ID: <phone> <name> <payout_phone>(<method>) <amount>/-`. |
| `employee_phone` | `TEXT` | Canonical primary phone of the resolved employee. Speeds up reports/dashboard/payroll reads without joining `fpe_employees`. |

### 4. Operational context

| Column | Type | Default | Why needed |
|--------|------|---------|------------|
| `program_id` | `BIGINT` | `NULL` | Escort program / roster entry this payment relates to. Enables escort reports and payroll to read directly from `fpe_cash_transactions`. |
| `original_payload` | `JSONB` | `NULL` | Raw structured Payment Event before column normalization. Supports audit, replay, and debugging. |
| `metadata` | `JSONB NOT NULL` | `'{}'::jsonb` | Extensible key/value bag for source-specific fields (bKash trx_id, Nagad trx_id, operator notes) without future schema churn. |

### 5. Approval lifecycle

| Column | Type | Default | Why needed |
|--------|------|---------|------------|
| `transaction_status` | `TEXT NOT NULL` | `'final'` | Lifecycle state: `final` | `pending` | `reversed` | `corrected`. Operator/frontend submissions start as `pending` and become `final` only after admin approval. |
| `approval_status` | `TEXT` | `NULL` | Approval state: `approved` | `rejected` | `pending_review`. Required by Owner rule: operator/user submissions stay pending until admin approval. |
| `approved_by` | `TEXT` | `NULL` | Who approved the transaction (admin phone / user id). Audit trail. |
| `approved_at` | `TIMESTAMPTZ` | `NULL` | Timestamp of approval. Audit + reporting. |
| `review_status` | `TEXT` | `NULL` | Review queue state: `pending` | `reviewed` | `auto_resolved` | `dismissed`. For operator/user submissions needing human review. |
| `submitted_by` | `TEXT` | `NULL` | Who originally submitted (operator phone, frontend user id, admin phone). Accountability. |
| `submitted_at` | `TIMESTAMPTZ` | `NULL` | When originally submitted. Separates submission time from final creation/approval time. |

### 6. Soft-delete / mutation tracking (from Sprint-C0C canonical schema)

| Column | Type | Why needed |
|--------|------|------------|
| `deleted_at` | `TIMESTAMPTZ` | Owner forbids hard delete. Reversals/corrections keep original rows immutable; soft-delete marks invalidated rows. |
| `deleted_by` | `TEXT` | Audit: who soft-deleted. |
| `updated_at` | `TIMESTAMPTZ NOT NULL DEFAULT NOW()` | Canonical schema requirement. Only soft-delete metadata is updated; financial data stays immutable. |

---

## Indexes added

| Index | Columns | Purpose |
|-------|---------|---------|
| `idx_fpe_txn_source` | `(source, created_at DESC)` | Filter transactions by origin, newest first. |
| `idx_fpe_txn_source_message` | `(source_message_id) WHERE source_message_id IS NOT NULL` | Fast idempotency lookups by external message id. |
| `idx_fpe_txn_legacy_wbom` | `(legacy_wbom_transaction_id) WHERE legacy_wbom_transaction_id IS NOT NULL` | Fast lookup of migrated WBOM rows. |
| `idx_fpe_txn_status` | `(transaction_status, approval_status, review_status)` | Dashboard/review queue filtering. |
| `idx_fpe_txn_program` | `(program_id) WHERE program_id IS NOT NULL` | Escort/payroll program joins. |
| `idx_fpe_txn_not_deleted` | `(id) WHERE deleted_at IS NULL` | Exclude soft-deleted rows efficiently. |

---

## What is NOT added / why

| Suggested column | Decision | Reason |
|------------------|----------|--------|
| `transaction_status` CHECK constraint | Deferred | Values may evolve during C1B implementation; will add in a later migration once the state machine is finalized. |
| `approval_status` CHECK constraint | Deferred | Same as above. |
| `source` CHECK constraint | Deferred | Source list may grow; will stabilize before C1B final certification. |
| Foreign key on `legacy_wbom_transaction_id` | Not added | WBOM is legacy archive; a FK would require WBOM table to exist and be maintained. We only store the id for traceability. |
| Foreign key on `program_id` | Not added | Program table ownership is outside FPE module; keeping it as a plain id avoids cross-module coupling. |

---

## Backfill strategy

Existing rows in `fpe_cash_transactions` were created by [`create_transaction()`](core/modules/fazle_payroll_engine/accounting.py:30), which only served the WhatsApp pipeline. Therefore the migration backfills:

- `source` → `'whatsapp'`
- `transaction_status` → `'final'`
- `metadata` → `'{}'::jsonb`
- `updated_at` → `created_at`

All other new columns remain `NULL` for historical rows unless explicitly backfilled by later migration scripts (e.g. WBOM historical migration in Phase 7).

---

## Sign-off

**Phase:** C1B — Phase 1: FPE Schema Completion  
**Status:** ✅ Migration 010 drafted with additive columns only  
**Next:** Phase 2 — Payment Event Model
