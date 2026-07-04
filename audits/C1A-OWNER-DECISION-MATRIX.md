# C1A Owner Decision Matrix

> **Status:** Awaiting Owner decisions on C1A-OD-1 through C1A-OD-6
> **Phase-C1A:** ✅ Approved (Audit & Certification Complete)
> **Phase-C1B:** ⛔ Hold — will not start until all 6 decisions are finalized
> **No code branch created for C1B.**

---

## How to Use This Matrix

Each row represents one Owner Decision from [`PHASE-C1A-PRODUCTION-BEHAVIOUR-CERTIFICATION.md`](PHASE-C1A-PRODUCTION-BEHAVIOUR-CERTIFICATION.md:496).

For each decision the Owner should select **one** option and record the final choice in the `Owner Final Decision` column. The `Next Sprint Impact` column shows exactly what C1B scope will be derived from that choice.

**Owner instruction:** Please review each decision and reply with your choice (e.g., "C1A-OD-1 = A", "C1A-OD-2 = B", etc.). You may also modify options or request more evidence before deciding.

---

## Decision C1A-OD-1: Dual-Table Unification Strategy

| Field | Value |
|-------|-------|
| **Decision ID** | C1A-OD-1 |
| **Subject** | Should Sprint-C1 unify `fpe_cash_transactions` and `wbom_cash_transactions` into a single table? |
| **Current Evidence** | Two parallel cash-transaction tables exist in production. [`fpe_cash_transactions`](core/modules/fazle_payroll_engine/accounting.py:53) is written by [`create_transaction()`](core/modules/fazle_payroll_engine/accounting.py:30) and updates [`fpe_employee_ledger`](core/modules/fazle_payroll_engine/accounting.py:190) + [`fpe_accounting_audit_logs`](core/modules/fazle_payroll_engine/accounting.py:77). [`wbom_cash_transactions`](core/modules/payment_workflow/__init__.py:335) is written by [`finalize_payment()`](core/modules/payment_workflow/__init__.py:306), [`ingest_admin_cash_entry()`](core/modules/payment_ingest/__init__.py:510), and [`intent_advance_record()`](core/modules/admin_commands/nl_advance_record.py:186) with **no ledger update and no audit log**. Dashboard reads from FPE; payroll [`compute_run()`](core/modules/payroll/__init__.py:104) reads advances from WBOM. [`wbom_fpe_sync.py`](core/modules/payment_ingest/wbom_fpe_sync.py:121) was intended to bridge but calls `create_transaction()` with the wrong signature and would crash if invoked. |
| **Possible Options** | **A.** Unify to `fpe_cash_transactions` only — migrate all WBOM write paths to call `create_transaction()`.<br>**B.** Keep both tables, fix `wbom_fpe_sync.py` to bridge every WBOM write to FPE.<br>**C.** Keep both tables, add ledger + audit logging directly to WBOM write paths.<br>**D.** Defer unification to Sprint-C2; in C1B only document the gap and add monitoring. |
| **Risk per Option** | **A.** 🔴 HIGH — Touches 3 production write paths (`finalize_payment`, `ingest_admin_cash_entry`, `intent_advance_record`), changes `txn_ref`/idempotency semantics, may alter dashboard/payroll totals if historical WBOM rows are not backfilled, and changes the WhatsApp Admin → Accountant production flow behaviour.<br>**B.** 🟠 MEDIUM — Requires a reliable async/event bridge; risk of duplicate FPE rows if idempotency is not perfect; still leaves two tables to maintain; sync latency may confuse dashboard/payroll.<br>**C.** 🟠 MEDIUM — Duplicates ledger/audit logic in multiple places; does not solve the payroll-vs-dashboard divergence; increases maintenance burden.<br>**D.** 🟢 LOW — Safest. No behaviour change. Defers real fix. Risk is continued divergence between dashboard and payroll until C2. |
| **Owner Final Decision** | *(Pending — please select A, B, C, or D)* |
| **Next Sprint Impact (C1B)** | If **A** → C1B scope becomes large: refactor 3 write paths, backfill WBOM → FPE, reconcile payroll/dashboard, add tests. If **B** → C1B builds a sync bridge + idempotency guard. If **C** → C1B adds ledger/audit calls to WBOM paths but keeps dual tables. If **D** → C1B is limited to monitoring/alerts only; no code change to production flow. |

---

## Decision C1A-OD-2: `finalize_payment()` Ledger Gap

| Field | Value |
|-------|-------|
| **Decision ID** | C1A-OD-2 |
| **Subject** | Should [`finalize_payment()`](core/modules/payment_workflow/__init__.py:306) (the `PAID <id> <amount> <method>` command path) update `fpe_employee_ledger`? |
| **Current Evidence** | [`finalize_payment()`](core/modules/payment_workflow/__init__.py:306) is the legacy escort/advance path triggered by the admin `PAID` command. It directly INSERTs into [`wbom_cash_transactions`](core/modules/payment_workflow/__init__.py:335) and updates the draft status, but **never calls `_upsert_ledger()`**. The newer `APPROVED` path (Sprint-3B) uses [`approve_draft()`](core/modules/draft_approval/__init__.py:401) → [`create_canonical_transaction()`](core/modules/draft_approval/__init__.py:293) → [`create_transaction()`](core/modules/fazle_payroll_engine/accounting.py:30), which does update the ledger. Therefore `PAID` and `APPROVED` commands produce different ledger states for otherwise similar payments. |
| **Possible Options** | **A.** Add `_upsert_ledger()` call inside `finalize_payment()` so PAID rows affect the ledger.<br>**B.** Leave as-is and document the gap; PAID remains a non-ledger legacy path.<br>**C.** Route PAID through `create_transaction()` instead of direct WBOM INSERT (major behaviour change). |
| **Risk per Option** | **A.** 🟡 MEDIUM — Changes production behaviour for the `PAID` command; existing PAID rows in WBOM will still not be reflected in the ledger unless backfilled; may surprise users who expected employee balance to match WBOM total.<br>**B.** 🟢 LOW — Safest. No behaviour change. Risk is continued inconsistency between WBOM payment total and ledger balance for any payment finalized via `PAID`.<br>**C.** 🔴 HIGH — Converts PAID from WBOM path to FPE path; changes table, idempotency, audit, and possibly dashboard/payroll visibility; high regression risk for WhatsApp Admin flow. |
| **Owner Final Decision** | *(Pending — please select A, B, or C)* |
| **Next Sprint Impact (C1B)** | If **A** → C1B adds ledger update to `finalize_payment()` and decides whether to backfill historical PAID rows. If **B** → C1B only documents the gap; no code change. If **C** → C1B rewrites the PAID command path to use the canonical transaction function; large change. |

---

## Decision C1A-OD-3: `ingest_admin_cash_entry()` and `intent_advance_record()` Audit Gap

| Field | Value |
|-------|-------|
| **Decision ID** | C1A-OD-3 |
| **Subject** | Should the direct WBOM INSERT paths [`ingest_admin_cash_entry()`](core/modules/payment_ingest/__init__.py:478) (accountant cash shorthand) and [`intent_advance_record()`](core/modules/admin_commands/nl_advance_record.py:157) (NL advance record) write audit rows to `fpe_accounting_audit_logs`? |
| **Current Evidence** | [`ingest_admin_cash_entry()`](core/modules/payment_ingest/__init__.py:478) is triggered when an accountant sends a cash shorthand such as `CASH <name> <amount> <method>`. It directly INSERTs into [`wbom_cash_transactions`](core/modules/payment_ingest/__init__.py:510) with no draft, no ledger update, and no audit log. [`intent_advance_record()`](core/modules/admin_commands/nl_advance_record.py:157) is triggered by natural-language advance queries and also directly INSERTs into [`wbom_cash_transactions`](core/modules/admin_commands/nl_advance_record.py:186). Both bypass [`create_transaction()`](core/modules/fazle_payroll_engine/accounting.py:30), so they also bypass the audit log written at [`accounting.py:77`](core/modules/fazle_payroll_engine/accounting.py:77). |
| **Possible Options** | **A.** Add `fpe_accounting_audit_logs` INSERT to both paths, keeping them on WBOM table.<br>**B.** Leave as-is and document the audit gap.<br>**C.** Route both paths through `create_transaction()` (major behaviour change: table switch + ledger update + idempotency). |
| **Risk per Option** | **A.** 🟡 MEDIUM — Adds audit trail without changing the table or ledger; low regression risk but introduces duplicate audit-log logic outside the canonical function.<br>**B.** 🟢 LOW — Safest. No behaviour change. Risk is reduced accountability for accountant/admin cash entries and advance records.<br>**C.** 🔴 HIGH — Switches both paths to FPE table and ledger; changes how accountant shorthand and NL advances are recorded; may break existing UI/dashboard expectations. |
| **Owner Final Decision** | *(Pending — please select A, B, or C)* |
| **Next Sprint Impact (C1B)** | If **A** → C1B adds audit-log calls to two modules; small, isolated change. If **B** → C1B only documents the gap. If **C** → C1B rewrites both modules to use canonical transaction creation; larger change tied to C1A-OD-1 outcome. |

---

## Decision C1A-OD-4: `wbom_fpe_sync.py` Disposition

| Field | Value |
|-------|-------|
| **Decision ID** | C1A-OD-4 |
| **Subject** | Should the broken [`wbom_fpe_sync.py`](core/modules/payment_ingest/wbom_fpe_sync.py:1) module be deleted, fixed, or left dormant? |
| **Current Evidence** | [`sync_wbom_transaction()`](core/modules/payment_ingest/wbom_fpe_sync.py:61) calls `create_transaction(employee_id=..., amount=..., payout_method=..., wa_message_id=...)` at line 121. The actual signature of [`create_transaction()`](core/modules/fazle_payroll_engine/accounting.py:30) is `async def create_transaction(req: TransactionCreateRequest)`. Calling it with keyword arguments would raise `TypeError`. `grep` found 0 external callers of `sync_wbom_transaction()` or `backfill_wbom_to_fpe()`. The module is effectively dead code. |
| **Possible Options** | **A.** Delete the module entirely (dead code removal).<br>**B.** Fix the signature to use `TransactionCreateRequest` and keep the bridge.<br>**C.** Leave as-is with an explicit `DORMANT` marker and warning comment. |
| **Risk per Option** | **A.** 🟢 LOW — No production callers; deletion reduces confusion and maintenance surface. Risk is minimal: if someone later expected the bridge, it must be re-implemented from scratch.<br>**B.** 🟡 MEDIUM — Fixing the signature is easy, but the bridge's correctness depends on C1A-OD-1; if tables are unified, the bridge becomes obsolete. Risk of half-implemented feature.<br>**C.** 🟢 LOW — No behaviour change. Risk is continued presence of broken code that may mislead future developers. |
| **Owner Final Decision** | *(Pending — please select A, B, or C)* |
| **Next Sprint Impact (C1B)** | If **A** → C1B deletes one file; trivial, safe cleanup. If **B** → C1B repairs the bridge and decides how/when to invoke it. If **C** → C1B only adds comments; no functional change. |

---

## Decision C1A-OD-5: `payment_correction` Module Disposition

| Field | Value |
|-------|-------|
| **Decision ID** | C1A-OD-5 |
| **Subject** | Should the dormant [`payment_correction`](core/modules/payment_correction/__init__.py:1) module be deleted, wired into admin commands, or left dormant? |
| **Current Evidence** | The module implements [`reverse_payment()`](core/modules/payment_correction/__init__.py:38) and [`adjust_payment()`](core/modules/payment_correction/__init__.py:160). `reverse_payment()` writes to [`wbom_cash_transactions`](core/modules/payment_correction/__init__.py:77) and creates a negative-amount row. `adjust_payment()` creates a correction draft. A repository-wide `grep` found **0 external callers** for either function. The module is fully implemented but unwired. |
| **Possible Options** | **A.** Delete the module entirely (dead code removal).<br>**B.** Wire `REVERSE` and `ADJUST` commands into [`process_admin_command()`](core/modules/admin_commands/__init__.py:233).<br>**C.** Leave as-is (already marked DORMANT in the C1A report). |
| **Risk per Option** | **A.** 🟢 LOW — No production callers; deletion reduces confusion. Risk is loss of pre-built reversal logic if it is needed later (can be recovered from git history).<br>**B.** 🟠 MEDIUM — Adds new admin commands and exposes reversal/adjustment to WhatsApp Admin flow; must be carefully designed to avoid accidental financial mutations; should include approval/draft layer.<br>**C.** 🟢 LOW — No behaviour change. Risk is dead code remaining in the codebase. |
| **Owner Final Decision** | *(Pending — please select A, B, or C)* |
| **Next Sprint Impact (C1B)** | If **A** → C1B deletes the module; trivial cleanup. If **B** → C1B designs and implements admin command wiring + safety checks; medium-sized feature. If **C** → C1B only adds/keeps DORMANT marker; no functional change. |

---

## Decision C1A-OD-6: `add_admin_transaction()` Canonical Alignment

| Field | Value |
|-------|-------|
| **Decision ID** | C1A-OD-6 |
| **Subject** | Should [`add_admin_transaction()`](core/modules/admin_transactions/__init__.py:566) in the admin API use [`create_transaction()`](core/modules/fazle_payroll_engine/accounting.py:30) instead of its own direct INSERT? |
| **Current Evidence** | [`add_admin_transaction()`](core/modules/admin_transactions/__init__.py:566) directly INSERTs into [`fpe_cash_transactions`](core/modules/admin_transactions/__init__.py:588), then separately calls [`_adjust_ledger()`](core/modules/admin_transactions/__init__.py:545) → [`_upsert_ledger()`](core/modules/fazle_payroll_engine/accounting.py:190), and writes its own audit row. It is functionally similar to `create_transaction()` but uses a different `txn_ref` format and a different `audit_action`. This creates a parallel canonical-ish path within the FPE table. |
| **Possible Options** | **A.** Refactor `add_admin_transaction()` to call `create_transaction()` (changes `txn_ref` format and `audit_action`).<br>**B.** Leave as-is (functionally equivalent, architecturally different).<br>**C.** Defer alignment to Sprint-C1 and only document the parallel path. |
| **Risk per Option** | **A.** 🟡 MEDIUM — Consolidates to one canonical path; improves maintainability. Risk is changing `txn_ref` format for admin-created transactions, which may affect idempotency lookups or future reconciliation scripts.<br>**B.** 🟢 LOW — Safest. No behaviour change. Risk is continued parallel logic that must be updated whenever `create_transaction()` evolves.<br>**C.** 🟢 LOW — Same as B for C1B; defers the refactor. |
| **Owner Final Decision** | *(Pending — please select A, B, or C)* |
| **Next Sprint Impact (C1B)** | If **A** → C1B refactors the admin API endpoint to delegate to `create_transaction()`; small-to-medium change. If **B** → C1B only documents the parallel path. If **C** → C1B documents and defers; no code change. |

---

## Cross-Decision Dependency Map

```
C1A-OD-1 (Dual-Table Strategy)
    ├── influences C1A-OD-2 (if unify to FPE, PAID must use ledger)
    ├── influences C1A-OD-3 (if unify to FPE, audit gap solved by create_transaction)
    ├── influences C1A-OD-4 (if unify to FPE, sync bridge becomes obsolete)
    └── influences C1A-OD-6 (if unify to FPE, admin API should use create_transaction)

C1A-OD-5 (payment_correction) is mostly independent.
```

**Recommendation for Owner review order:** Decide **C1A-OD-1 first**, then C1A-OD-2, C1A-OD-3, C1A-OD-4, and C1A-OD-6 in any order. C1A-OD-5 can be decided independently.

---

## C1B Hold Status

| Condition | Status |
|-----------|--------|
| Phase-C1A approved | ✅ Yes |
| All 6 Owner Decisions finalized | ⛔ Pending |
| Per-decision impact analysis documented | ✅ Yes (this matrix) |
| C1B scope limited to approved decisions | ⛔ Pending Owner choices |
| Code branch created for C1B | ⛔ No — will not create until decisions are finalized |

---

## Sign-off

**Auditor:** Production Financial Auditor (AI Agent)
**Date:** 2026-06-29
**Status:** ✅ C1A Approved — C1B on Hold — Awaiting Owner decisions C1A-OD-1 through C1A-OD-6

> No code branch created. No C1B sprint started. This document is read-only analysis only.
