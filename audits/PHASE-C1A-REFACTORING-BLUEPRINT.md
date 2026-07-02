# Phase-C1A: Canonical Transaction Function Consolidation
## Evidence-Based Refactoring Blueprint

**Date:** 2026-06-28  
**Auditor:** Financial Architecture Refactoring Auditor  
**Status:** BLUEPRINT COMPLETE — Awaiting Owner Approval before any code changes  
**Absolute Rule:** Zero Behaviour Change. No table migration. No dashboard/payroll/report rewrite. Internal refactoring only.  

---

## Table of Contents

1. [Step-1: Fresh Runtime Audit](#step-1-fresh-runtime-audit)
2. [Step-2: Business Logic Extraction](#step-2-business-logic-extraction)
3. [Step-3: Canonical Mapping](#step-3-canonical-mapping)
4. [Step-4: Gap Analysis](#step-4-gap-analysis)
5. [Step-5: Implementation Strategy + Wrapper Design](#step-5-implementation-strategy--wrapper-design)
6. [Step-6: Risk Matrix](#step-6-risk-matrix)
7. [Step-7: Refactoring Plan / Priority Order](#step-7-refactoring-plan--priority-order)
8. [Step-8: Regression Matrix](#step-8-regression-matrix)
9. [Step-9: Owner Decision Gates](#step-9-owner-decision-gates)

---

## Step-1: Fresh Runtime Audit

### Methodology

Scanned current runtime source code (not old audit reports) for ALL `INSERT INTO` statements targeting transaction tables. Source of truth = files on disk at audit time.

### Complete Inventory of Financial Write Paths

| # | Module | Function | Target Table | Evidence (file:line) |
|---|--------|----------|-------------|---------------------|
| **W-1** | `payment_ingest` | `_finalize_admin_instruction()` | `wbom_cash_transactions` | [`payment_ingest/__init__.py:510`](modules/payment_ingest/__init__.py:510) |
| **W-2** | `admin_commands/nl_advance_record` | `intent_advance_record()` | `wbom_cash_transactions` | [`nl_advance_record.py:186`](modules/admin_commands/nl_advance_record.py:186) |
| **W-3** | `payment_workflow` | `finalize_payment()` | `wbom_cash_transactions` | [`payment_workflow/__init__.py:335`](modules/payment_workflow/__init__.py:335) |
| **W-4** | `payment_correction` | `reverse_payment()` | `wbom_cash_transactions` | [`payment_correction/__init__.py:95`](modules/payment_correction/__init__.py:95) |
| **W-5** | `admin_transactions` | `add_admin_transaction()` | `fpe_cash_transactions` | [`admin_transactions/__init__.py:590`](modules/admin_transactions/__init__.py:590) |
| **C-1** | `fazle_payroll_engine/accounting` | `create_transaction()` | `fpe_cash_transactions` | [`accounting.py:55`](modules/fazle_payroll_engine/accounting.py:55) |
| **C-2** | `fazle_payroll_engine/accounting` | `reverse_transaction()` | `fpe_cash_transactions` | [`accounting.py:133`](modules/fazle_payroll_engine/accounting.py:133) |
| **C-3** | `fazle_payroll_engine/accounting` | `create_income_transaction()` | `fpe_income_transactions` | [`accounting.py:313`](modules/fazle_payroll_engine/accounting.py:313) |
| **D-1** | `draft_approval` | `create_canonical_transaction()` | `fpe_cash_transactions` (via C-1) | [`draft_approval/__init__.py:382`](modules/draft_approval/__init__.py:382) |

### Additional Financial Write Paths (UPDATE, not INSERT)

| # | Module | Function | Target Table | Action | Evidence |
|---|--------|----------|-------------|--------|---------|
| **U-1** | `payment_correction` | `reverse_payment()` | `wbom_cash_transactions` | UPDATE `is_reversed=true` | [`payment_correction/__init__.py:89`](modules/payment_correction/__init__.py:89) |
| **U-2** | `admin_transactions` | `edit_admin_transaction()` | `fpe_cash_transactions` | UPDATE fields | [`admin_transactions/__init__.py:722`](modules/admin_transactions/__init__.py:722) |
| **U-3** | `admin_transactions` | `soft_delete_transaction()` | `fpe_cash_transactions` | UPDATE `deleted_at` | [`admin_transactions/__init__.py:788`](modules/admin_transactions/__init__.py:788) |

### Related (non-transaction) Financial Tables

| # | Module | Function | Target Table | Evidence |
|---|--------|----------|-------------|---------|
| **S-1** | `payment_ingest` | `_create_staging_row()` | `wbom_staging_payments` | [`payment_ingest/__init__.py:294`](modules/payment_ingest/__init__.py:294) |
| **R-1** | `payment_correction` | `reverse_payment()` | `fazle_payment_correction_log` | [`payment_correction/__init__.py:130`](modules/payment_correction/__init__.py:130) |
| **R-2** | `payment_correction` | `adjust_payment()` | `fazle_payment_correction_log` | [`payment_correction/__init__.py:259`](modules/payment_correction/__init__.py:259) |
| **A-1** | `admin_transactions` | `add_admin_transaction()` | `fpe_accounting_audit_logs` | [`admin_transactions/__init__.py:611`](modules/admin_transactions/__init__.py:611) |
| **A-2** | `admin_transactions` | `edit_admin_transaction()` | `fpe_accounting_audit_logs` | [`admin_transactions/__init__.py:729`](modules/admin_transactions/__init__.py:729) |
| **A-3** | `admin_transactions` | `soft_delete_transaction()` | `fpe_accounting_audit_logs` | [`admin_transactions/__init__.py:795`](modules/admin_transactions/__init__.py:795) |
| **A-4** | `accounting` | `create_transaction()` | `fpe_accounting_audit_logs` | [`accounting.py:79`](modules/fazle_payroll_engine/accounting.py:79) |
| **A-5** | `accounting` | `reverse_transaction()` | `fpe_accounting_audit_logs` | [`accounting.py:158`](modules/fazle_payroll_engine/accounting.py:158) |
| **L-1** | `accounting` | `_upsert_ledger()` | `fpe_employee_ledger` | [`accounting.py:210`](modules/fazle_payroll_engine/accounting.py:210) |
| **L-2** | `admin_transactions` | `_adjust_ledger()` → `_upsert_ledger()` | `fpe_employee_ledger` | [`admin_transactions/__init__.py:552`](modules/admin_transactions/__init__.py:552) |

### Undiscovered Financial Write Paths

**None found.** The fresh scan confirmed exactly the 5 direct INSERT paths identified in the earlier audit. No additional undiscovered paths were detected.

---

## Step-2: Business Logic Extraction

For each direct INSERT path, I extract the complete business logic performed BEFORE the INSERT.

### W-1: `payment_ingest._finalize_admin_instruction()`

| Logic Step | Detail | Evidence |
|-----------|--------|---------|
| **Parse** | `parse_admin_cash_shorthand(text)` — extracts amount, method, mobile, employee_id_mobile | Line 487 |
| **Employee Lookup** | `_match_or_create_instruction_employee(parsed)` — matches by phone/name, creates if not found | Line 491 |
| **Idempotency** | `idem = f"admin-accountant-message:{message_id}"` — uses WhatsApp message_id | Line 492 |
| **Idempotency Check** | `SELECT transaction_id FROM wbom_cash_transactions WHERE idempotency_key=$1` | Line 494-497 |
| **Transaction Type** | Hardcoded `'advance'` | Line 515 |
| **Payment Method** | `parsed["method"]` — from parser | Line 521 |
| **Payment Mobile** | `parsed.get("payout_mobile") or parsed.get("mobile")` | Line 523 |
| **Employee Phone** | `parsed.get("employee_id_mobile") or employee_mobile` | Line 524 |
| **Remarks** | `f"Admin→Accountant instruction: {text.strip()[:500]}"` | Line 525 |
| **Created By** | `sender_number` (admin phone) | Line 526 |
| **Source** | `'admin-accountant-instruction'` | Line 517 |
| **Date** | `CURRENT_DATE` | Line 515 |
| **Audit Log** | ❌ None | — |
| **Ledger Update** | ❌ None | — |
| **Conflict Handling** | `ON CONFLICT (idempotency_key) DO NOTHING` + post-insert re-query | Line 518, 530-534 |

### W-2: `admin_commands/nl_advance_record.intent_advance_record()`

| Logic Step | Detail | Evidence |
|-----------|--------|---------|
| **Parse** | `_parse_emp_id()`, `_parse_phone()`, `_parse_amount()`, `_parse_method()`, `_parse_remarks()` — Bengali digit translation + regex | Lines 160-164 |
| **Employee Lookup** | `_lookup_employee(emp_id, phone)` — queries `wbom_employees` by ID or phone | Line 173 |
| **Transaction Type** | Hardcoded `'advance'` | Line 187 |
| **Payment Method** | `method` — raw string (bkash/nagad/rocket/cash) | Line 188 |
| **Employee Phone** | `emp.get("employee_mobile")` | Line 192 |
| **Remarks** | `f"Admin NL: {remarks}"` | Line 190 |
| **Source** | `'admin_nl'` | Line 189 |
| **Date** | `CURRENT_DATE` | Line 187 |
| **WhatsApp Message ID** | `whatsapp_message_id` (passed as parameter) | Line 193 |
| **Idempotency** | ❌ None — no idempotency key | — |
| **Audit Log** | ❌ None | — |
| **Ledger Update** | ❌ None | — |
| **Post-Insert** | `_cumulative_advance()` — reads back from `wbom_cash_transactions` for display | Line 199 |

### W-3: `payment_workflow.finalize_payment()`

| Logic Step | Detail | Evidence |
|-----------|--------|---------|
| **Draft Lookup** | `SELECT * FROM fazle_payment_drafts WHERE id = $1 FOR UPDATE` — row lock | Line 316 |
| **Idempotency** | `if draft.get("status") == "sent": return already_finalized` — status-based idempotency | Line 321-328 |
| **Transaction Type** | `'advance' if draft.get("draft_type") == "advance" else 'escort_payment'` | Line 333 |
| **Employee ID** | `draft.get("employee_id")` — from draft (wbom_employees.employee_id) | Line 340 |
| **Program ID** | `draft.get("escort_program_id")` — from draft | Line 340 |
| **Amount** | `approved_amount` — passed as parameter by admin | Line 340 |
| **Payment Method** | `method` — passed as parameter | Line 341 |
| **Remarks** | `f"Draft #{draft_id} — approved by admin"` | Line 341 |
| **Idempotency Key** | `f"payment-draft:{draft_id}"` | Line 342 |
| **Source** | `'payment-draft'` | Line 338 |
| **Date** | `CURRENT_DATE` | Line 338 |
| **Conflict Handling** | `ON CONFLICT (idempotency_key) DO NOTHING` | Line 339 |
| **Audit Log** | ❌ None | — |
| **Ledger Update** | ❌ None | — |
| **Post-Insert** | UPDATE `fazle_payment_drafts` SET `status='sent'`, `approved_amount`, `payment_method`, `accountant_msg` | Lines 355-361 |
| **Accountant Message** | Built AFTER insert as notification (Bengali text) | Lines 345-353 |

### W-4: `payment_correction.reverse_payment()`

| Logic Step | Detail | Evidence |
|-----------|--------|---------|
| **Draft Validation** | `SELECT * FROM fazle_payment_drafts WHERE id = $1` — check status is 'approved' or 'sent' | Lines 54-68 |
| **Original TX Lookup** | `SELECT transaction_id, amount, payment_method, transaction_type FROM wbom_cash_transactions WHERE employee_id=$1 AND amount=$2 AND is_reversed IS NOT TRUE AND transaction_type != 'reversal' ORDER BY transaction_time DESC LIMIT 1` | Lines 71-82 |
| **Mark Original Reversed** | `UPDATE wbom_cash_transactions SET is_reversed = true, correction_note = $1 WHERE transaction_id = $2` | Lines 88-91 |
| **Counter-Transaction** | INSERT with `transaction_type='reversal'`, negative amount, `reversal_of=orig_tx_id` | Lines 94-111 |
| **Employee ID** | `draft["employee_id"]` | Line 102 |
| **Program ID** | `draft.get("escort_program_id")` | Line 103 |
| **Amount** | `-float(orig_tx["amount"])` — negative | Line 104 |
| **Payment Method** | `orig_tx.get("payment_method") or draft.get("payment_method")` | Line 105 |
| **Payment Mobile** | `draft.get("payment_number")` | Line 106 |
| **Date/Time** | `CURRENT_DATE`, `NOW()` | Line 99 |
| **Status** | `'completed'` | Line 100 |
| **Remarks** | `(reason or "reversal")[:500]` | Line 107 |
| **Created By** | `admin_phone` | Line 108 |
| **Reversal Of** | `orig_tx_id` | Line 109 |
| **Correction Note** | `(reason or "reversed by admin")[:500]` | Line 110 |
| **Idempotency** | ❌ None — no idempotency key on counter-transaction | — |
| **Audit Log** | `fazle_payment_correction_log` (own table, not `fpe_accounting_audit_logs`) | Lines 130-142 |
| **Ledger Update** | ❌ None — no `fpe_employee_ledger` update | — |
| **Post-Insert** | UPDATE `fazle_payment_drafts` SET `status='reversed'`, correction metadata | Lines 114-126 |

### W-5: `admin_transactions.add_admin_transaction()`

| Logic Step | Detail | Evidence |
|-----------|--------|---------|
| **RBAC** | `_require_transaction_mutation_access(key, "create")` | Line 571 |
| **Employee Resolution** | `resolve_or_create_employee(name, id_phone, payout_phone)` — FPE employee system | Line 572-576 |
| **Phone Normalization** | `normalize_bd_phone(body.payout_phone)` | Line 579 |
| **Period** | `body.txn_date.strftime("%Y-%m")` | Line 580 |
| **Txn Ref** | `"fpe-admin-" + sha256(emp_id|amount|date|uuid)[:16]` — **includes UUID = non-deterministic** | Lines 583-585 |
| **Transaction Table** | `fpe_cash_transactions` (same as canonical!) | Line 590 |
| **Fields** | `txn_ref, employee_id, employee_name_raw, amount, payout_phone, payout_method, txn_date, txn_category, source_message_text, accounting_period, created_by='admin_manual'` | Lines 591-607 |
| **Audit Log** | `INSERT INTO fpe_accounting_audit_logs` — own insertion (duplicates C-1 logic) | Lines 609-626 |
| **Ledger Update** | `_adjust_ledger()` → calls canonical `_upsert_ledger()` | Lines 629, 552-560 |
| **Idempotency** | ❌ **BROKEN** — txn_ref includes `uuid.uuid4()` so re-submission creates duplicate | Line 584 |

---

## Step-3: Canonical Mapping

### Canonical Function Parameters (`TransactionCreateRequest`)

```python
class TransactionCreateRequest(BaseModel):
    fpe_wa_message_id: Optional[int] = None      # for idempotency
    employee_id: Optional[int] = None             # fpe_employees.id
    employee_name_raw: Optional[str] = None
    amount: Decimal
    payout_phone: Optional[str] = None
    payout_method: PayoutMethod = PayoutMethod.unknown
    txn_date: date
    txn_category: TxnCategory = TxnCategory.salary  # salary|advance|bonus|deduction|correction
    source_message_text: Optional[str] = None
    accounting_period: Optional[str] = None         # auto-derived if None
    created_by: str = "fpe_engine"
```

### Canonical Function Internal Logic

1. `txn_ref = sha256(fpe_wa_message_id + employee_id + amount + period + method)` — deterministic
2. Idempotency check: `SELECT FROM fpe_cash_transactions WHERE txn_ref = $1`
3. INSERT into `fpe_cash_transactions`
4. INSERT into `fpe_accounting_audit_logs` (action='create')
5. `_upsert_ledger()` into `fpe_employee_ledger`

### Mapping: W-1 `payment_ingest` → `create_transaction()`

| Direct INSERT Field | Canonical Parameter | Status |
|---------------------|---------------------|--------|
| `employee_id` (wbom) | `employee_id` (fpe) | ⚠️ **GAP**: wbom_employee_id ≠ fpe_employees.id — needs resolution |
| `amount` | `amount` | ✅ Direct map |
| `transaction_type='advance'` | `txn_category=TxnCategory.advance` | ✅ Map |
| `payment_method` | `payout_method=PayoutMethod(method)` | ✅ Map |
| `payment_mobile` | `payout_phone` | ✅ Map |
| `employee_phone` | ❌ **No canonical field** | ⚠️ Lost (was stored in wbom, not in fpe) |
| `transaction_date=CURRENT_DATE` | `txn_date=date.today()` | ✅ Map |
| `remarks` | `source_message_text` | ✅ Map (semantic shift) |
| `created_by=sender_number` | `created_by` | ✅ Map |
| `source='admin-accountant-instruction'` | ❌ **No canonical field** | ⚠️ Lost (fpe has no `source` column) |
| `idempotency_key` | `fpe_wa_message_id` (for txn_ref) | ⚠️ **GAP**: idempotency mechanism differs |
| `whatsapp_message_id` | `fpe_wa_message_id` | ✅ Map (but different idempotency logic) |
| ❌ No audit log | ✅ Canonical writes audit | 🆕 **GAINED** |
| ❌ No ledger | ✅ Canonical writes ledger | 🆕 **GAINED** |

**Fields Lost:** `employee_phone`, `source`, `idempotency_key` (replaced by txn_ref)  
**Fields Gained:** audit log, ledger update  
**Gap:** Employee ID resolution (wbom → fpe), idempotency mechanism change  

### Mapping: W-2 `nl_advance_record` → `create_transaction()`

| Direct INSERT Field | Canonical Parameter | Status |
|---------------------|---------------------|--------|
| `employee_id` (wbom) | `employee_id` (fpe) | ⚠️ **GAP**: needs resolution |
| `amount` | `amount` | ✅ Map |
| `transaction_type='advance'` | `txn_category=TxnCategory.advance` | ✅ Map |
| `payment_method` (raw) | `payout_method=PayoutMethod(method)` | ✅ Map |
| `employee_phone` | ❌ **No canonical field** | ⚠️ Lost |
| `transaction_date=CURRENT_DATE` | `txn_date=date.today()` | ✅ Map |
| `remarks` | `source_message_text` | ✅ Map |
| `source='admin_nl'` | ❌ **No canonical field** | ⚠️ Lost |
| `whatsapp_message_id` | `fpe_wa_message_id` | ✅ Map |
| ❌ No idempotency | `txn_ref` (deterministic) | 🆕 **GAINED** (fixes current bug) |
| ❌ No audit log | ✅ Canonical writes audit | 🆕 **GAINED** |
| ❌ No ledger | ✅ Canonical writes ledger | 🆕 **GAINED** |

**Fields Lost:** `employee_phone`, `source`  
**Fields Gained:** idempotency, audit log, ledger update  
**Gap:** Employee ID resolution (wbom → fpe)  

### Mapping: W-3 `finalize_payment` → `create_transaction()`

| Direct INSERT Field | Canonical Parameter | Status |
|---------------------|---------------------|--------|
| `employee_id` (wbom, from draft) | `employee_id` (fpe) | ⚠️ **GAP**: needs resolution |
| `program_id` | ❌ **No canonical field** | ⚠️ Lost (fpe has no program_id column) |
| `amount` | `amount` | ✅ Map |
| `transaction_type` (advance/escort_payment) | `txn_category` (advance/salary) | ✅ Map (escort_payment → salary) |
| `payment_method` | `payout_method` | ✅ Map |
| `transaction_date=CURRENT_DATE` | `txn_date=date.today()` | ✅ Map |
| `remarks` | `source_message_text` | ✅ Map |
| `idempotency_key=f"payment-draft:{draft_id}"` | `fpe_wa_message_id` (hash of draft_id) | ⚠️ **GAP**: mechanism change |
| `source='payment-draft'` | ❌ **No canonical field** | ⚠️ Lost |
| ❌ No audit log | ✅ Canonical writes audit | 🆕 **GAINED** |
| ❌ No ledger | ✅ Canonical writes ledger | 🆕 **GAINED** |

**Fields Lost:** `program_id`, `source`, `idempotency_key` (replaced)  
**Fields Gained:** audit log, ledger update  
**Gap:** Employee ID resolution, program_id not in fpe schema, idempotency mechanism change  
**Note:** Sprint-3B `draft_approval.create_canonical_transaction()` already does this mapping correctly. `finalize_payment()` is the OLD path that should delegate to the same logic.  

### Mapping: W-4 `reverse_payment` → `reverse_transaction()`

| Direct INSERT Field | Canonical Parameter | Status |
|---------------------|---------------------|--------|
| `employee_id` (wbom) | `employee_id` (fpe) | ⚠️ **GAP**: needs resolution |
| `program_id` | ❌ **No canonical field** | ⚠️ Lost |
| `transaction_type='reversal'` | `is_reversal=TRUE` | ✅ Map (different mechanism) |
| `amount` (negative) | `amount` (negative, canonical handles) | ✅ Map |
| `payment_method` | `payout_method` | ✅ Map |
| `payment_mobile` | `payout_phone` | ✅ Map |
| `transaction_date=CURRENT_DATE` | `txn_date` (from original) | ✅ Map |
| `status='completed'` | ❌ **No canonical field** | ⚠️ Lost (fpe has no status column) |
| `remarks` | `source_message_text` | ✅ Map |
| `created_by=admin_phone` | `created_by` | ✅ Map |
| `reversal_of=orig_tx_id` | `reversed_txn_id` | ✅ Map (different name) |
| `correction_note` | ❌ **No canonical field** | ⚠️ Lost |
| `is_reversed=true` (on original) | ❌ **Canonical never mutates original** | ⚠️ **BEHAVIOUR DIFF** |
| `fazle_payment_correction_log` | `fpe_accounting_audit_logs` | ⚠️ Different audit table |
| ❌ No ledger update | ✅ Canonical writes ledger | 🆕 **GAINED** |

**Fields Lost:** `program_id`, `status`, `correction_note`  
**Behaviour Difference:** Direct path mutates original row (`is_reversed=true`); canonical never mutates original — only creates a reversal row. **This is a semantic difference that must be preserved.**  
**Gap:** Employee ID resolution, original row mutation, correction log table  

### Mapping: W-5 `add_admin_transaction` → `create_transaction()`

| Direct INSERT Field | Canonical Parameter | Status |
|---------------------|---------------------|--------|
| `txn_ref` (with UUID) | `txn_ref` (deterministic) | ⚠️ **FIX**: remove UUID for idempotency |
| `employee_id` (fpe) | `employee_id` (fpe) | ✅ Same system |
| `employee_name_raw` | `employee_name_raw` | ✅ Map |
| `amount` | `amount` | ✅ Map |
| `payout_phone` | `payout_phone` | ✅ Map |
| `payout_method` | `payout_method` | ✅ Map |
| `txn_date` | `txn_date` | ✅ Map |
| `txn_category` | `txn_category` | ✅ Map |
| `source_message_text` | `source_message_text` | ✅ Map |
| `accounting_period` | `accounting_period` | ✅ Map |
| `created_by='admin_manual'` | `created_by` | ✅ Map |
| Own audit log INSERT | Canonical audit log | ✅ **REMOVE** (canonical handles) |
| `_adjust_ledger()` | Canonical `_upsert_ledger()` | ✅ **REMOVE** (canonical handles) |

**Fields Lost:** None  
**Fields Gained:** Proper idempotency (fixes UUID bug)  
**Gap:** txn_ref generation includes UUID (non-deterministic) — must be fixed  
**Note:** This is the CLOSEST to canonical. Same table, same fields. Only differences: own audit log insertion (duplicates canonical), own ledger call (delegates to canonical), broken idempotency.  

---

## Step-4: Gap Analysis

### Gap Summary

| Gap # | Description | Affected Paths | Severity | Solution |
|-------|-------------|---------------|----------|----------|
| **G-1** | Employee ID resolution: wbom_employees.employee_id → fpe_employees.id | W-1, W-2, W-3, W-4 | 🔴 CRITICAL | Use `_resolve_fpe_employee_id()` from draft_approval (already exists) |
| **G-2** | `program_id` has no canonical field in `fpe_cash_transactions` | W-3, W-4 | 🟠 HIGH | Accept field loss (program_id not in fpe schema) or add column (FORBIDDEN by freeze) |
| **G-3** | `source` column not in `fpe_cash_transactions` | W-1, W-2, W-3 | 🟡 MEDIUM | Encode in `source_message_text` or `created_by` |
| **G-4** | `employee_phone` not in `fpe_cash_transactions` | W-1, W-2 | 🟡 MEDIUM | Accept loss (fpe uses `payout_phone` only) |
| **G-5** | `status` column not in `fpe_cash_transactions` | W-4 | 🟡 MEDIUM | Accept loss (fpe uses `is_reversal` instead) |
| **G-6** | `correction_note` not in `fpe_cash_transactions` | W-4 | 🟡 MEDIUM | Encode in `source_message_text` |
| **G-7** | Idempotency mechanism differs (idempotency_key vs txn_ref) | W-1, W-3 | 🟠 HIGH | Wrapper must map idempotency_key → fpe_wa_message_id for txn_ref |
| **G-8** | W-4 mutates original row (`is_reversed=true`); canonical never mutates | W-4 | 🔴 CRITICAL | **BEHAVIOUR DIFF** — wrapper must preserve mutation |
| **G-9** | W-4 uses `fazle_payment_correction_log`; canonical uses `fpe_accounting_audit_logs` | W-4 | 🟠 HIGH | Keep correction_log as additional audit (additive, not replacement) |
| **G-10** | W-5 txn_ref includes UUID (non-deterministic) | W-5 | 🟠 HIGH | Fix to deterministic ref (behaviour improvement, not change) |
| **G-11** | W-1, W-2, W-3 write to `wbom_cash_transactions`; canonical writes to `fpe_cash_transactions` | W-1, W-2, W-3 | 🔴 CRITICAL | **TABLE MISMATCH** — see Decision Gate below |

### Critical Gap: Table Mismatch (G-11)

**The fundamental problem:** `create_transaction()` writes to `fpe_cash_transactions`. The legacy paths write to `wbom_cash_transactions`. Dashboard, payroll, and reports read from `wbom_cash_transactions`.

**If we route W-1/W-2/W-3 through `create_transaction()`:**
- Transactions will land in `fpe_cash_transactions`
- Dashboard/payroll/reports will NOT see them (they read `wbom_cash_transactions`)
- **This is a BEHAVIOUR CHANGE** — violates the Absolute Rule

**If we do NOT route them through `create_transaction()`:**
- The Constitution §1 violation remains
- But behaviour is preserved

**This is the core tension.** The Owner's instruction says "Zero Behaviour Change" AND "One Financial Write Function". These two requirements conflict for W-1/W-2/W-3 because the canonical function targets a different table than the readers expect.

### Possible Approaches (NO CODE — design only)

| Approach | Description | Behaviour Change? | Constitution Compliant? |
|----------|-------------|-------------------|------------------------|
| **A: Dual-Write Wrapper** | Wrapper calls `create_transaction()` (fpe) AND also inserts into `wbom_cash_transactions` (legacy) | ❌ No change — wbom still gets data | ⚠️ Partial — two writes, but one business logic |
| **B: Redirect Only** | Wrapper calls `create_transaction()` only, stops writing to wbom | ✅ YES — dashboard breaks | ✅ Yes |
| **C: Extend Canonical** | Modify `create_transaction()` to write to BOTH tables | ❌ No change — both tables get data | ⚠️ Partial — canonical function now has dual-write |
| **D: Do Nothing** | Leave direct INSERTs as-is, document as known tech debt | ❌ No change | ❌ No — Constitution violation remains |

**Owner must choose.** Approach A or C preserve behaviour. Approach B breaks dashboard/payroll. Approach D violates Constitution.

---

## Step-5: Implementation Strategy + Wrapper Design

### Wrapper Design (per path — NO CODE, design only)

#### W-5: `admin_transactions.add_admin_transaction()` — LOWEST RISK

**Current:** Direct INSERT into `fpe_cash_transactions` + own audit + own ledger  
**Wrapper:** Replace direct INSERT with `create_transaction()` call  
**Mapping:**
```
body.employee_name → employee_name_raw
body.amount → amount
body.payout_phone → payout_phone
body.payout_method → payout_method
body.txn_date → txn_date
body.txn_category → txn_category
body.notes → source_message_text
period → accounting_period
'admin_manual' → created_by
```
**Fix:** Remove UUID from txn_ref generation (let canonical generate deterministic ref)  
**Remove:** Own audit log INSERT (canonical handles), `_adjust_ledger()` call (canonical handles)  
**Backward Compatible?** ✅ Yes — same table, same fields, same API response shape  
**Regression Risk:** LOW — only changes internal write mechanism  
**Rollback:** Revert to direct INSERT — trivial  

#### W-2: `nl_advance_record.intent_advance_record()` — MEDIUM RISK

**Current:** Direct INSERT into `wbom_cash_transactions`  
**Wrapper:** Must resolve wbom→fpe employee, then call `create_transaction()`  
**Problem:** Transaction lands in `fpe_cash_transactions`, but `_cumulative_advance()` reads from `wbom_cash_transactions`  
**Solution:** Approach A (dual-write) or accept that cumulative advance display will change  
**Backward Compatible?** ⚠️ Only with dual-write (Approach A)  
**Regression Risk:** MEDIUM — employee lookup may fail, cumulative display may break  
**Rollback:** Revert to direct INSERT  

#### W-1: `payment_ingest._finalize_admin_instruction()` — MEDIUM RISK

**Current:** Direct INSERT into `wbom_cash_transactions` with own idempotency  
**Wrapper:** Must resolve wbom→fpe employee, map idempotency_key → fpe_wa_message_id  
**Problem:** Same table mismatch as W-2  
**Solution:** Approach A (dual-write) or accept behaviour change  
**Backward Compatible?** ⚠️ Only with dual-write  
**Regression Risk:** MEDIUM — idempotency mechanism change, employee resolution  
**Rollback:** Revert to direct INSERT  

#### W-3: `payment_workflow.finalize_payment()` — HIGH RISK

**Current:** Direct INSERT into `wbom_cash_transactions`, actively called by PAID command  
**Wrapper:** Should delegate to `draft_approval.create_canonical_transaction()` (Sprint-3B path)  
**Problem:** Sprint-3B path writes to `fpe_cash_transactions`; PAID command expects `wbom_cash_transactions`  
**Solution:** Approach A (dual-write) OR deprecate `finalize_payment()` and redirect PAID to Sprint-3B path  
**Backward Compatible?** ⚠️ Only with dual-write  
**Regression Risk:** HIGH — actively called, multiple callers, draft status update logic  
**Rollback:** Revert to direct INSERT  

#### W-4: `payment_correction.reverse_payment()` — HIGH RISK

**Current:** Direct INSERT into `wbom_cash_transactions` + UPDATE original row + correction log  
**Wrapper:** Should call `reverse_transaction()` from accounting.py  
**Problems:**
1. `reverse_transaction()` writes to `fpe_cash_transactions`, not `wbom_cash_transactions`
2. `reverse_transaction()` never mutates original; direct path sets `is_reversed=true`
3. `reverse_transaction()` doesn't write to `fazle_payment_correction_log`
4. `reverse_transaction()` doesn't update `fazle_payment_drafts` status
**Solution:** Approach A (dual-write) + preserve original row mutation + keep correction log  
**Backward Compatible?** ⚠️ Only with complex wrapper that preserves all side effects  
**Regression Risk:** HIGH — reversal is critical financial operation, multiple side effects  
**Rollback:** Revert to direct INSERT  

---

## Step-6: Risk Matrix

| Path | Risk | Reason | Mitigation |
|------|------|--------|------------|
| **W-5** `admin_transactions` | 🟢 LOW | Same table as canonical. Only internal mechanism change. No external behaviour change. | Unit test: verify same API response, same DB row, audit log present |
| **W-2** `nl_advance_record` | 🟡 MEDIUM | Table mismatch (wbom→fpe). Employee resolution may fail. Cumulative advance display reads wbom. | Dual-write wrapper (Approach A). Test employee resolution. |
| **W-1** `payment_ingest` | 🟡 MEDIUM | Table mismatch. Idempotency mechanism change. Employee resolution. | Dual-write wrapper. Test idempotency with duplicate messages. |
| **W-3** `payment_workflow` | 🔴 HIGH | Actively called by PAID command + payment_ingest + app/main. Multiple callers. Draft status update logic. Table mismatch. | Dual-write wrapper. Full regression of PAID command flow. Consider deprecation instead. |
| **W-4** `payment_correction` | 🔴 HIGH | Reversal is critical. Original row mutation behaviour difference. Correction log table. Draft status update. Table mismatch. | Complex wrapper preserving all side effects. Full regression of correction flow. |

---

## Step-7: Refactoring Plan / Priority Order

### Recommended Order (lowest risk first)

| Phase | Path | Risk | Rationale |
|-------|------|------|-----------|
| **Phase-1** | W-5 `admin_transactions` | 🟢 LOW | Same table. Cleanest refactor. Proves the pattern. |
| **Phase-2** | W-2 `nl_advance_record` | 🟡 MEDIUM | Simple flow. Good test of employee resolution + dual-write. |
| **Phase-3** | W-1 `payment_ingest` | 🟡 MEDIUM | Tests idempotency mapping. Similar to W-2. |
| **Phase-4** | W-3 `payment_workflow` | 🔴 HIGH | Most complex. Multiple callers. Consider deprecation. |
| **Phase-5** | W-4 `payment_correction` | 🔴 HIGH | Most dangerous. Reversal logic. Do last. |

### Each Phase Follows SOP

```
Audit (done) → Root Cause (done) → Implementation Plan (done) → Owner Approval → Implementation → Regression → Owner Acceptance → Production Approval
```

**No phase begins without Owner approval of the previous phase's results.**

---

## Step-8: Regression Matrix

After EACH phase, verify ALL of the following:

| # | Flow | Verification | How to Test |
|---|------|-------------|-------------|
| 1 | WhatsApp Admin → Accountant | Transaction appears in `wbom_cash_transactions` | Send test admin instruction, query table |
| 2 | Cash Ledger | Dashboard shows correct cash position | Check dashboard overview endpoint |
| 3 | Payroll | Payroll calculation includes advances | Run payroll compute for test employee |
| 4 | Dashboard | Dashboard counts match | Check `/dashboard/overview` |
| 5 | Reports | Cash report shows transaction | Call daily cash report |
| 6 | Operator PAID | PAID command creates transaction | Send PAID command, verify |
| 7 | Draft Approval | APPROVED command creates transaction | Send APPROVED command, verify |
| 8 | Manual Entry (Admin UI) | Admin UI creates transaction | POST to `/transactions`, verify |
| 9 | Correction (Reversal) | Reversal creates counter-transaction | Call `reverse_payment()`, verify |
| 10 | Correction (Adjust) | Adjustment creates new draft | Call `adjust_payment()`, verify |
| 11 | Idempotency | Duplicate submission returns existing | Submit same message twice, verify same txn_id |
| 12 | Audit Trail | `fpe_accounting_audit_logs` has entry | Query audit table after each operation |
| 13 | Ledger | `fpe_employee_ledger` updated | Query ledger after transaction |
| 14 | Existing Tests | All 110 tests pass | `pytest` full suite |

---

## Step-9: Owner Decision Gates

### Gate-1: Table Mismatch Strategy (BLOCKING — must decide before any implementation)

The core conflict: `create_transaction()` writes to `fpe_cash_transactions`, but dashboard/payroll/reports read from `wbom_cash_transactions`.

**Options:**

| Option | Description | Behaviour Preserved? | Constitution Compliant? |
|--------|-------------|---------------------|------------------------|
| **A: Dual-Write Wrapper** | Each wrapper calls `create_transaction()` (fpe) AND inserts into `wbom_cash_transactions` (legacy) | ✅ Yes | ⚠️ Partial — one business logic, two table writes |
| **B: Redirect Only** | Wrapper calls `create_transaction()` only | ❌ No — dashboard/payroll break | ✅ Yes |
| **C: Extend Canonical** | `create_transaction()` writes to both tables internally | ✅ Yes | ⚠️ Partial — canonical function has dual-write |
| **D: Defer** | Leave as-is, document as tech debt for future migration sprint | ✅ Yes | ❌ No |

**My recommendation:** Option A (Dual-Write Wrapper). It preserves behaviour (wbom still gets data), routes business logic through canonical function (Constitution §1 satisfied for logic), and creates a clear migration path (remove wbom write when dashboard/payroll are migrated in a future sprint).

### Gate-2: W-3 `finalize_payment` Strategy

Should `finalize_payment()` be:
- **Refactored** to call `create_transaction()` (with dual-write)?
- **Deprecated** in favor of `draft_approval.create_canonical_transaction()` (redirect PAID command)?

### Gate-3: W-4 `reverse_payment` Original Row Mutation

`reverse_payment()` sets `is_reversed=true` on the original row. `reverse_transaction()` does NOT. Should the wrapper:
- **Preserve** the mutation (add `UPDATE wbom_cash_transactions SET is_reversed=true` after calling canonical)?
- **Drop** the mutation (accept behaviour change)?

### Gate-4: Implementation Order Approval

Do you approve the recommended order (W-5 → W-2 → W-1 → W-3 → W-4)?

---

## Required Deliverables Checklist

| # | Deliverable | Status |
|---|------------|--------|
| 1 | Current Runtime Evidence Report | ✅ Step-1 |
| 2 | Duplicate Business Logic Report | ✅ Step-2 |
| 3 | Canonical Mapping Report | ✅ Step-3 |
| 4 | Gap Analysis | ✅ Step-4 |
| 5 | Wrapper Design | ✅ Step-5 |
| 6 | Risk Assessment | ✅ Step-6 |
| 7 | Migration Strategy | ✅ Step-7 |
| 8 | Regression Checklist | ✅ Step-8 |
| 9 | Rollback Strategy | ✅ Per-path in Step-5 (revert to direct INSERT) |
| 10 | Implementation Order | ✅ Step-7 |

---

## Success Criteria

| # | Criterion | Met? |
|---|-----------|------|
| 1 | Production Behaviour unchanged | ⏳ Depends on Gate-1 decision |
| 2 | No table changes | ✅ No ALTER/DROP/MIGRATE proposed |
| 3 | No dashboard changes | ✅ Not touched |
| 4 | No payroll changes | ✅ Not touched |
| 5 | Direct INSERT path inventory complete | ✅ 5 paths identified + 3 UPDATE paths |
| 6 | All business logic documented with evidence | ✅ Step-2 |
| 7 | Canonical mapping complete | ✅ Step-3 |
| 8 | Owner can safely approve next sprint | ⏳ Awaiting Gate decisions |

---

## Sign-off

| Role | Status |
|------|--------|
| Auditor | ✅ Blueprint complete |
| Owner Decision Gate-1 (Table Strategy) | ⏳ Pending |
| Owner Decision Gate-2 (finalize_payment) | ⏳ Pending |
| Owner Decision Gate-3 (reversal mutation) | ⏳ Pending |
| Owner Decision Gate-4 (Implementation Order) | ⏳ Pending |
| Implementation | ⏳ Blocked on all 4 gates |