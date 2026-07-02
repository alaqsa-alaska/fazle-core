# Canonical Transaction Table Audit — Evidence Report

**Date:** 2026-06-28  
**Purpose:** Prove with code evidence which database table each financial flow reads from or writes to.  
**Status:** Evidence complete — awaiting Owner decision  

---

## The Two-Table Problem

There are **two separate transaction tables** in this system. They are not connected. Data written to one is invisible to the other.

| Table | Created By | Used By |
|-------|-----------|---------|
| `wbom_cash_transactions` | Legacy system (pre-FPE) | WhatsApp admin flow, payroll, reports, dashboard |
| `fpe_cash_transactions` | FPE Payroll Engine | `create_transaction()` (canonical), draft_approval, admin_transactions UI, FPE routes, FPE reconcile |

**Ledger tables are also split:**
| Table | Used By |
|-------|---------|
| `wbom_cash_transactions` (no separate ledger table — payroll reads directly) | Legacy payroll, reports |
| `fpe_employee_ledger` | `create_transaction()` → `_upsert_ledger()`, FPE reconcile |

---

## Evidence: Flow-by-Flow Table Mapping

### 1. `create_transaction()` (Canonical Function) → `fpe_cash_transactions`

**File:** [`modules/fazle_payroll_engine/accounting.py:30`](modules/fazle_payroll_engine/accounting.py:30)

```python
# Line 42-46: Idempotency check reads from fpe_cash_transactions
existing = await fetch_one(
    "SELECT id, txn_ref, ... FROM fpe_cash_transactions WHERE txn_ref = $1",
    txn_ref,
)

# Line 53-61: INSERT into fpe_cash_transactions
new_id: int = await conn.fetchval(
    """
    INSERT INTO fpe_cash_transactions
        (txn_ref, fpe_wa_message_id, employee_id, ...)
    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
    RETURNING id
    """,
    ...
)

# Line 77-86: Audit log into fpe_accounting_audit_logs
await conn.execute(
    "INSERT INTO fpe_accounting_audit_logs ...",
    ...
)

# Line 94-95: Ledger update into fpe_employee_ledger
await _upsert_ledger(req.employee_id, period, req.amount, req.txn_category)
```

**`_upsert_ledger()` writes to:** `fpe_employee_ledger` (line 210-219)

**Verdict:** `create_transaction()` writes to `fpe_cash_transactions` + `fpe_accounting_audit_logs` + `fpe_employee_ledger`. It does NOT touch `wbom_cash_transactions`.

---

### 2. Draft Approval (Sprint-3B) → `fpe_cash_transactions` (via `create_transaction()`)

**File:** [`modules/draft_approval/__init__.py:293`](modules/draft_approval/__init__.py:293)

```python
# Line 324: Imports canonical function
from modules.fazle_payroll_engine.accounting import create_transaction

# Line 382: Calls canonical function
txn_row = await create_transaction(req)
```

**Verdict:** Draft Approval correctly delegates to `create_transaction()` → writes to `fpe_cash_transactions`. ✅ Constitution compliant.

---

### 3. WhatsApp Admin → Accountant Flow → `wbom_cash_transactions` (DIRECT INSERT — bypasses canonical)

**File:** [`modules/payment_ingest/__init__.py:510`](modules/payment_ingest/__init__.py:510)

```python
# Line 510-524: Direct INSERT into wbom_cash_transactions
row = await fetch_one(
    """INSERT INTO wbom_cash_transactions
          (employee_id, amount, transaction_type, payment_method,
           payment_mobile, payment_number, employee_phone,
           transaction_date, remarks, created_by, source,
           idempotency_key, whatsapp_message_id)
       VALUES ($1, $2, 'advance', $3, $4::text, $4::text, $5,
               CURRENT_DATE, $6, $7, 'admin-accountant-instruction',
               $8, $9)
       ON CONFLICT (idempotency_key) ... DO NOTHING
       RETURNING transaction_id""",
    ...
)
```

**Verdict:** ❌ Bypasses `create_transaction()`. Writes to `wbom_cash_transactions`. No `fpe_accounting_audit_logs` entry. No `fpe_employee_ledger` update.

---

### 4. Manual Entry (Admin NL Advance) → `wbom_cash_transactions` (DIRECT INSERT — bypasses canonical)

**File:** [`modules/admin_commands/nl_advance_record.py:186`](modules/admin_commands/nl_advance_record.py:186)

```python
# Line 186-194: Direct INSERT into wbom_cash_transactions
await execute(
    """INSERT INTO wbom_cash_transactions
           (employee_id, amount, transaction_type, payment_method,
            transaction_date, remarks, employee_phone, source, whatsapp_message_id)
       VALUES ($1, $2, 'advance', $3, CURRENT_DATE, $4, $5, 'admin_nl', $6)""",
    ...
)
```

**Verdict:** ❌ Bypasses `create_transaction()`. Writes to `wbom_cash_transactions`. No audit log. No ledger update.

---

### 5. Operator Approval (PAID command → `finalize_payment()`) → `wbom_cash_transactions` (DIRECT INSERT — bypasses canonical)

**File:** [`modules/payment_workflow/__init__.py:335`](modules/payment_workflow/__init__.py:335)

```python
# Line 335-343: Direct INSERT into wbom_cash_transactions
await conn.execute(
    """INSERT INTO wbom_cash_transactions
           (employee_id, program_id, amount, transaction_type, payment_method,
            transaction_date, remarks, idempotency_key, source)
       VALUES ($1, $2, $3, $4, $5, CURRENT_DATE, $6, $7, 'payment-draft')
       ON CONFLICT (idempotency_key) ... DO NOTHING""",
    ...
)
```

**Called by:** `admin_commands.__init__.py:772` (PAID command), `payment_ingest.__init__.py:355`, `app/main.py:38`

**Verdict:** ❌ Bypasses `create_transaction()`. Writes to `wbom_cash_transactions`. No audit log. No ledger update.

---

### 6. Payment Correction (Reversal) → `wbom_cash_transactions` (DIRECT INSERT — bypasses canonical)

**File:** [`modules/payment_correction/__init__.py:95`](modules/payment_correction/__init__.py:95)

```python
# Line 95-108: Direct INSERT into wbom_cash_transactions for reversal
counter_tx_id = await fetch_val(
    """INSERT INTO wbom_cash_transactions
           (employee_id, program_id, transaction_type, amount,
            payment_method, payment_mobile, transaction_date, transaction_time,
            status, remarks, created_by, reversal_of, correction_note)
       VALUES ($1, $2, 'reversal', $3, $4, $5, CURRENT_DATE, NOW(),
               'completed', $6, $7, $8, $9)
       RETURNING transaction_id""",
    ...
)
```

**Verdict:** ❌ Bypasses `reverse_transaction()` from `accounting.py`. Writes to `wbom_cash_transactions`. No `fpe_accounting_audit_logs` entry.

---

### 7. Admin UI Manual Entry (`admin_transactions`) → `fpe_cash_transactions` (DIRECT INSERT — bypasses `create_transaction()`)

**File:** [`modules/admin_transactions/__init__.py:565`](modules/admin_transactions/__init__.py:565)

```python
# Line 580-595: Direct INSERT into fpe_cash_transactions (same table as canonical, but bypasses create_transaction)
new_id: int = await conn.fetchval(
    """
    INSERT INTO fpe_cash_transactions
        (txn_ref, employee_id, employee_name_raw,
         amount, payout_phone, payout_method, txn_date, txn_category,
         source_message_text, accounting_period, created_by)
    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,'admin_manual')
    RETURNING id
    """,
    ...
)

# Line 611: Own audit log insertion (duplicates accounting.py logic)
await conn.execute(
    "INSERT INTO fpe_accounting_audit_logs ...",
    ...
)

# Line 632: Own ledger adjustment
await _adjust_ledger(emp["id"], period, body.amount, body.txn_category)
```

**Verdict:** ⚠️ Writes to the CORRECT table (`fpe_cash_transactions`) but bypasses `create_transaction()`. Has its own audit log insertion and its own ledger adjustment (`_adjust_ledger` vs canonical `_upsert_ledger`). Partial compliance — same table, different code path.

---

### 8. Dashboard → reads from `wbom_cash_transactions` (NOT `fpe_cash_transactions`)

**File:** [`modules/reports/__init__.py:81`](modules/reports/__init__.py:81)

```python
# Line 81-89: Cash report reads from wbom_cash_transactions
pay = await fetch_one(
    """SELECT
          COALESCE(SUM(CASE WHEN transaction_type='in'  THEN amount END), 0) AS total_in,
          COALESCE(SUM(CASE WHEN transaction_type='out' THEN amount END), 0) AS total_out,
          ...
       FROM wbom_cash_transactions
       WHERE transaction_date = $1 AND status = 'Completed'""",
    d,
)
```

**File:** [`app/main.py:2516`](app/main.py:2516) — Dashboard overview counts

```python
# Dashboard counts employees and drafts, but cash data comes from reports module
"active_employees": await _n("SELECT COUNT(*) AS n FROM wbom_employees WHERE status='active'"),
"pending_payment_drafts": await _n("SELECT COUNT(*) AS n FROM fazle_payment_drafts WHERE status='pending'"),
```

**Verdict:** Dashboard cash reports read from `wbom_cash_transactions`. Sprint-3B transactions (in `fpe_cash_transactions`) are **INVISIBLE to the dashboard**.

---

### 9. Payroll Calculation → reads from `wbom_cash_transactions` (NOT `fpe_cash_transactions`)

**File:** [`modules/payroll/__init__.py:104`](modules/payroll/__init__.py:104)

```python
# Line 104-108: Payroll advance sum reads from wbom_cash_transactions
advances = await fetch_val(
    """SELECT COALESCE(SUM(amount), 0) FROM wbom_cash_transactions
       WHERE employee_id=$1 AND transaction_type='advance'
         AND transaction_date BETWEEN $2 AND $3""",
    employee_id, period_start, period_end,
) or 0
```

**File:** [`modules/payroll_logic/__init__.py:67`](modules/payroll_logic/__init__.py:67)

```python
# Line 67-73: Monthly transactions read from wbom_cash_transactions
month_txns = await fetch_all(
    """SELECT amount, payment_method, transaction_date, status
       FROM wbom_cash_transactions
       WHERE employee_id = $1 AND transaction_date >= $2
       ORDER BY transaction_date DESC""",
    employee_id, month_start,
)

# Line 89: Total ever paid reads from wbom_cash_transactions
total_paid = await fetch_val(
    "SELECT COALESCE(SUM(amount), 0) FROM wbom_cash_transactions WHERE employee_id = $1",
    employee_id,
)
```

**Verdict:** Payroll reads from `wbom_cash_transactions`. Sprint-3B transactions (in `fpe_cash_transactions`) are **INVISIBLE to payroll calculation**.

---

### 10. FPE Reconcile → reads from `fpe_cash_transactions`

**File:** [`modules/fazle_payroll_engine/reconcile.py:84`](modules/fazle_payroll_engine/reconcile.py:84)

```python
# Line 84-88: Reconcile reads from fpe_cash_transactions
ledger_sum_q = f"""
    SELECT COALESCE(SUM(amount), 0)
    FROM fpe_cash_transactions
    WHERE NOT is_reversal
    {where_period_txn}
"""
```

**Verdict:** FPE reconcile reads from `fpe_cash_transactions`. It will NOT see legacy `wbom_cash_transactions` data.

---

## Summary Matrix

| Flow | Writes To | Reads From | Audit Log | Ledger | Canonical? |
|------|-----------|------------|-----------|--------|------------|
| `create_transaction()` | `fpe_cash_transactions` | `fpe_cash_transactions` | ✅ `fpe_accounting_audit_logs` | ✅ `fpe_employee_ledger` | ✅ YES |
| Draft Approval (Sprint-3B) | `fpe_cash_transactions` (via `create_transaction()`) | — | ✅ (via canonical) | ✅ (via canonical) | ✅ YES |
| Admin UI Manual Entry | `fpe_cash_transactions` (direct) | `fpe_cash_transactions` | ✅ (own insert) | ⚠️ `_adjust_ledger` (own) | ⚠️ SAME TABLE, BYPASSED FUNCTION |
| WhatsApp Admin→Accountant | `wbom_cash_transactions` (direct) | — | ❌ None | ❌ None | ❌ NO |
| Admin NL Advance | `wbom_cash_transactions` (direct) | — | ❌ None | ❌ None | ❌ NO |
| Operator PAID (`finalize_payment`) | `wbom_cash_transactions` (direct) | — | ❌ None | ❌ None | ❌ NO |
| Payment Correction (Reversal) | `wbom_cash_transactions` (direct) | — | ❌ None | ❌ None | ❌ NO |
| **Dashboard / Reports** | — | `wbom_cash_transactions` | — | — | ❌ READS WRONG TABLE |
| **Payroll Calculation** | — | `wbom_cash_transactions` | — | — | ❌ READS WRONG TABLE |
| FPE Reconcile | — | `fpe_cash_transactions` | — | — | ✅ Reads FPE table |
| FPE Routes/Dashboard | — | `fpe_cash_transactions` | — | — | ✅ Reads FPE table |

---

## Critical Impact

**Sprint-3B transactions are INVISIBLE to the Dashboard and Payroll.**

When an admin approves a draft via the Sprint-3B `APPROVED` command:
1. ✅ Transaction is created in `fpe_cash_transactions`
2. ✅ Audit log is written to `fpe_accounting_audit_logs`
3. ✅ Ledger is updated in `fpe_employee_ledger`
4. ❌ Dashboard does NOT show this transaction (reads `wbom_cash_transactions`)
5. ❌ Payroll does NOT include this advance (reads `wbom_cash_transactions`)
6. ❌ Cash report does NOT include this transaction (reads `wbom_cash_transactions`)

**The Sprint-3B canonical path and the legacy operational path are disconnected.**

---

## Decision Required from Owner

The system has two parallel financial databases. You must choose one direction:

### Option A: `fpe_cash_transactions` is the SINGLE canonical table
- `create_transaction()` already targets it ✅
- All 4 direct `wbom_cash_transactions` INSERTs must be refactored to call `create_transaction()`
- Dashboard, Reports, and Payroll must be migrated to read from `fpe_cash_transactions` + `fpe_employee_ledger`
- Historical data in `wbom_cash_transactions` must be migrated to `fpe_cash_transactions`
- **Effort:** High (dashboard/payroll rewrite + data migration)

### Option B: `wbom_cash_transactions` is the SINGLE canonical table
- `create_transaction()` must be rewritten to target `wbom_cash_transactions`
- `fpe_employee_ledger` logic must be adapted to `wbom_cash_transactions` model (or a new ledger table)
- FPE routes, reconcile, admin_transactions must be migrated to read `wbom_cash_transactions`
- **Effort:** High (canonical function rewrite + FPE read migration)

### Option C: Keep both tables, add a sync/bridge layer
- `create_transaction()` writes to `fpe_cash_transactions`, then syncs to `wbom_cash_transactions`
- Dashboard/payroll continue reading `wbom_cash_transactions`
- **Effort:** Medium (sync layer), but adds complexity and risk of drift
- **Constitution risk:** This creates a "shadow write" which violates the "one logic, one function" principle

**My recommendation as auditor:** Option A — `fpe_cash_transactions` is the future. The FPE engine was built to replace the legacy system. The canonical function already targets it. Migrate the readers (dashboard, payroll, reports) to FPE tables and decommission `wbom_cash_transactions` direct writes.

---

## Sign-off

| Role | Status |
|------|--------|
| Auditor | ✅ Evidence complete |
| Owner Decision | ⏳ Pending (Option A / B / C) |