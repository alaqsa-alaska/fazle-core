# Canonical Financial Data Model Audit
## Field-by-Field Semantic Mapping, Migration Feasibility, Constitution Compatibility

**Date:** 2026-06-28  
**Auditor:** Financial Architecture Refactoring Auditor  
**Status:** Evidence complete — awaiting Owner's permanent Architectural Decision  

---

## Table of Contents

1. [Schema Evidence: Both Tables Side-by-Side](#1-schema-evidence-both-tables-side-by-side)
2. [Field-by-Field Semantic Mapping](#2-field-by-field-semantic-mapping)
3. [Module Read/Write Map](#3-module-readwrite-map)
4. [Migration Feasibility Analysis](#4-migration-feasibility-analysis-data-loss-risk)
5. [Constitution Compatibility Assessment](#5-constitution-compatibility-assessment)
6. [Auditor's Final Recommendation](#6-auditors-final-recommendation)

---

## 1. Schema Evidence: Both Tables Side-by-Side

### `wbom_cash_transactions` (Legacy)

**Source:** [`tests/conftest.py:167`](tests/conftest.py:167) + [`db/migrations/008_payment_correction.sql`](db/migrations/008_payment_correction.sql) + [`db/migrations/003c_schema_align.sql`](db/migrations/003c_schema_align.sql)

| # | Column | Type | Default | FK | Notes |
|---|--------|------|---------|-----|-------|
| 1 | `transaction_id` | SERIAL | — | PK | Legacy auto-increment PK |
| 2 | `employee_id` | INT | — | → wbom_employees(employee_id) | Legacy employee system |
| 3 | `program_id` | INT | — | → wbom_escort_programs(program_id) | Escort program link |
| 4 | `transaction_type` | VARCHAR(20) | — | — | 'advance' \| 'escort_payment' \| 'reversal' \| 'in' \| 'out' |
| 5 | `amount` | DECIMAL(10,2) | — | — | Can be negative (reversal) |
| 6 | `payment_method` | VARCHAR(10) | — | — | 'bkash' \| 'nagad' \| 'cash' \| 'rocket' |
| 7 | `payment_mobile` | VARCHAR(20) | — | — | Payout mobile number |
| 8 | `employee_phone` | TEXT | — | — | Employee's phone (separate from payout) |
| 9 | `payment_number` | TEXT | — | — | Another phone field (redundant with payment_mobile?) |
| 10 | `transaction_date` | DATE | CURRENT_DATE | — | Date of transaction |
| 11 | `transaction_time` | TIMESTAMPTZ | NOW() | — | Timestamp |
| 12 | `status` | VARCHAR(20) | 'Completed' | — | 'Completed' \| 'Pending' etc. |
| 13 | `reference_number` | VARCHAR(50) | — | — | External reference |
| 14 | `remarks` | TEXT | — | — | Free-text notes |
| 15 | `created_by` | VARCHAR(50) | — | — | Who created it |
| 16 | `reversal_of` | INT | — | → wbom_cash_transactions(transaction_id) | Added by migration 008 |
| 17 | `is_reversal` | BOOLEAN | FALSE | — | Added by migration 008 |
| 18 | `is_reversed` | BOOLEAN | FALSE | — | Added by migration 008 — marks original as reversed |
| 19 | `correction_note` | TEXT | — | — | Added by migration 008 |
| 20 | `reversal_reason` | TEXT | — | — | In schema, not seen in code |
| 21 | `source` | TEXT | — | — | 'admin_nl' \| 'admin-accountant-instruction' \| 'payment-draft' |
| 22 | `idempotency_key` | TEXT | — | — | Unique where not null |
| 23 | `whatsapp_message_id` | INT | — | — | WhatsApp message reference |

**Total: 23 columns**

### `fpe_cash_transactions` (FPE Engine)

**Source:** [`modules/fazle_payroll_engine/migrations/001_fpe_schema.sql:87`](modules/fazle_payroll_engine/migrations/001_fpe_schema.sql:87) + [`migrations/004_add_accounting_period_constraint.sql`](migrations/004_add_accounting_period_constraint.sql)

| # | Column | Type | Default | FK | Notes |
|---|--------|------|---------|-----|-------|
| 1 | `id` | BIGSERIAL | — | PK | FPE auto-increment PK |
| 2 | `txn_ref` | TEXT | — | UNIQUE NOT NULL | Deterministic hash — idempotency |
| 3 | `fpe_wa_message_id` | BIGINT | — | → fpe_wa_messages(id) | FPE WhatsApp message link |
| 4 | `employee_id` | BIGINT | — | → fpe_employees(id) | FPE employee system |
| 5 | `employee_name_raw` | TEXT | — | — | Name as parsed |
| 6 | `amount` | NUMERIC(12,2) | — | — | NOT NULL; can be negative (reversal) |
| 7 | `payout_phone` | TEXT | — | — | 01XXXXXXXXX canonical |
| 8 | `payout_method` | TEXT | — | — | 'bkash' \| 'nagad' \| 'cash' \| 'rocket' \| 'bank' |
| 9 | `txn_date` | DATE | — | — | NOT NULL |
| 10 | `txn_category` | TEXT | 'salary' | — | 'salary' \| 'advance' \| 'bonus' \| 'deduction' \| 'correction' |
| 11 | `source_message_text` | TEXT | — | — | Original message text |
| 12 | `is_reversal` | BOOLEAN | FALSE | — | Marks this row AS a reversal |
| 13 | `reversed_txn_id` | BIGINT | — | → fpe_cash_transactions(id) | Points to original being reversed |
| 14 | `accounting_period` | TEXT | — | — | YYYY-MM; CHECK constraint enforced |
| 15 | `created_at` | TIMESTAMPTZ | NOW() | — | — |
| 16 | `created_by` | TEXT | 'fpe_engine' | — | — |

**Total: 16 columns (as per migration files)**

### ⚠️ Audit Finding: Phantom Columns

`admin_transactions` code references `updated_at`, `deleted_at`, `deleted_by` on `fpe_cash_transactions`:
- [`admin_transactions/__init__.py:632`](modules/admin_transactions/__init__.py:632): `SELECT updated_at FROM fpe_cash_transactions`
- [`admin_transactions/__init__.py:659`](modules/admin_transactions/__init__.py:659): `SELECT ... deleted_at ... FROM fpe_cash_transactions`
- [`admin_transactions/__init__.py:788`](modules/admin_transactions/__init__.py:788): `UPDATE fpe_cash_transactions SET deleted_at = $1, deleted_by = $2`

**These columns do NOT exist in any tracked migration file.** They must have been added to the production database via an untracked manual ALTER TABLE. This is a **schema drift** finding — the production database has columns not reflected in the repository's migration files.

**Impact:** If the database is recreated from migration files alone, `admin_transactions` will crash.

---

## 2. Field-by-Field Semantic Mapping

### Direct Semantic Equivalents (Same Meaning, Different Name)

| wbom_cash_transactions | fpe_cash_transactions | Semantic Match | Notes |
|------------------------|----------------------|----------------|-------|
| `transaction_id` | `id` | ✅ PK | Different name, same purpose |
| `employee_id` (→ wbom_employees) | `employee_id` (→ fpe_employees) | ⚠️ Same name, **different FK target** | wbom_employees.employee_id (INT) vs fpe_employees.id (BIGINT). Migration 008 added `fpe_employees.wbom_employee_id` soft-link for bridging. |
| `amount` | `amount` | ✅ Exact | DECIMAL(10,2) vs NUMERIC(12,2) — fpe has higher precision |
| `payment_method` | `payout_method` | ✅ Same meaning | Different column name |
| `payment_mobile` | `payout_phone` | ✅ Same meaning | Different column name |
| `transaction_date` | `txn_date` | ✅ Same meaning | Different column name |
| `remarks` | `source_message_text` | ⚠️ Partial | `remarks` is admin notes; `source_message_text` is original message. Semantics overlap but not identical. |
| `created_by` | `created_by` | ✅ Exact | Same name, same purpose |
| `is_reversal` | `is_reversal` | ✅ Exact | Both mark a row as being a reversal |
| `reversal_of` | `reversed_txn_id` | ✅ Same meaning | Different column name; both point to original transaction |
| `transaction_time` | `created_at` | ⚠️ Partial | `transaction_time` = when txn happened; `created_at` = when row was inserted. Usually same but not guaranteed. |

### Fields in wbom ONLY (No fpe Equivalent)

| wbom Column | Type | Semantic Meaning | Migration Target? | Data Loss Risk |
|-------------|------|-----------------|-------------------|----------------|
| `program_id` | INT | Escort program reference | ❌ No fpe column | 🟠 MEDIUM — program link lost |
| `transaction_type` | VARCHAR(20) | 'advance' \| 'escort_payment' \| 'reversal' \| 'in' \| 'out' | ⚠️ Maps to `txn_category` but **different vocabulary** | 🟠 MEDIUM — see vocabulary mismatch below |
| `employee_phone` | TEXT | Employee's own phone | ❌ No fpe column | 🟡 LOW — fpe uses `payout_phone` only |
| `payment_number` | TEXT | Another phone field | ❌ No fpe column | 🟡 LOW — appears redundant with `payment_mobile` |
| `status` | VARCHAR(20) | 'Completed' \| 'Pending' | ❌ No fpe column | 🟡 LOW — fpe has no status concept (immutable rows) |
| `reference_number` | VARCHAR(50) | External reference | ❌ No fpe column | 🟡 LOW — not seen used in code |
| `reversal_reason` | TEXT | Reason for reversal | ❌ No fpe column | 🟡 LOW — not seen used in code |
| `source` | TEXT | 'admin_nl' etc. | ❌ No fpe column | 🟠 MEDIUM — provenance tracking lost |
| `idempotency_key` | TEXT | Dedup key | ⚠️ Replaced by `txn_ref` (deterministic hash) | 🟢 LOW — different mechanism, same purpose |
| `whatsapp_message_id` | INT | WhatsApp msg ref | ⚠️ Maps to `fpe_wa_message_id` (BIGINT, FK to fpe_wa_messages) | 🟠 MEDIUM — FK target differs |
| `is_reversed` | BOOLEAN | Marks original as reversed | ❌ No fpe equivalent | 🔴 HIGH — **behaviour difference** (see below) |
| `correction_note` | TEXT | Correction reason | ❌ No fpe column | 🟡 LOW — could encode in `source_message_text` |

### Fields in fpe ONLY (No wbom Equivalent)

| fpe Column | Type | Semantic Meaning | Migration Source? | Data Loss Risk |
|------------|------|-----------------|-------------------|----------------|
| `txn_ref` | TEXT UNIQUE | Deterministic idempotency hash | 🆕 New — no wbom equivalent | N/A — generated, not migrated |
| `fpe_wa_message_id` | BIGINT | FK to fpe_wa_messages | 🆕 New | N/A — FPE-specific |
| `employee_name_raw` | TEXT | Name as parsed | ⚠️ Could derive from wbom_employees.employee_name | 🟢 LOW |
| `txn_category` | TEXT | 'salary' \| 'advance' \| 'bonus' \| 'deduction' \| 'correction' | ⚠️ Maps from `transaction_type` but **vocabulary mismatch** | 🟠 MEDIUM — see below |
| `accounting_period` | TEXT | YYYY-MM | 🆕 New — derivable from `transaction_date` | 🟢 LOW — can compute |
| `updated_at` | TIMESTAMPTZ | (phantom column) | 🆕 New | N/A |
| `deleted_at` | TIMESTAMPTZ | (phantom column) | 🆕 New | N/A |
| `deleted_by` | TEXT | (phantom column) | 🆕 New | N/A |

### Transaction Type Vocabulary Mismatch

| wbom `transaction_type` | fpe `txn_category` | Match? | Notes |
|------------------------|-------------------|--------|-------|
| `'advance'` | `'advance'` | ✅ Exact | — |
| `'escort_payment'` | `'salary'` | ⚠️ **Mapped** | escort_payment → salary (draft_approval does this) |
| `'reversal'` | (is_reversal=TRUE, txn_category from original) | ⚠️ **Different mechanism** | wbom uses type='reversal'; fpe uses boolean flag + original category |
| `'in'` | ❌ No equivalent | ❌ **No match** | Cash income — fpe uses separate `fpe_income_transactions` table |
| `'out'` | ❌ No equivalent | ❌ **No match** | Cash outgoing — fpe doesn't distinguish in/out by type |

**This is a critical semantic gap.** The wbom table has `'in'` and `'out'` transaction types (used by reports for cash flow). The fpe table has no equivalent — it uses `txn_category` (salary/advance/bonus/deduction/correction) which is a different classification axis.

---

## 3. Module Read/Write Map

### Write Map (Who writes to which table)

| Module | Function | Writes to `wbom_cash_transactions` | Writes to `fpe_cash_transactions` | Writes to `fpe_employee_ledger` | Writes to `fpe_accounting_audit_logs` | Writes to `fazle_payment_correction_log` |
|--------|----------|----------------------------------|----------------------------------|--------------------------------|--------------------------------------|----------------------------------------|
| `payment_ingest` | `_finalize_admin_instruction()` | ✅ INSERT | ❌ | ❌ | ❌ | ❌ |
| `admin_commands/nl_advance_record` | `intent_advance_record()` | ✅ INSERT | ❌ | ❌ | ❌ | ❌ |
| `payment_workflow` | `finalize_payment()` | ✅ INSERT | ❌ | ❌ | ❌ | ❌ |
| `payment_correction` | `reverse_payment()` | ✅ INSERT + UPDATE | ❌ | ❌ | ❌ | ✅ INSERT |
| `admin_transactions` | `add_admin_transaction()` | ❌ | ✅ INSERT | ✅ (via `_adjust_ledger`) | ✅ INSERT | ❌ |
| `admin_transactions` | `edit_admin_transaction()` | ❌ | ✅ UPDATE | ✅ (adjust old + new) | ✅ INSERT | ❌ |
| `admin_transactions` | `soft_delete_transaction()` | ❌ | ✅ UPDATE (deleted_at) | ✅ (reverse) | ✅ INSERT | ❌ |
| `fazle_payroll_engine/accounting` | `create_transaction()` | ❌ | ✅ INSERT | ✅ `_upsert_ledger()` | ✅ INSERT | ❌ |
| `fazle_payroll_engine/accounting` | `reverse_transaction()` | ❌ | ✅ INSERT (reversal) | ✅ `_upsert_ledger()` | ✅ INSERT | ❌ |
| `draft_approval` | `create_canonical_transaction()` | ❌ | ✅ (via `create_transaction()`) | ✅ (via canonical) | ✅ (via canonical) | ❌ |

### Read Map (Who reads from which table)

| Module | Function | Reads `wbom_cash_transactions` | Reads `fpe_cash_transactions` | Reads `fpe_employee_ledger` |
|--------|----------|-------------------------------|------------------------------|---------------------------|
| `reports` | `cash_report()` | ✅ SELECT | ❌ | ❌ |
| `reports` | (cash summary) | ✅ SELECT | ❌ | ❌ |
| `payroll` | advance calculation | ✅ SELECT SUM | ❌ | ❌ |
| `payroll_logic` | monthly transactions | ✅ SELECT | ❌ | ❌ |
| `payroll_logic` | total ever paid | ✅ SELECT SUM | ❌ | ❌ |
| `admin_commands/nl_advance_record` | `_cumulative_advance()` | ✅ SELECT SUM | ❌ | ❌ |
| `payment_correction` | `reverse_payment()` (find original) | ✅ SELECT | ❌ | ❌ |
| `fazle_payroll_engine/reconcile` | reconciliation | ❌ | ✅ SELECT SUM | ❌ |
| `fazle_payroll_engine/routes` | transaction list/detail | ❌ | ✅ SELECT | ❌ |
| `fazle_payroll_engine/routes` | employee transactions | ❌ | ✅ SELECT | ❌ |
| `admin_transactions` | list/get/edit/delete | ❌ | ✅ SELECT | ❌ |
| `app/main.py` | dashboard overview | ❌ (counts only employees/drafts) | ❌ | ❌ |

### Visual Summary

```
WRITES:
  wbom_cash_transactions ←── payment_ingest, nl_advance_record, payment_workflow, payment_correction
  fpe_cash_transactions  ←── admin_transactions, accounting (canonical), draft_approval

READS:
  wbom_cash_transactions ──→ reports, payroll, payroll_logic, nl_advance_record, payment_correction
  fpe_cash_transactions  ──→ fpe_routes, fpe_reconcile, admin_transactions

DISCONNECT:
  Writers to fpe_cash_transactions are INVISIBLE to readers of wbom_cash_transactions
  Writers to wbom_cash_transactions are INVISIBLE to readers of fpe_cash_transactions
```

---

## 4. Migration Feasibility Analysis (Data Loss Risk)

### Scenario A: Migrate wbom → fpe (make fpe the single table)

| Step | Action | Data Loss? | Risk |
|------|--------|-----------|------|
| 1 | Resolve `wbom_employees.employee_id` → `fpe_employees.id` | ⚠️ Some employees may not exist in fpe_employees | 🟠 MEDIUM — migration 008 added `wbom_employee_id` soft-link, but not all employees may be linked |
| 2 | Map `transaction_type` → `txn_category` | ⚠️ `'in'` and `'out'` have no fpe equivalent | 🔴 HIGH — cash flow transactions cannot be migrated without semantic loss |
| 3 | Map `program_id` | ❌ No fpe column | 🟠 MEDIUM — program link lost |
| 4 | Map `status` | ❌ No fpe column | 🟡 LOW — fpe is immutable, status not needed |
| 5 | Map `source` | ❌ No fpe column | 🟡 LOW — encode in `source_message_text` |
| 6 | Map `employee_phone` | ❌ No fpe column | 🟡 LOW — fpe uses `payout_phone` |
| 7 | Map `is_reversed` | ❌ No fpe equivalent | 🔴 HIGH — **behaviour change**: fpe never marks original as reversed |
| 8 | Map `idempotency_key` → `txn_ref` | ⚠️ Different mechanism | 🟡 LOW — generate new txn_ref |
| 9 | Map `whatsapp_message_id` → `fpe_wa_message_id` | ⚠️ FK target differs | 🟠 MEDIUM — may need NULL or create fpe_wa_messages rows |
| 10 | Migrate `fpe_employee_ledger` | 🆕 Must rebuild from migrated transactions | 🟠 MEDIUM — ledger doesn't exist for wbom data |

**Verdict: Migration from wbom → fpe has CRITICAL data loss risk** due to:
1. `'in'`/`'out'` transaction types have no fpe equivalent
2. `is_reversed` mutation behaviour has no fpe equivalent
3. `program_id` link would be lost
4. Employee ID resolution gap

### Scenario B: Migrate fpe → wbom (make wbom the single table)

| Step | Action | Data Loss? | Risk |
|------|--------|-----------|------|
| 1 | Map `fpe_employees.id` → `wbom_employees.employee_id` | ⚠️ Reverse resolution needed | 🟠 MEDIUM — use `fpe_employees.wbom_employee_id` soft-link |
| 2 | Map `txn_category` → `transaction_type` | ⚠️ `'bonus'`, `'deduction'`, `'correction'` have no wbom equivalent | 🟠 MEDIUM — could map to closest type or add new types |
| 3 | Map `txn_ref` → `idempotency_key` | ✅ Direct map | 🟢 LOW |
| 4 | Map `accounting_period` | ❌ No wbom column | 🟡 LOW — derivable from `transaction_date` |
| 5 | Map `source_message_text` → `remarks` | ✅ Direct map | 🟢 LOW |
| 6 | Map `is_reversal` | ✅ Direct map | 🟢 LOW |
| 7 | Map `reversed_txn_id` → `reversal_of` | ✅ Direct map | 🟢 LOW |
| 8 | Map `payout_phone` → `payment_mobile` | ✅ Direct map | 🟢 LOW |
| 9 | Map `payout_method` → `payment_method` | ✅ Direct map | 🟢 LOW |
| 10 | Map `fpe_wa_message_id` → `whatsapp_message_id` | ⚠️ Type differs (BIGINT → INT) | 🟡 LOW |
| 11 | Map `employee_name_raw` | ❌ No wbom column | 🟡 LOW — derivable from `wbom_employees.employee_name` |
| 12 | Rebuild ledger | ❌ wbom has no ledger table — payroll reads directly from transactions | 🟢 LOW — no migration needed |

**Verdict: Migration from fpe → wbom has LOWER data loss risk** but still has:
1. `txn_category` vocabulary mismatch (`bonus`, `deduction`, `correction` have no wbom type)
2. `accounting_period` would be lost (but derivable)
3. `employee_name_raw` would be lost (but derivable)
4. fpe's `deleted_at`/`deleted_by` soft-delete capability would be lost (no wbom equivalent)

### Scenario C: Keep both, add sync layer

| Aspect | Assessment |
|--------|-----------|
| Data Loss | ✅ None — both tables keep their data |
| Complexity | 🟠 HIGH — sync logic, dual-write, drift detection |
| Constitution | ⚠️ Violates "one logic, one function" — sync is a second write path |
| Maintenance | 🟠 HIGH — every new write path must sync to both tables |

---

## 5. Constitution Compatibility Assessment

### Business Constitution §1: Canonical Transaction Principle

> "All financial transactions must flow through a single canonical function (`create_transaction()`). No module may directly INSERT into a transaction table."

| Table | Constitution Compliant? | Reasoning |
|-------|------------------------|-----------|
| `fpe_cash_transactions` | ✅ **YES** | `create_transaction()` already targets this table. Sprint-3B Draft Approval correctly uses it. The canonical function, audit log, and ledger are all built around this table. |
| `wbom_cash_transactions` | ❌ **NO** | 4 modules directly INSERT into this table, bypassing `create_transaction()`. No audit log. No ledger. No idempotency (except payment_ingest/payment_workflow which have their own). |

### Business Constitution §1.3: Draft as Source of Truth

| Table | Compatible? | Reasoning |
|-------|-------------|-----------|
| `fpe_cash_transactions` | ✅ **YES** | `create_transaction()` accepts a `TransactionCreateRequest` — a structured data model, not a re-parsed message. Draft Approval builds this request from the draft dict. |
| `wbom_cash_transactions` | ❌ **NO** | Direct INSERTs build SQL strings from parsed message fields — no structured request model. |

### Business Constitution §2: Three Valid Paths to Cash Ledger

| Path | Currently Uses | Constitution Says |
|------|---------------|-------------------|
| Path-1: Admin �� Accountant WhatsApp → Canonical Transaction | `wbom_cash_transactions` (payment_ingest) | Should use `create_transaction()` → `fpe_cash_transactions` |
| Path-2: Employee Request → Draft → Approved → Canonical Transaction | `fpe_cash_transactions` (draft_approval) | ✅ Already correct |
| Path-3: Operator → Pending → Approved → Canonical Transaction | `wbom_cash_transactions` (payment_workflow.finalize_payment) | Should use `create_transaction()` → `fpe_cash_transactions` |

### Business Constitution §5: Audit Requirements

> "Every financial transaction must create an audit log entry in `fpe_accounting_audit_logs`."

| Table | Audit Log? | Constitution Compliant? |
|-------|-----------|------------------------|
| `fpe_cash_transactions` | ✅ `create_transaction()` writes to `fpe_accounting_audit_logs` | ✅ YES |
| `wbom_cash_transactions` | ❌ No `fpe_accounting_audit_logs` entry for direct INSERTs | ❌ NO |

### Business Constitution §6: Ledger Requirements

> "Every financial transaction must update `fpe_employee_ledger`."

| Table | Ledger Update? | Constitution Compliant? |
|-------|---------------|------------------------|
| `fpe_cash_transactions` | ✅ `create_transaction()` calls `_upsert_ledger()` | ✅ YES |
| `wbom_cash_transactions` | ❌ No `fpe_employee_ledger` update | ❌ NO |

### Summary

| Constitution Article | `fpe_cash_transactions` | `wbom_cash_transactions` |
|---------------------|------------------------|--------------------------|
| §1 Canonical Transaction Principle | ✅ Compliant | ❌ Violated |
| §1.3 Draft as Source of Truth | ✅ Compliant | ❌ Violated |
| §2 Three Valid Paths | ✅ Path-2 compliant | ⚠️ Paths 1 & 3 use wbom |
| §5 Audit Requirements | ✅ Compliant | ❌ Violated |
| §6 Ledger Requirements | ✅ Compliant | ❌ Violated |

---

## 6. Auditor's Final Recommendation

### Which table is more compatible with the Business Constitution?

**`fpe_cash_transactions` is overwhelmingly more Constitution-compatible.**

It is the table that `create_transaction()` already targets. It has:
- ✅ Deterministic idempotency via `txn_ref`
- ✅ Audit log via `fpe_accounting_audit_logs`
- ✅ Ledger via `fpe_employee_ledger`
- ✅ Structured request model (`TransactionCreateRequest`)
- ✅ Immutable row design (no status column, no mutation of originals)
- ✅ Sprint-3B Draft Approval already uses it correctly

`wbom_cash_transactions` is the legacy table that:
- ❌ Has 4 direct INSERT paths bypassing the canonical function
- ❌ Has no audit log
- ❌ Has no ledger
- ❌ Has no structured request model
- ❌ Has no deterministic idempotency (except partial in 2 paths)
- ❌ Mutates original rows on reversal (violates immutability principle)

### The Problem

Dashboard, Payroll, and Reports read from `wbom_cash_transactions`. If `fpe_cash_transactions` becomes the single source of truth, these readers must be migrated. But the Owner's instruction forbids changing Dashboard, Payroll, and Reports.

### The Tension

| Requirement | Points to |
|-----------|-----------|
| Constitution §1 (canonical function) | `fpe_cash_transactions` |
| Constitution §5 (audit log) | `fpe_cash_transactions` |
| Constitution §6 (ledger) | `fpe_cash_transactions` |
| "Don't change Dashboard" | `wbom_cash_transactions` (readers stay) |
| "Don't change Payroll" | `wbom_cash_transactions` (readers stay) |
| "Don't change Reports" | `wbom_cash_transactions` (readers stay) |
| "Zero Behaviour Change" | Both tables must coexist |

### My Recommendation

**`fpe_cash_transactions` should be the permanent Single Source of Financial Truth.**

This is the only table that satisfies the Business Constitution. However, given the Architecture Freeze constraint:

1. **Immediate (Sprint-C1A):** Implement dual-write wrappers. Each legacy write path calls `create_transaction()` (writing to `fpe_cash_transactions` with audit + ledger) AND also writes to `wbom_cash_transactions` (preserving dashboard/payroll/report behaviour). This satisfies Constitution §1 (one business logic) while preserving external behaviour.

2. **Future (post-Release-1.0):** Migrate Dashboard, Payroll, and Reports to read from `fpe_cash_transactions` + `fpe_employee_ledger`. Then remove the `wbom_cash_transactions` dual-write. This completes the migration without breaking the freeze.

### Migration Feasibility Verdict

| Direction | Data Loss Risk | Recommendation |
|-----------|---------------|----------------|
| wbom → fpe | 🔴 HIGH (in/out types, is_reversed, program_id) | Not recommended as immediate migration |
| fpe → wbom | 🟠 MEDIUM (txn_category vocab, accounting_period, soft-delete) | Not recommended — goes against Constitution |
| Dual-write (both) | 🟢 LOW (no data moved) | ✅ **Recommended for Sprint-C1A** |
| Future: wbom readers → fpe readers | 🟡 MEDIUM (query rewrite, not data migration) | Recommended for post-Release-1.0 |

---

## Sign-off

| Role | Status |
|------|--------|
| Auditor | ✅ Evidence complete |
| Owner Architectural Decision | ⏳ Pending |

**The Owner will now make a permanent Architectural Decision on which table is the Single Source of Financial Truth.**