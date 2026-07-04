# Sprint-C0A: Production Schema Certification Report
## Production DB ↔ Migration Files ↔ Repository (conftest.py) Alignment Audit

**Date:** 2026-06-28  
**Auditor:** Financial Architecture Refactoring Auditor  
**Methodology:** Direct query of production database via `docker exec ai-postgres psql` + comparison against migration SQL files + comparison against `tests/conftest.py` test schema  
**Status:** AUDIT COMPLETE — Awaiting Owner decision on remediation  

---

## Executive Summary

**219 tables** exist in the production database. This audit examines the **8 financial tables** identified in the Canonical Financial Data Model Audit.

**Critical Finding: Schema drift is pervasive.** Every financial table has at least one discrepancy between what exists in production, what the migration files define, and what the test schema (conftest.py) defines. If the database is recreated from migration files alone, **multiple production code paths will crash.**

| Severity | Count | Description |
|----------|-------|-------------|
| 🔴 CRITICAL | 5 | Production columns missing from migrations — code will crash on fresh DB restore |
| 🟠 HIGH | 4 | Type mismatches between production and conftest — tests may pass but production differs |
| 🟡 MEDIUM | 6 | conftest columns missing from production — test-only schema drift |
| 🟢 LOW | 3 | Minor type/nullable differences |

---

## Table 1: `fpe_cash_transactions`

### Production DB (19 columns)

| # | Column | Type | Nullable | Default |
|---|--------|------|----------|---------|
| 1 | `id` | bigint | NO | nextval |
| 2 | `txn_ref` | text | NO | — |
| 3 | `fpe_wa_message_id` | bigint | YES | — |
| 4 | `employee_id` | bigint | YES | — |
| 5 | `employee_name_raw` | text | YES | — |
| 6 | `amount` | numeric | NO | — |
| 7 | `payout_phone` | text | YES | — |
| 8 | `payout_method` | text | YES | — |
| 9 | `txn_date` | date | NO | — |
| 10 | `txn_category` | text | NO | 'salary' |
| 11 | `source_message_text` | text | YES | — |
| 12 | `is_reversal` | boolean | NO | false |
| 13 | `reversed_txn_id` | bigint | YES | — |
| 14 | `accounting_period` | text | YES | — |
| 15 | `created_at` | timestamptz | NO | now() |
| 16 | `created_by` | text | NO | 'fpe_engine' |
| **17** | **`deleted_at`** | **timestamptz** | **YES** | **—** |
| **18** | **`deleted_by`** | **text** | **YES** | **—** |
| **19** | **`updated_at`** | **timestamptz** | **YES** | **now()** |

### Migration File (001_fpe_schema.sql) — 16 columns

Columns 1-16 match production. **Columns 17-19 (`deleted_at`, `deleted_by`, `updated_at`) do NOT exist in any migration file.**

### conftest.py — 16 columns

Columns 1-16 present. **`deleted_at`, `deleted_by`, `updated_at` are MISSING.** Also `employee_id` is `INT NOT NULL` in conftest but `bigint YES` in production. `payout_method` is `VARCHAR(20) NOT NULL` in conftest but `text YES` in production. `accounting_period` is `VARCHAR(7) NOT NULL` in conftest but `text YES` in production.

### Drift Summary

| Column | Production | Migration 001 | conftest | Drift Type |
|--------|-----------|---------------|----------|------------|
| `deleted_at` | ✅ timestamptz YES | ❌ MISSING | ❌ MISSING | 🔴 CRITICAL — phantom column |
| `deleted_by` | ✅ text YES | ❌ MISSING | ❌ MISSING | 🔴 CRITICAL — phantom column |
| `updated_at` | ✅ timestamptz YES | ❌ MISSING | ❌ MISSING | 🔴 CRITICAL — phantom column |
| `employee_id` | bigint YES | bigint YES (FK) | INT NOT NULL | 🟠 HIGH — type + nullable mismatch |
| `payout_method` | text YES | text | VARCHAR(20) NOT NULL | 🟠 HIGH — type + nullable mismatch |
| `accounting_period` | text YES | text | VARCHAR(7) NOT NULL | 🟠 HIGH — type + nullable mismatch |

**Impact:** `admin_transactions` module uses `deleted_at`, `deleted_by`, `updated_at` — if DB is restored from migrations, these queries crash. Tests pass because conftest doesn't test `admin_transactions` against these columns.

---

## Table 2: `wbom_cash_transactions`

### Production DB (23 columns)

| # | Column | Type | Nullable | Default |
|---|--------|------|----------|---------|
| 1 | `transaction_id` | integer | NO | nextval |
| 2 | `employee_id` | integer | NO | — |
| 3 | `program_id` | integer | YES | — |
| 4 | `transaction_type` | varchar(20) | NO | — |
| 5 | `amount` | numeric | NO | — |
| 6 | `payment_method` | varchar(10) | NO | — |
| 7 | `payment_mobile` | varchar(20) | YES | — |
| 8 | `transaction_date` | date | NO | CURRENT_DATE |
| 9 | `transaction_time` | timestamptz | YES | now() |
| 10 | `status` | varchar(20) | YES | 'Completed' |
| 11 | `reference_number` | varchar(50) | YES | — |
| 12 | `remarks` | text | YES | — |
| **13** | **`whatsapp_message_id`** | **varchar(100)** | **YES** | **—** |
| 14 | `created_by` | varchar(50) | YES | — |
| 15 | `idempotency_key` | varchar(64) | YES | — |
| **16** | **`approved_by`** | **varchar(80)** | **YES** | **—** |
| **17** | **`approved_at`** | **timestamptz** | **YES** | **—** |
| 18 | `source` | varchar(30) | YES | 'web' |
| 19 | `is_reversed` | boolean | YES | false |
| 20 | `reversal_of` | integer | YES | — |
| 21 | `correction_note` | text | YES | — |
| 22 | `employee_phone` | text | YES | — |
| 23 | `payment_number` | text | YES | — |

### conftest.py — 23 columns (different set!)

conftest has: `transaction_id, employee_id, program_id, transaction_type, amount, payment_method, payment_mobile, employee_phone, payment_number, transaction_date, transaction_time, status, reference_number, remarks, created_by, reversal_of, is_reversal, is_reversed, correction_note, reversal_reason, source, idempotency_key, whatsapp_message_id`

### Drift Summary

| Column | Production | conftest | Migration 008 | Drift Type |
|--------|-----------|----------|---------------|------------|
| `whatsapp_message_id` | varchar(100) YES | INT | — | 🔴 CRITICAL — type mismatch (varchar vs INT) |
| `approved_by` | ✅ varchar(80) YES | ❌ MISSING | ❌ MISSING | 🔴 CRITICAL — phantom column |
| `approved_at` | ✅ timestamptz YES | ❌ MISSING | ❌ MISSING | 🔴 CRITICAL — phantom column |
| `is_reversal` | ❌ MISSING | ✅ BOOLEAN | ✅ Added by 008 | 🟡 MEDIUM — conftest has column not in production |
| `reversal_reason` | ❌ MISSING | ✅ TEXT | ❌ MISSING | 🟡 MEDIUM — conftest has column not in production |
| `payment_method` | varchar(10) NO | varchar(10) | — | 🟢 LOW — nullable differs |
| `source` | varchar(30) YES default 'web' | TEXT | — | 🟢 LOW — type differs (varchar vs TEXT) |
| `idempotency_key` | varchar(64) YES | TEXT | — | 🟢 LOW — type differs |

**Impact:** `whatsapp_message_id` is `varchar(100)` in production but `INT` in conftest. Code in `nl_advance_record.py:193` passes `whatsapp_message_id` as INT — this works in production because the column is varchar (PostgreSQL auto-casts), but the semantic is different. `approved_by` and `approved_at` are phantom columns used by `wbom_staging_payments` sync logic.

---

## Table 3: `fpe_employee_ledger`

### Production DB (10 columns)

| # | Column | Type | Nullable | Default |
|---|--------|------|----------|---------|
| 1 | `id` | bigint | NO | nextval |
| 2 | `employee_id` | bigint | NO | — |
| 3 | `accounting_period` | text | NO | — |
| 4 | `opening_balance` | numeric | NO | 0 |
| 5 | `total_earned` | numeric | NO | 0 |
| 6 | `total_paid` | numeric | NO | 0 |
| 7 | `total_advance` | numeric | NO | 0 |
| 8 | `closing_balance` | numeric | NO | 0 |
| 9 | `txn_count` | integer | NO | 0 |
| 10 | `last_updated` | timestamptz | NO | now() |

### Migration 001 — 10 columns (matches!)

### conftest.py — 9 columns (no `id` column)

conftest uses `PRIMARY KEY (employee_id, accounting_period)` — composite key, no `id` column. Production has `id` as BIGSERIAL PK with a separate unique constraint implied.

### Drift Summary

| Column | Production | Migration 001 | conftest | Drift Type |
|--------|-----------|---------------|----------|------------|
| `id` | ✅ bigint PK | ✅ bigint PK | ❌ MISSING | 🟡 MEDIUM — conftest uses composite PK instead |
| `employee_id` | bigint NO | bigint NO | INT NOT NULL | 🟠 HIGH — type mismatch |

**Impact:** conftest's composite PK `(employee_id, accounting_period)` works for tests but differs from production's `id` BIGSERIAL PK. The `_upsert_ledger()` function uses `ON CONFLICT (employee_id, accounting_period)` which works in both because production has a unique constraint on that pair (implied by the migration's `CONSTRAINT fpe_ledger_unique UNIQUE`).

---

## Table 4: `fazle_payment_drafts`

### Production DB (38 columns)

This is the most drifted table. Key columns in production not in conftest or migrations:

| Column | Production | conftest | Drift Type |
|--------|-----------|----------|------------|
| `method` | ✅ text YES | ❌ MISSING (conftest has `payment_method` instead) | 🟡 MEDIUM — duplicate/redundant column |
| `admin_reply` | ❌ NOT in production | ✅ TEXT in conftest | 🟡 MEDIUM — conftest has phantom column |
| `source_bridge` | ❌ NOT in production | ✅ TEXT DEFAULT 'bridge2' in conftest | 🟡 MEDIUM — conftest has phantom column |
| `escort_roster_entry_id` | ✅ integer YES | ✅ integer | ✅ Match (added by migration 008) |
| `gross_amount` | ✅ numeric NO default 0 | ✅ FLOAT DEFAULT 0 | 🟢 LOW — type differs (numeric vs FLOAT) |
| `food_bill` | ✅ numeric NO default 0 | ✅ FLOAT DEFAULT 0 | 🟢 LOW — type differs |
| `conveyance` | ✅ numeric NO default 0 | ✅ FLOAT DEFAULT 0 | 🟢 LOW — type differs |
| `advance_deduction` | ✅ numeric NO default 0 | ✅ FLOAT DEFAULT 0 | 🟢 LOW — type differs |
| Sprint-3B columns (transaction_id, txn_ref, etc.) | ✅ All present | ✅ All present | ✅ Match |

**Impact:** `method` column exists in production but conftest doesn't have it (conftest uses `payment_method`). Some code may use `method`, some may use `payment_method` — potential confusion. `admin_reply` and `source_bridge` exist in conftest but NOT in production — test-only columns that don't reflect reality.

---

## Table 5: `fpe_accounting_audit_logs`

### Production DB (9 columns)

| # | Column | Type | Nullable | Default |
|---|--------|------|----------|---------|
| 1 | `id` | bigint | NO | nextval |
| 2 | `entity_type` | text | NO | — |
| 3 | `entity_id` | bigint | NO | — |
| 4 | `action` | text | NO | — |
| 5 | `before_state` | jsonb | YES | — |
| 6 | `after_state` | jsonb | YES | — |
| 7 | `performed_by` | text | NO | 'fpe_engine' |
| 8 | `reason` | text | YES | — |
| 9 | `created_at` | timestamptz | NO | now() |

### Migration 005 — matches production ✅

### conftest.py — 9 columns (matches, minor type differences)

conftest uses `VARCHAR(50)` for `entity_type` and `action` vs production `text`. This is a minor type difference that PostgreSQL handles transparently.

### Drift Summary

| Column | Production | conftest | Drift Type |
|--------|-----------|----------|------------|
| `entity_type` | text | VARCHAR(50) | 🟢 LOW — type differs |
| `action` | text | VARCHAR(50) | 🟢 LOW — type differs |
| `performed_by` | text NO default 'fpe_engine' | TEXT (no default) | 🟢 LOW — default missing in conftest |

**Impact:** Negligible. This table is the best-aligned of all 8.

---

## Table 6: `fazle_payment_correction_log`

### Production DB (11 columns)

Matches migration 008 definition exactly. ✅

### conftest.py — present (matches)

### Drift Summary: ✅ No drift detected.

---

## Table 7: `fpe_income_transactions`

### Production DB (11 columns)

| # | Column | Type | Nullable | Default |
|---|--------|------|----------|---------|
| 1 | `id` | bigint | NO | nextval |
| 2 | `txn_ref` | text | NO | — |
| 3 | `fpe_wa_message_id` | bigint | YES | — |
| 4 | `employee_id` | bigint | YES | — |
| 5 | `employee_name_raw` | text | YES | — |
| 6 | `amount` | numeric | NO | — |
| 7 | `txn_date` | date | NO | — |
| 8 | `accounting_period` | text | NO | — |
| 9 | `reported_by_phone` | text | YES | — |
| 10 | `source_message_text` | text | YES | — |
| 11 | `created_at` | timestamptz | NO | now() |

### Migration 006 — matches production ✅

### conftest.py — ❌ TABLE NOT PRESENT

`fpe_income_transactions` is **not defined in conftest.py at all.** Any test that touches income transactions will fail with "table does not exist" unless the test creates it manually.

### Drift Summary

| Issue | Drift Type |
|-------|------------|
| Table missing from conftest entirely | 🟠 HIGH — tests cannot exercise income transaction paths |

---

## Table 8: `wbom_staging_payments`

### Production DB (16 columns)

| # | Column | Type | Nullable | Default |
|---|--------|------|----------|---------|
| 1 | `staging_id` | integer | NO | nextval |
| 2 | `message_id` | integer | YES | — |
| 3 | `sender_number` | varchar | YES | — |
| 4 | `extracted_name` | varchar | YES | — |
| 5 | `extracted_mobile` | varchar | YES | — |
| 6 | `amount` | numeric | YES | — |
| 7 | `payment_method` | varchar | YES | — |
| 8 | `transaction_type` | varchar | YES | — |
| 9 | `matched_employee_id` | integer | YES | — |
| 10 | `name_match_ratio` | numeric | YES | — |
| 11 | `status` | varchar | YES | 'pending' |
| 12 | `approved_by` | varchar | YES | — |
| 13 | `approved_at` | timestamp (no tz) | YES | — |
| 14 | `created_at` | timestamp (no tz) | YES | now() |
| 15 | `final_transaction_id` | integer | YES | — |
| 16 | `idempotency_key` | varchar | YES | — |

### conftest.py — 15 columns (missing `idempotency_key`)

conftest has all columns except `idempotency_key` is present but positioned differently. Also `approved_at` and `created_at` are `TIMESTAMPTZ` in conftest but `timestamp without time zone` in production.

### Drift Summary

| Column | Production | conftest | Drift Type |
|--------|-----------|----------|------------|
| `approved_at` | timestamp (no tz) | TIMESTAMPTZ | 🟡 MEDIUM — timezone mismatch |
| `created_at` | timestamp (no tz) | TIMESTAMPTZ | 🟡 MEDIUM — timezone mismatch |
| `sender_number` | varchar | TEXT | 🟢 LOW — type differs |
| `extracted_name` | varchar | TEXT | 🟢 LOW — type differs |
| `extracted_mobile` | varchar | TEXT | 🟢 LOW — type differs |

---

## Complete Drift Register

### 🔴 CRITICAL (5) — Production will crash on fresh DB restore

| # | Table | Column | Issue | Code Affected |
|---|-------|--------|-------|---------------|
| 1 | `fpe_cash_transactions` | `deleted_at` | In production, not in migrations | `admin_transactions.soft_delete_transaction()` |
| 2 | `fpe_cash_transactions` | `deleted_by` | In production, not in migrations | `admin_transactions.soft_delete_transaction()` |
| 3 | `fpe_cash_transactions` | `updated_at` | In production, not in migrations | `admin_transactions.add_admin_transaction()`, `edit_admin_transaction()` |
| 4 | `wbom_cash_transactions` | `approved_by` | In production, not in migrations/conftest | `payment_ingest` sync logic |
| 5 | `wbom_cash_transactions` | `approved_at` | In production, not in migrations/conftest | `payment_ingest` sync logic |

### 🟠 HIGH (4) — Type mismatches between production and conftest

| # | Table | Column | Production | conftest | Risk |
|---|-------|--------|-----------|----------|------|
| 1 | `fpe_cash_transactions` | `employee_id` | bigint YES | INT NOT NULL | Tests may mask nullable issues |
| 2 | `fpe_cash_transactions` | `payout_method` | text YES | VARCHAR(20) NOT NULL | Tests may mask nullable issues |
| 3 | `fpe_cash_transactions` | `accounting_period` | text YES | VARCHAR(7) NOT NULL | Tests may mask nullable issues |
| 4 | `fpe_income_transactions` | (entire table) | ✅ Exists | ❌ Not in conftest | Income transaction paths untestable |

### 🟡 MEDIUM (6) — conftest has columns not in production or vice versa

| # | Table | Column | Issue |
|---|-------|--------|-------|
| 1 | `wbom_cash_transactions` | `is_reversal` | In conftest, NOT in production (only `is_reversed` exists) |
| 2 | `wbom_cash_transactions` | `reversal_reason` | In conftest, NOT in production |
| 3 | `fazle_payment_drafts` | `admin_reply` | In conftest, NOT in production |
| 4 | `fazle_payment_drafts` | `source_bridge` | In conftest, NOT in production |
| 5 | `fpe_employee_ledger` | `id` | In production (BIGSERIAL PK), conftest uses composite PK |
| 6 | `wbom_staging_payments` | `approved_at`/`created_at` | Production: timestamp (no tz), conftest: TIMESTAMPTZ |

### 🟢 LOW (3) — Minor type differences

| # | Table | Column | Issue |
|---|-------|--------|-------|
| 1 | `fazle_payment_drafts` | `gross_amount`/`food_bill`/`conveyance`/`advance_deduction` | numeric vs FLOAT |
| 2 | `fpe_accounting_audit_logs` | `entity_type`/`action` | text vs VARCHAR(50) |
| 3 | `wbom_cash_transactions` | `source` | varchar(30) vs TEXT |

---

## Root Cause Analysis

| Root Cause | Affected Items |
|------------|---------------|
| **Manual ALTER TABLE without tracking migration** | `fpe_cash_transactions.deleted_at/deleted_by/updated_at`, `wbom_cash_transactions.approved_by/approved_at` |
| **conftest.py written independently of production schema** | `wbom_cash_transactions.is_reversal/reversal_reason`, `fazle_payment_drafts.admin_reply/source_bridge` |
| **FPE migration files not integrated into main migration runner** | `fpe_income_transactions` missing from conftest, `fpe_cash_transactions` phantom columns |
| **Type drift over time** | `employee_id` INT vs bigint, `payout_method` VARCHAR vs text |

---

## Remediation Plan (Proposed — Requires Owner Approval)

### Phase 1: Create Missing Migration Files (CRITICAL)

Create tracked migration files for all phantom columns:

1. **Migration: Add soft-delete columns to `fpe_cash_transactions`**
   ```sql
   ALTER TABLE fpe_cash_transactions
       ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ,
       ADD COLUMN IF NOT EXISTS deleted_by TEXT,
       ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW();
   ```

2. **Migration: Add approval columns to `wbom_cash_transactions`**
   ```sql
   ALTER TABLE wbom_cash_transactions
       ADD COLUMN IF NOT EXISTS approved_by VARCHAR(80),
       ADD COLUMN IF NOT EXISTS approved_at TIMESTAMPTZ;
   ```

3. **Migration: Fix `wbom_cash_transactions.whatsapp_message_id` type**
   - Production: `varchar(100)`, conftest: `INT`
   - Decision needed: Should we keep varchar or alter to INT?

### Phase 2: Align conftest.py with Production (HIGH)

1. Add `fpe_income_transactions` table to conftest
2. Remove `is_reversal` and `reversal_reason` from conftest `wbom_cash_transactions` (not in production)
3. Remove `admin_reply` and `source_bridge` from conftest `fazle_payment_drafts` (not in production)
4. Fix type mismatches: `employee_id` → BIGINT, `payout_method` → TEXT, `accounting_period` → TEXT
5. Add `id` column to conftest `fpe_employee_ledger`

### Phase 3: Verify Alignment (MEDIUM)

1. Run a schema diff script: `production vs conftest` for all 8 financial tables
2. Run full test suite (110 tests) — must still pass
3. Document any remaining intentional differences

### Phase 4: Certification

1. Create `SCHEMA-CERTIFICATION.md` documenting that Production DB = Migration Files = conftest.py
2. Add schema certification check to CI/CD pipeline (if applicable)
3. Lock: No future ALTER TABLE without a tracked migration file

---

## Success Criteria

| # | Criterion | Status |
|---|-----------|--------|
| 1 | All production columns have a tracked migration | ❌ 5 phantom columns |
| 2 | conftest.py matches production schema for all 8 financial tables | ❌ Multiple mismatches |
| 3 | Fresh DB restore from migrations produces working schema | ❌ Will crash |
| 4 | All 110 tests pass after alignment | ⏳ To verify after remediation |
| 5 | Schema certification document created | ⏳ Pending |

---

## Sign-off

| Role | Status |
|------|--------|
| Auditor | ✅ Audit complete |
| Owner Decision | ⏳ Pending — approve remediation plan? |