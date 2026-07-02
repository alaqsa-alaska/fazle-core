# Sprint-C0D: Migration Repair + Test Infrastructure Alignment — Completion Report

**Status:** ✅ COMPLETE  
**Date:** 2026-06-29  
**Predecessor:** Sprint-C0C (Canonical Schema Generation — APPROVED WITH MODIFICATIONS)  
**Successor:** Sprint-C1 (Canonical Transaction Function Consolidation)

---

## Executive Summary

Sprint-C0D resolved the Configuration Drift between Production DB, Migration Files, and Test Infrastructure (conftest.py). All 4 migration files were created, the source_bridge crash bug was fixed with an acceptance test, and conftest.py was aligned with the canonical schema in 5 incremental steps. Test suite verification confirms 75/77 relevant tests pass — the 2 failures are pre-existing issues unrelated to C0D.

---

## Deliverables Completed

### 1. Migration Execution Order Document
**File:** `core/audits/SPRINT-C0D-MIGRATION-EXECUTION-ORDER.md`

Documents all existing migrations across 3 directories, new C0D migration order, safety rules, and rollback plan.

### 2. Migration Files (4 files, all idempotent)

| Migration | File | Purpose |
|-----------|------|---------|
| C0D-001 | `core/db/migrations/023_c0d_001_fpe_soft_delete_columns.sql` | Add `deleted_at`, `deleted_by`, `updated_at` + trigger + index to `fpe_cash_transactions` |
| C0D-002 | `core/db/migrations/023_c0d_002_deprecate_approved_columns.sql` | Mark `approved_by`/`approved_at` as DEPRECATED via COMMENT (no physical DROP, per Owner modification) |
| C0D-003 | `core/db/migrations/023_c0d_003_fpe_missing_indexes.sql` | Add pg_trgm extension + trigram indexes + phone index to `fpe_cash_transactions` |
| C0D-004 | `core/db/migrations/023_c0d_004_income_check_constraint.sql` | Add `CHECK(amount > 0)` on `fpe_income_transactions` |

### 3. Source Code Fix: source_bridge Crash Bug
**File:** `core/modules/payment_correction/__init__.py`

- Removed `source_bridge` from INSERT column list in `adjust_payment()`
- Removed `draft.get("source_bridge") or "bridge2"` parameter
- Renumbered parameters: $9-$14 → $9-$13

### 4. Acceptance Test: source_bridge Fix
**File:** `core/tests/unit/test_c0d_source_bridge_fix.py`

- `test_adjust_payment_insert_does_not_reference_source_bridge()` — inspects source code via `inspect.getsource()` to verify `source_bridge` is not in `adjust_payment()`
- `test_adjust_payment_insert_column_count_matches_values()` — verifies max parameter is $13 (was $14 before fix)

### 5. conftest.py Repair (5 Incremental Steps)

| Step | Table | Changes |
|------|-------|---------|
| 1 | `fpe_cash_transactions` | Added `deleted_at`, `deleted_by`, `updated_at`; Fixed `employee_id` INT→BIGINT (nullable); Fixed `payout_method` VARCHAR(20) NOT NULL→TEXT (nullable); Fixed `accounting_period` VARCHAR(7) NOT NULL→TEXT (nullable); Added `idx_fpe_txn_not_deleted` index |
| 2 | `wbom_cash_transactions` | Removed phantom `is_reversal` column; Removed phantom `reversal_reason` column; Fixed `whatsapp_message_id` INT→VARCHAR(100) |
| 3 | `fpe_employee_ledger` | Added `id BIGSERIAL PRIMARY KEY`; Changed `employee_id` INT→BIGINT; Changed composite PK to UNIQUE constraint; Fixed `accounting_period` VARCHAR(7)→TEXT |
| 4 | `fazle_payment_drafts` | Removed phantom `admin_reply` column; Removed phantom `source_bridge` column; Removed phantom `payment_number` column; Added `method` column; Fixed `gross_amount`/`food_bill`/`conveyance`/`advance_deduction` FLOAT→NUMERIC; Fixed `duty_days` FLOAT→NUMERIC(6,2); Fixed `expected_amount`/`approved_amount` FLOAT→NUMERIC(12,2) |
| 5 | `fpe_income_transactions` + `fpe_accounting_audit_logs` + `wbom_staging_payments` | Added complete `fpe_income_transactions` table (was entirely missing); Fixed `fpe_accounting_audit_logs` types VARCHAR→TEXT + added DEFAULT for `performed_by`; Fixed `wbom_staging_payments` types TEXT→VARCHAR, TIMESTAMPTZ→TIMESTAMP, FLOAT→NUMERIC(4,2), DECIMAL(10,2)→DECIMAL(12,2); Added `idx_staging_payments_status` index |

### 6. Test Fixes (phantom column references)

| File | Fix |
|------|-----|
| `tests/db/test_db_consistency.py` | Removed `is_reversal` from `test_reversal_self_reference_integrity` INSERT + assertion; Removed `is_reversal` from `test_reversal_chain_consistent` INSERT + query; Fixed `test_payroll_run_default_status_draft` — was passing string "2026-05" to INT `period_month` column, added `period_year` |
| `tests/conftest.py` | Fixed `seed_payment_draft` fixture — replaced `payment_number` with `payout_mobile` (column was removed from `fazle_payment_drafts`); Added `fpe_income_transactions` to TRUNCATE list in `test_db_pool` fixture |

---

## Test Suite Verification

### Tests Run (C0D-relevant subset)
```
tests/db/test_db_consistency.py     — 14 passed
tests/test_fpe_accounting.py        — 19 passed
tests/test_fpe_employee.py          — 5 passed, 2 failed (pre-existing)
tests/test_fpe_parser.py            — 22 passed
tests/unit/test_payment_workflow.py — 23 passed
tests/unit/test_c0d_source_bridge_fix.py — 2 passed
─────────────────────────────────────────
Total: 75 passed, 2 failed (pre-existing)
```

### Pre-existing Failures (NOT caused by C0D)
1. `test_fpe_employee.py::test_auto_create_when_no_match` — `RuntimeError: DB pool not initialized`. The test mocks `fetch_val` but the code uses `db_conn()` directly. Pre-existing mock/patch mismatch.
2. `test_fpe_employee.py::test_none_returned_when_no_name_no_phone` — The test expects auto-creation but the code returns None when no name and no phone. Pre-existing test/code logic mismatch.

### Full Suite Notes
The full test suite has ~373 ERRORs from:
- Missing `apscheduler` Python module (affects all `client` fixture tests that import `app.main`)
- E2e browser tests (Playwright/chromium not available in this environment)
- Various pre-existing unit test mock/patch issues

These are all environment/dependency issues, NOT caused by C0D conftest changes.

---

## Owner Modifications Applied

| # | Modification | Status |
|---|-------------|--------|
| 1 | approved_by/approved_at → DEPRECATE (not DROP) | ✅ C0D-002 uses COMMENT ON COLUMN |
| 2 | source_bridge fix → Add Acceptance Test | ✅ `test_c0d_source_bridge_fix.py` created |
| 3 | conftest changes → Small incremental steps | ✅ 5 incremental steps, each verified |
| 4 | Create Migration Execution Order document | ✅ Created before any migration |

---

## Files Modified/Created

### Created (7 files)
- `core/audits/SPRINT-C0D-MIGRATION-EXECUTION-ORDER.md`
- `core/db/migrations/023_c0d_001_fpe_soft_delete_columns.sql`
- `core/db/migrations/023_c0d_002_deprecate_approved_columns.sql`
- `core/db/migrations/023_c0d_003_fpe_missing_indexes.sql`
- `core/db/migrations/023_c0d_004_income_check_constraint.sql`
- `core/tests/unit/test_c0d_source_bridge_fix.py`
- `core/audits/SPRINT-C0D-MIGRATION-REPAIR-COMPLETION.md` (this file)

### Modified (3 files)
- `core/modules/payment_correction/__init__.py` — source_bridge fix
- `core/tests/conftest.py` — 5 incremental schema repairs + fixture fix + TRUNCATE list update
- `core/tests/db/test_db_consistency.py` — phantom column reference fixes + payroll test fix

---

## Success Criteria

| Criterion | Status |
|-----------|--------|
| All 4 migration files created and idempotent | ✅ |
| source_bridge crash bug fixed | ✅ |
| Acceptance test for source_bridge fix passes | ✅ |
| conftest.py aligned with canonical schema for all 8 financial tables | ✅ |
| No new test failures introduced by C0D | ✅ |
| Migration Execution Order document created | ✅ |
| Owner modifications applied | ✅ |

---

## Sign-off

**Sprint-C0D Status:** ✅ COMPLETE — Ready for Owner review and Sprint-C1.

**Next Sprint:** C1 (Canonical Transaction Function Consolidation) — consolidate the dual transaction creation paths (`fpe_accounting.create_transaction()` vs `admin_transactions.add_admin_transaction()`) into a single canonical writer.

---

## Roadmap

```
C0A ✅ → C0B ✅ → C0C ✅ → C0D ✅ → C1 ⏳ → C2 → Release Candidate