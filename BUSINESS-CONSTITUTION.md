# Fazle Core — Business Constitution

**Version:** 1.0  
**Date:** 2026-06-28  
**Status:** LOCKED — Architecture Freeze Active  
**Authority:** Owner  
**Scope:** All financial workflows in the Fazle Core system

---

## Article I: Canonical Transaction Principle

### §1.1 — Single Source of Financial Truth

There is exactly ONE canonical function that creates a financial transaction:

```
create_transaction(req: TransactionCreateRequest) -> TransactionRow
```

Located at [`core/modules/fazle_payroll_engine/accounting.py`](core/modules/fazle_payroll_engine/accounting.py:30).

This function:
- Inserts into `fpe_cash_transactions`
- Calls `_upsert_ledger()` internally (which updates `fpe_employee_ledger`)
- Writes to `fpe_accounting_audit_logs`

### §1.2 — No Parallel Financial Logic

No parallel transaction engine, no direct ledger writes, no bypass paths.

**PROHIBITED:**
- Direct `INSERT INTO fpe_cash_transactions` outside `create_transaction()`
- Direct `INSERT/UPDATE fpe_employee_ledger` outside `_upsert_ledger()`
- Re-parsing an accountant message to create a transaction
- Creating financial records from raw WhatsApp messages without draft approval

### §1.3 — Draft is the Source of Truth

The Draft (in `fazle_payment_drafts`) is the authoritative source of financial data.

The Accountant Message is a **notification only** — it informs the accountant that a payment should be processed. It is NEVER re-parsed to create a transaction.

```
Draft (source of truth)
    │
    ├──→ create_transaction() reads from Draft
    │        └── _upsert_ledger() called internally
    │
    └──→ build_accountant_message() — notification only
             └── sent to accountant via WhatsApp
```

**Financial data flows FROM the Draft TO the transaction.**  
**Never FROM the accountant message TO the transaction.**

---

## Article II: Valid Paths to Cash Ledger

System-এ Cash Ledger-এ প্রবেশের বৈধ পথ মাত্র তিনটি।  
There are exactly THREE valid paths to create a financial transaction.

### Path-1: Admin → Accountant WhatsApp → Canonical Transaction

```
Admin sends payment instruction via WhatsApp
    ↓
Accountant receives message
    ↓
parse_message() extracts payment data
    ↓
ingest_message() → match_or_create_employee()
    ↓
create_transaction()  [CANONICAL]
    ↓
fpe_cash_transactions + fpe_employee_ledger
```

**Use case:** Admin directly instructs accountant to make a payment (e.g., escort duty payment, salary disbursement).

### Path-2: Employee Request → Draft → Approved → Canonical Transaction

```
Employee sends payment/advance request via WhatsApp
    ↓
Sprint-3A: Employee Conversation creates Draft (status='pending')
    ↓
Admin reviews draft
    ↓
Sprint-3B: APPROVED command
    ↓
draft_approval.approve_draft()
    ↓
create_transaction()  [CANONICAL]
    ↓
fpe_cash_transactions + fpe_employee_ledger
```

**Use case:** Employee requests advance, salary, or emergency payment through the conversation system.

### Path-3: Operator → Pending → Approved → Canonical Transaction

```
Operator creates payment draft (e.g., escort payment draft)
    ↓
Draft status='pending'
    ↓
Admin/Operator approves
    ↓
Sprint-3B: APPROVED command
    ↓
draft_approval.approve_draft()
    ↓
create_transaction()  [CANONICAL]
    ↓
fpe_cash_transactions + fpe_employee_ledger
```

**Use case:** System-generated drafts from escort program completion, payroll runs, or operator-initiated payment requests.

### §2.1 — No Other Paths

**Any financial transaction created outside these three paths is a violation of this Constitution.**

If a new path is needed, it must:
1. Be documented in this Constitution
2. Be approved by the Owner
3. Route through `create_transaction()` — no exceptions

---

## Article III: Protected Components

The following components are PROTECTED. They may be called but NEVER modified without Owner approval.

| Component | File | Protection Level |
|-----------|------|-----------------|
| `create_transaction()` | [`accounting.py:30`](core/modules/fazle_payroll_engine/accounting.py:30) | 🔒 LOCKED |
| `_upsert_ledger()` | [`accounting.py:190`](core/modules/fazle_payroll_engine/accounting.py:190) | 🔒 LOCKED |
| `accounting_worker()` | — | 🔒 LOCKED |
| `parse_message()` | [`parser.py:199`](core/modules/fazle_payroll_engine/parser.py:199) | 🔒 LOCKED |
| `match_or_create_employee()` | [`employee.py:94`](core/modules/fazle_payroll_engine/employee.py:94) | 🔒 LOCKED |
| WhatsApp Admin ↔ Accountant Flow | [`message_router/__init__.py`](core/modules/message_router/__init__.py) | 🔒 LOCKED |
| Existing Payroll Engine | [`fazle_payroll_engine/`](core/modules/fazle_payroll_engine/) | 🔒 LOCKED |
| Existing Ledger Calculation | `fpe_employee_ledger` schema | 🔒 LOCKED |
| Existing Employee Identity Rules | [`employee.py`](core/modules/fazle_payroll_engine/employee.py) | 🔒 LOCKED |

### §3.1 — Additive-Only Rule

All changes to the system must be **additive**:
- New functions may be added
- New columns may be added to existing tables
- New tables may be created
- **No existing function signature may be changed**
- **No existing table column may be removed or renamed**
- **No existing table structure may be altered**

---

## Article IV: Idempotency Guarantee

### §4.1 — One Draft = One Transaction

Each draft can produce at most ONE canonical transaction.

**Idempotency mechanism:**
1. Deterministic `wa_message_id = sha256("draft-<draft_id>")`
2. `txn_ref = sha256(wa_message_id + employee_id + amount + period + method)`
3. `create_transaction()` checks for existing `txn_ref` and returns existing row
4. Row-level lock (`SELECT FOR UPDATE`) prevents concurrent approvals
5. State check: `transaction_id IS NULL` required before approval

### §4.2 — Duplicate Approval Safety

If an admin sends `APPROVED <id> <amount> <method>` twice:
- First call: creates transaction, sets draft to `completed`
- Second call: rejected — draft is no longer `pending`
- No duplicate transaction is ever created

---

## Article V: Audit Requirements

### §5.1 — Dual Audit Trail

Every financial action produces audit entries in TWO systems:

1. **Draft Audit** (`fazle_draft_audit_log`) — Draft lifecycle events
2. **Financial Audit** (`fpe_accounting_audit_logs`) — Transaction/ledger events (written by `create_transaction()`)

### §5.2 — Required Audit Events per APPROVED Workflow

| Event | System | Before State | After State |
|-------|--------|-------------|-------------|
| `approved` | fazle_draft_audit_log | ✅ pre-approval snapshot | ✅ status, txn_id, txn_ref |
| `transaction_created` | fazle_draft_audit_log | — | ✅ transaction_id, txn_ref |
| `ledger_updated` | fazle_draft_audit_log | — | ✅ transaction_id, txn_ref |
| `accountant_forwarded` | fazle_draft_audit_log | — | ✅ accountant_msg |
| `create` (transaction) | fpe_accounting_audit_logs | — | ✅ txn_ref, amount, method |

### §5.3 — Required Audit Events per REJECT Workflow

| Event | System | Before State | After State |
|-------|--------|-------------|-------------|
| `rejected` | fazle_draft_audit_log | ✅ pre-reject snapshot | ✅ status, reason |

**NO financial audit entry** — no transaction was created.

### §5.4 — Required Audit Events per EDIT Workflow

| Event | System | Before State | After State |
|-------|--------|-------------|-------------|
| `edited` | fazle_draft_audit_log | ✅ pre-edit snapshot | ✅ new values, version |

---

## Article VI: Draft Version History (Roadmap)

### §6.1 — Current State

The system currently stores:
- `version` (INT) — incremented on each edit
- `before_state` (JSONB) — snapshot before edit
- `after_state` (JSONB) — snapshot after edit
- `editor` (TEXT) — who edited

### §6.2 — Future: Draft Timeline

**Roadmap for Audit UI (not yet implemented):**

```
Created → Edited (v1) → Edited (v2) → Approved → Transaction → Completed
```

Each event is already recorded in `fazle_draft_audit_log` with timestamps. A future Audit UI can reconstruct the full timeline by querying:

```sql
SELECT event, before_state, after_state, performed_by, created_at
FROM fazle_draft_audit_log
WHERE draft_id = $1
ORDER BY created_at ASC;
```

---

## Article VII: Architecture Freeze

### §7.1 — No New Business Features

Effective immediately, **no new business features** will be added until the System Consolidation Sprint is complete.

### §7.2 — System Consolidation Sprint (Pre-Sprint-4)

Before Sprint-4, the following consolidation work must be completed:

| Task | Description |
|------|-------------|
| Duplicate Code Audit | Identify and remove duplicate logic across modules |
| Dead Code Cleanup | Remove unused functions, imports, and variables |
| Documentation Alignment | Ensure all docs match actual code behavior |
| RBAC Alignment | Verify all commands have correct RBAC roles |
| Frontend Consistency | Ensure dashboard UI matches backend API |
| Dashboard Consistency | Verify all dashboard data sources are correct |
| Admin Panel Review | Review all admin commands and their outputs |
| Transaction UI Review | Verify transaction display is accurate |
| Pending Queue Review | Review pending drafts/transactions queue |
| Audit UI Review | Review audit log display and completeness |

### §7.3 — Sprint-4 Prerequisites

Sprint-4 may only begin after:
1. All consolidation tasks are complete
2. All existing tests pass
3. This Constitution is reviewed and updated if needed
4. Owner approval is obtained

---

## Article VIII: Amendment Process

This Constitution may only be amended by the Owner.

Any proposed amendment must:
1. Be documented in writing
2. Include the rationale for the change
3. Be reviewed for impact on protected components
4. Receive explicit Owner approval

---

## Appendix A: Command Reference

### Sprint-3B Commands (Canonical Financial Approval)

| Command | Format | Path | Creates Transaction? |
|---------|--------|------|---------------------|
| `APPROVED` | `APPROVED <id> <amount> <method>` | Path-2 / Path-3 | ✅ Yes |
| `DREDIT` | `DREDIT <id> <amount> <method> [payout=<phone>]` | — | ❌ No (edit only) |
| `DREJECT` | `DREJECT <id> [reason]` | — | ❌ No |

### Legacy Commands (Path-1 — Accountant Flow)

| Command | Format | Path | Creates Transaction? |
|---------|--------|------|---------------------|
| `PAID` | `PAID <id> <amount> <method>` | Path-1 | ✅ Yes (via finalize_payment → wbom_cash_transactions) |
| `ADVANCE` | `ADVANCE <id> <amount> <method>` | Path-1 | ✅ Yes (via finalize_payment → wbom_cash_transactions) |

### Draft Reply Commands (Non-Financial)

| Command | Format | Purpose |
|---------|--------|---------|
| `APPROVE` | `APPROVE <id>` | Approve draft reply (message delivery, not financial) |
| `REJECT` | `REJECT <id>` | Reject draft reply |
| `EDIT` | `EDIT <id> <new text>` | Edit draft reply text |

---

**This Constitution is LOCKED. All development must comply.**

*Last updated: 2026-06-28*  
*Authority: Owner*  
*Next review: After System Consolidation Sprint*