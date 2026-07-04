# Sprint-3B Owner Report — Draft Approval → Canonical Transaction → Ledger

**Date:** 2026-06-28  
**Sprint:** 3B — Financial Approval Layer  
**Status:** ✅ Complete — All 35 Sprint-3B tests passed, 0 regressions  
**Success Metric:** Approved Draft → Single Canonical Transaction → Correct Ledger → Complete Audit

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Files Created / Modified](#2-files-created--modified)
3. [Database Migration (022)](#3-database-migration-022)
4. [Draft State Machine](#4-draft-state-machine)
5. [APPROVED Workflow — Step by Step](#5-approved-workflow--step-by-step)
6. [EDIT Workflow](#6-edit-workflow)
7. [REJECT Workflow](#7-reject-workflow)
8. [Canonical Transaction Integration](#8-canonical-transaction-integration)
9. [Ledger Update Path](#9-ledger-update-path)
10. [Idempotency Guarantee](#10-idempotency-guarantee)
11. [Audit Trail](#11-audit-trail)
12. [WhatsApp Compatibility](#12-whatsapp-compatibility)
13. [Admin Command Interface](#13-admin-command-interface)
14. [Protected Components — Zero Modification](#14-protected-components--zero-modification)
15. [Test Results](#15-test-results)
16. [Production Deployment Checklist](#16-production-deployment-checklist)

---

## 1. Architecture Overview

Sprint-3B implements the **Financial Approval Layer** that sits between Sprint-3A drafts (status=`pending`) and the canonical accounting pipeline (`create_transaction()` → `_upsert_ledger()`).

```
Sprint-3A Draft (pending)
        │
        ▼
┌─────────────────────────────┐
│   Admin Decision (Sprint-3B) │
│   APPROVED / EDIT / REJECT   │
└──────────┬──────────────────┘
           │
     ┌─────┴─────┐
     │ APPROVED  │──→ Lock Draft (SELECT FOR UPDATE)
     │           │──→ Build Accountant Message (parser-compatible)
     │           │──→ Call create_transaction() [PROTECTED]
     │           │      └── _upsert_ledger() [PROTECTED, called internally]
     │           │──→ Audit: approved, transaction_created, ledger_updated, accountant_forwarded
     │           │──→ Finalize: status='completed', save transaction_id + txn_ref
     │           │
     │ EDIT     │──→ Version increment
     │           │──→ Save before_state / after_state (JSONB)
     │           │──→ Audit: edited
     │           │──→ Draft stays pending (admin must APPROVED after edit)
     │           │
     │ REJECT   │──→ status='rejected', save reason
     │           │──→ NO transaction, NO ledger
     │           │──→ Audit: rejected
     └───────────┘
```

**Key Principle:** Employee Request is NEVER a Transaction. Only an Approved Draft creates a canonical transaction.

---

## 2. Files Created / Modified

### Created (New Files)

| File | Purpose |
|------|---------|
| [`core/db/migrations/022_sprint3b_draft_approval.sql`](core/db/migrations/022_sprint3b_draft_approval.sql) | Database migration: Sprint-3B columns + audit table |
| [`core/modules/draft_approval/__init__.py`](core/modules/draft_approval/__init__.py) | Main Sprint-3B module — all 16 steps |
| [`core/tests/unit/test_sprint3b_draft_approval.py`](core/tests/unit/test_sprint3b_draft_approval.py) | 15 acceptance tests (35 test cases) |

### Modified (Additive Only)

| File | Changes |
|------|---------|
| [`core/modules/admin_commands/__init__.py`](core/modules/admin_commands/__init__.py) | Added APPROVED/DREDIT/DREJECT commands + handlers |
| [`core/modules/message_router/__init__.py`](core/modules/message_router/__init__.py) | Updated `_resolve_forward_target()` to route APPROVED to accountant |
| [`core/tests/conftest.py`](core/tests/conftest.py) | Added fpe_* tables, Sprint-3B columns, seed fixtures |

---

## 3. Database Migration (022)

**File:** [`core/db/migrations/022_sprint3b_draft_approval.sql`](core/db/migrations/022_sprint3b_draft_approval.sql)

### Columns Added to `fazle_payment_drafts`

| Column | Type | Default | Purpose |
|--------|------|---------|---------|
| `transaction_id` | BIGINT | NULL | FK to fpe_cash_transactions.id |
| `txn_ref` | TEXT | NULL | Canonical transaction reference |
| `rejected_reason` | TEXT | NULL | Rejection reason |
| `reviewed_by` | TEXT | NULL | Admin phone who reviewed |
| `reviewed_at` | TIMESTAMPTZ | NULL | Review timestamp |
| `version` | INT | 0 | Edit version counter |
| `before_state` | JSONB | NULL | Pre-edit snapshot |
| `after_state` | JSONB | NULL | Post-edit snapshot |
| `editor` | TEXT | NULL | Admin who edited |
| `completed_at` | TIMESTAMPTZ | NULL | Completion timestamp |

### New Table: `fazle_draft_audit_log`

```sql
CREATE TABLE fazle_draft_audit_log (
    id            BIGSERIAL PRIMARY KEY,
    draft_id      INT NOT NULL REFERENCES fazle_payment_drafts(id) ON DELETE CASCADE,
    event         TEXT NOT NULL,
    before_state  JSONB,
    after_state   JSONB,
    performed_by  TEXT,
    reason        TEXT,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);
```

### Indexes Created

- `idx_payment_drafts_txn_ref` — on `txn_ref` (WHERE NOT NULL)
- `idx_payment_drafts_transaction_id` — on `transaction_id` (WHERE NOT NULL)
- `idx_draft_audit_draft` — on `fazle_draft_audit_log(draft_id)`
- `idx_draft_audit_event` — on `fazle_draft_audit_log(event)`

---

## 4. Draft State Machine

```
                    ┌─────────┐
         Sprint-3A  │ pending │
        creates ──→ │         │
                    └────┬────┘
                         │
              ┌──────────┼──────────┐
              │          │          │
              ▼          ▼          ▼
         APPROVED    EDIT      REJECT
              │          │          │
              ▼          ▼          ▼
         ┌─────────┐ ┌────────┐ ┌──────────┐
         │approved │ │pending │ │rejected │
         │(locked) │ │(v+1)  │ │(no txn) │
         └────┬────┘ └────────┘ └──────────┘
              │           │
              ▼           └──→ admin can APPROVED
         ┌──────────┐
         │completed │
         │(txn_id)  │
         └──────────┘

         ┌────────┐
         │expired │ ← 24h TTL elapsed (cannot approve)
         └────────┘
```

**State Transitions:**
- `pending` → `approved` → `completed` (APPROVED workflow)
- `pending` → `pending` (EDIT — version increment, stays pending)
- `pending` → `rejected` (REJECT workflow)
- `pending` → `expired` (24h TTL)

---

## 5. APPROVED Workflow — Step by Step

**Function:** [`approve_draft()`](core/modules/draft_approval/__init__.py:395)

### Step 1: Draft Retrieval
- `retrieve_draft(draft_id)` fetches the draft with all Sprint-3B columns
- Returns `None` if not found

### Step 2: State Validation
- `is_draft_pending(draft)` — only `status == 'pending'` can be approved
- `is_draft_expired(draft)` — 24h TTL check
- `is_draft_already_processed(draft)` — idempotency guard (transaction_id or txn_ref present)

### Step 5a: Row-Level Lock
- `SELECT ... FOR UPDATE` prevents concurrent approvals
- Double-checks status and transaction_id inside the transaction

### Step 6: Build Accountant Message
- `build_accountant_message(draft, amount, method)` produces parser-compatible format:
  ```
  ID: 01811111111 Test Guard Karim 01811111111(B) 2000/-
  ```

### Step 7 & 8: Canonical Transaction
- `create_canonical_transaction(draft, amount, method, admin_phone)` calls:
  - `_resolve_fpe_employee_id()` — maps wbom employee → fpe_employees.id
  - `create_transaction(req)` — the PROTECTED canonical function
  - `create_transaction()` internally calls `_upsert_ledger()`

### Step 10: Draft Finalization
- `UPDATE fazle_payment_drafts SET status='completed', transaction_id=..., txn_ref=..., completed_at=NOW()`

### Step 14: Audit Events
- `approved` — before/after state
- `transaction_created` — transaction_id + txn_ref
- `ledger_updated` — via create_transaction
- `accountant_forwarded` — accountant message

---

## 6. EDIT Workflow

**Function:** [`edit_draft()`](core/modules/draft_approval/__init__.py:578)

- Only pending drafts can be edited
- Version increments: `version = version + 1`
- `before_state` saved as JSONB snapshot of current draft
- `after_state` saved as JSONB with new values
- `editor` field records who edited
- Draft stays `pending` — admin must APPROVED after edit
- Audit event: `edited` with before/after state

**Command:** `DREDIT <id> <amount> <method> [payout=<phone>]`

---

## 7. REJECT Workflow

**Function:** [`reject_draft()`](core/modules/draft_approval/__init__.py:687)

- Only pending drafts can be rejected
- Sets `status='rejected'`, `rejected_reason`, `reviewed_by`, `reviewed_at`
- **NO transaction created**
- **NO ledger updated**
- Audit event: `rejected` with before/after state

**Command:** `DREJECT <id> [reason]`

---

## 8. Canonical Transaction Integration

**Function:** [`create_canonical_transaction()`](core/modules/draft_approval/__init__.py:293)

### How create_transaction() is Called

```python
req = TransactionCreateRequest(
    fpe_wa_message_id=wa_msg_id_int,      # deterministic: sha256("draft-<id>")
    employee_id=fpe_employee_id,            # resolved from fpe_employees
    employee_name_raw=draft["employee_name"],
    amount=Decimal(str(amount)),
    payout_phone=payout_phone,
    payout_method=PayoutMethod(method),
    txn_date=date.today(),
    txn_category=txn_category,             # mapped from draft purpose
    source_message_text=source_msg,
    created_by=f"admin:{admin_phone}",
)

txn_row = await create_transaction(req)    # PROTECTED — called as-is
```

### Employee ID Resolution

`_resolve_fpe_employee_id()` resolves `wbom_employees.employee_id` → `fpe_employees.id`:
1. Phone match on `fpe_employees.primary_phone`
2. Phone match on `fpe_employees.employee_id_phone`
3. Name match on `fpe_employees.name_normalized`
4. Fallback: `match_or_create_employee()` (PROTECTED)

### Category Mapping

| Draft Purpose | TxnCategory |
|---------------|-------------|
| advance | advance |
| salary | salary |
| food_bill | deduction |
| conveyance | deduction |
| emergency | advance |
| escort_payment | salary |
| (default) | advance |

---

## 9. Ledger Update Path

**`_upsert_ledger()` is called INSIDE `create_transaction()` — NOT directly by Sprint-3B.**

```
approve_draft()
  └── create_canonical_transaction()
        └── create_transaction()          [PROTECTED]
              ├── INSERT fpe_cash_transactions
              ├── INSERT fpe_accounting_audit_logs
              └── _upsert_ledger()         [PROTECTED — called internally]
                    └── INSERT/UPDATE fpe_employee_ledger
```

**Ledger columns updated by category:**
- `advance` → `total_advance`
- `salary`, `bonus` → `total_paid`
- `deduction`, `correction` → `total_paid`

**Employee balance read:** [`get_employee_balance()`](core/modules/draft_approval/__init__.py:799) reads directly from `fpe_employee_ledger` — no manual calculation.

---

## 10. Idempotency Guarantee

### One Draft = One Transaction

**Mechanism:** `create_transaction()` uses `txn_ref = sha256(wa_message_id + employee_id + amount + period + method)`

Sprint-3B passes a **deterministic** `fpe_wa_message_id`:
```python
wa_message_id_str = f"draft-{draft['id']}"
wa_msg_id_int = int(sha256(wa_message_id_str.encode()).hexdigest()[:12], 16)
```

This means:
- Same draft + same amount + same method → same `txn_ref`
- `create_transaction()` checks for existing `txn_ref` and returns the existing row
- Duplicate approvals are safe — the second call returns the first transaction

### Additional Guards

1. **State check:** `is_draft_already_processed()` checks `transaction_id` and `txn_ref`
2. **Row-level lock:** `SELECT FOR UPDATE` prevents concurrent approvals
3. **Status check:** After lock, re-checks `status == 'pending'` and `transaction_id IS NULL`

---

## 11. Audit Trail

### `fazle_draft_audit_log` Events

| Event | When | Before State | After State |
|-------|------|-------------|-------------|
| `created` | Draft created (Sprint-3A) | — | draft snapshot |
| `edited` | EDIT command | pre-edit snapshot | post-edit snapshot |
| `approved` | APPROVED command | pre-approval snapshot | status, txn_id, txn_ref |
| `transaction_created` | After create_transaction() | — | transaction_id, txn_ref |
| `ledger_updated` | After _upsert_ledger() | — | transaction_id, txn_ref |
| `accountant_forwarded` | After accountant message built | — | accountant_msg |
| `rejected` | REJECT command | pre-reject snapshot | status, reason |
| `expired` | 24h TTL elapsed | — | status=expired |

### `fpe_accounting_audit_logs` (Existing — PROTECTED)

`create_transaction()` writes its own audit row:
```sql
INSERT INTO fpe_accounting_audit_logs
    (entity_type, entity_id, action, after_state, performed_by)
VALUES ('transaction', $1, 'create', $2, $3)
```

---

## 12. WhatsApp Compatibility

### Accountant Message Format

```
ID: <payout_phone> <employee_name> <payout_phone>(<method_code>) <amount>/-
```

### Method Codes (Parser-Compatible)

| Method | Code |
|--------|------|
| bkash | B |
| nagad | N |
| cash | cash |
| rocket | R |
| bank | bank |

### Parser Verification (Test-15)

The accountant message is verified to be parseable by `parse_message()`:
```python
msg = build_accountant_message(draft, 2000.0, "bkash")
# msg = "ID: 01811111111 Test Guard Karim 01811111111(B) 2000/-"
result = parse_message(msg)
assert result.message_type.value == "payment"
```

### Phone Normalization

- `+880XXXXXXXXX` → `0XXXXXXXXX`
- `880XXXXXXXXX` → `0XXXXXXXXX`
- Ensures parser receives `01XXXXXXXXX` format

---

## 13. Admin Command Interface

### New Commands (Sprint-3B)

| Command | Format | Handler | Returns |
|---------|--------|---------|---------|
| APPROVED | `APPROVED <id> <amount> <method>` | `_cmd_approved()` | `(confirm, accountant_msg)` |
| DREDIT | `DREDIT <id> <amount> <method> [payout=<phone>]` | `_cmd_dredit()` | confirm text |
| DREJECT | `DREJECT <id> [reason]` | `_cmd_dreject()` | confirm text |

### RBAC Mapping

| Sprint-3B Command | RBAC Role |
|-------------------|-----------|
| APPROVED | `paid` (same financial action) |
| DREDIT | `edit` |
| DREJECT | `reject` |

### Message Router Integration

`_resolve_forward_target()` in [`message_router/__init__.py`](core/modules/message_router/__init__.py:700) routes APPROVED to the accountant:
```python
if t.startswith(("paid", "advance", "approved")):
    return settings.accountant_phone or None
```

### Existing Commands (Unchanged)

| Command | Status |
|---------|--------|
| APPROVE | ✅ Unchanged (operates on fazle_draft_replies) |
| REJECT | ✅ Unchanged |
| EDIT | ✅ Unchanged |
| PAID | ✅ Unchanged (legacy escort payment via finalize_payment) |
| ADVANCE | ✅ Unchanged |

---

## 14. Protected Components — Zero Modification

| Component | File | Status |
|-----------|------|--------|
| `create_transaction()` | [`accounting.py:30`](core/modules/fazle_payroll_engine/accounting.py:30) | ✅ Called as-is, NOT modified |
| `_upsert_ledger()` | [`accounting.py:190`](core/modules/fazle_payroll_engine/accounting.py:190) | ✅ Called internally by create_transaction |
| `accounting_worker()` | — | ✅ Untouched |
| `parse_message()` | [`parser.py:199`](core/modules/fazle_payroll_engine/parser.py:199) | ✅ Untouched |
| WhatsApp Admin ↔ Accountant Flow | [`message_router/__init__.py`](core/modules/message_router/__init__.py) | ✅ Untouched (additive only) |
| Existing Payroll Engine | [`fazle_payroll_engine/`](core/modules/fazle_payroll_engine/) | ✅ Untouched |
| Existing Ledger Calculation | `fpe_employee_ledger` | ✅ Updated only via _upsert_ledger() |
| Existing Employee Identity Rules | [`employee.py`](core/modules/fazle_payroll_engine/employee.py) | ✅ Called as-is via match_or_create_employee() |

**All changes are ADDITIVE.** No existing function signature was changed. No existing table structure was altered (only columns added).

---

## 15. Test Results

### Sprint-3B Acceptance Tests

**File:** [`core/tests/unit/test_sprint3b_draft_approval.py`](core/tests/unit/test_sprint3b_draft_approval.py)

| Test | Class | Test Cases | Status |
|------|-------|-----------|--------|
| Test-1 | Draft Retrieval (STEP 1) | 3 | ✅ Pass |
| Test-2 | State Validation (STEP 2) | 3 | ✅ Pass |
| Test-3 | Admin Command Processing (STEP 3) | 2 | ✅ Pass |
| Test-4 | EDIT Workflow (STEP 4) | 3 | ✅ Pass |
| Test-5 | APPROVED Workflow (STEP 5) | 2 | ✅ Pass |
| Test-6 | Accountant Forward (STEP 6) | 2 | ✅ Pass |
| Test-7 | Canonical Transaction (STEP 7) | 2 | ✅ Pass |
| Test-8 | Ledger Update (STEP 8) | 1 | ✅ Pass |
| Test-9 | Employee Balance (STEP 9) | 2 | ✅ Pass |
| Test-10 | Draft Finalization (STEP 10) | 1 | ✅ Pass |
| Test-11 | Reject Workflow (STEP 11) | 3 | ✅ Pass |
| Test-12 | Draft Expiry (STEP 12) | 2 | ✅ Pass |
| Test-13 | Idempotency (STEP 13) | 2 | ✅ Pass |
| Test-14 | Audit Requirements (STEP 14) | 4 | ✅ Pass |
| Test-15 | WhatsApp Compatibility (STEP 15) | 3 | ✅ Pass |
| **Total** | | **35** | **✅ All Pass** |

### Regression Suite

| Test Suite | Tests | Status |
|-----------|-------|--------|
| Sprint-3B (`test_sprint3b_draft_approval.py`) | 35 | ✅ All Pass |
| Sprint-3A (`test_sprint3a_employee_conversation.py`) | 42 | ✅ All Pass |
| Payment Workflow (`test_payment_workflow.py`) | 23 | ✅ All Pass |
| Draft Reply (`test_draft_reply.py`) | 10 | ✅ All Pass |
| **Total** | **110** | **✅ All Pass, 0 Regressions** |

### Pre-existing Failures (Unrelated to Sprint-3B)

26 failures in `test_backup_pipeline.py`, `test_llm_provider_order.py`, `test_phase12_concurrency.py`, `test_pipeline_phases.py`, `test_recruitment_ai_restricted.py`, `test_chat_exact_db_tools.py` — all pre-existing, none related to Sprint-3B changes.

---

## 16. Production Deployment Checklist

### Pre-Deployment

- [x] Migration 022 created and tested
- [x] `draft_approval` module created with all 16 steps
- [x] APPROVED/DREDIT/DREJECT commands added to admin_commands
- [x] Message router updated to forward APPROVED to accountant
- [x] conftest.py updated with fpe_* tables + Sprint-3B columns
- [x] 15 acceptance tests written and passing
- [x] Regression suite passes (110 tests, 0 regressions)
- [x] Protected components verified unmodified
- [x] All changes are additive

### Deployment Steps

1. **Apply Migration 022:**
   ```bash
   psql -f core/db/migrations/022_sprint3b_draft_approval.sql
   ```

2. **Deploy Updated Code:**
   - `core/modules/draft_approval/__init__.py` (new)
   - `core/modules/admin_commands/__init__.py` (modified)
   - `core/modules/message_router/__init__.py` (modified)

3. **Verify:**
   ```bash
   python3 -m pytest tests/unit/test_sprint3b_draft_approval.py -v
   ```

4. **Smoke Test:**
   - Send `APPROVED <draft_id> <amount> <method>` from admin phone
   - Verify draft status → `completed`
   - Verify `fpe_cash_transactions` has new row
   - Verify `fpe_employee_ledger` updated
   - Verify `fazle_draft_audit_log` has 4 events

### Post-Deployment Monitoring

- Monitor `fazle_draft_audit_log` for audit completeness
- Monitor `fpe_cash_transactions` for duplicate txn_ref (should never happen)
- Monitor draft expiry — pending drafts older than 24h should be expired

---

## Summary

**Sprint-3B is complete.** The Financial Approval Layer is fully operational:

- ✅ **Approved Draft → Single Canonical Transaction** — via `create_transaction()` with deterministic idempotency
- ✅ **Correct Ledger** — via `_upsert_ledger()` called internally by `create_transaction()`
- ✅ **Complete Audit** — 4 audit events per approval in `fazle_draft_audit_log` + existing `fpe_accounting_audit_logs`
- ✅ **Zero Regressions** — 110 tests pass, all protected components unmodified
- ✅ **All Changes Additive** — no existing function signatures changed, no existing table structures altered

**Success Metric Achieved:** Approved Draft → Single Canonical Transaction → Correct Ledger → Complete Audit