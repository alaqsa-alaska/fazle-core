# Payroll Engine, Employee Finance & Escort Operations Audit

**Audit date:** 2026-07-04  
**Mode:** Read-only repository code inspection (no destructive action)  
**Scope:** Employee onboarding, payroll, salary/advance/payment flows, accountant posting, escort order lifecycle, roster projection, release settlement, OCR/review path, and WBOM-FPE consistency

---

## 1. Executive Summary

The system is not in the same state as the original 2026-06-13 report. Several P0/P1 issues were fixed in code, especially around release finalization safety and canonical transaction posting for payment draft approval.

At the same time, structural risks remain:

- Two parallel admin payment approval command paths still exist (`PAID` and `APPROVED`) with different draft-state behavior.
- Payroll paid-state transition still does not create canonical FPE payout transactions.
- Roster is still both projection and directly editable surface.
- A WBOM status-case mismatch bug remains in FPE cross-lookup (`status != 'inactive'` vs WBOM `Active/Inactive`).
- Internal notification decoupling from safe mode is strong in bridge poller path, but not fully consistent in all entry paths.

Overall assessment:

- **Release-to-payment operational safety:** Improved significantly
- **Canonical accounting reliability:** Improved for payment drafts, incomplete for payroll paid runs
- **Roster source-of-truth discipline:** Partially improved, still ambiguous
- **Identity unification (WBOM/FPE):** Improved but incomplete
- **Architecture maturity:** Better than June, still fragmented

---

## 2. Evidence Method

This report is based on current source verification of key modules and tests, including:

- `modules/escort_lifecycle`
- `modules/payment_workflow`
- `modules/draft_approval`
- `modules/payroll`
- `modules/escort`
- `modules/escort_roster`
- `modules/fazle_payroll_engine/employee`
- `modules/payment_ingest/wbom_fpe_sync`
- `modules/bridge_poller`, `app/main`, `app/config`

Important note:

- Live database counts from the 2026-06-13/2026-06-25 snapshots were not re-counted in this run.

---

## 3. Current State Matrix (2026-07-04)

| Area | June concern | Current state | Status |
| --- | --- | --- | --- |
| Release finalize safety | Partial completion risk | Close + attendance + payment draft + roster upsert run in one DB transaction and require admin confirmation | ✅ Fixed (code path) |
| Release schema mismatch | `shift_type` and `contact_id` breakage | Replaced with valid fields and normalized mobile lookup | ✅ Fixed |
| Escort payment draft structure | Food/conveyance not reliably persisted | Draft stores `gross_amount`, `food_bill`, `conveyance`, `advance_deduction`, `expected_amount` | ✅ Fixed |
| Payment posting target | WBOM-only path concern | `finalize_payment()` now creates canonical FPE transaction via `create_transaction()` | ✅ Fixed for payment drafts |
| Payroll paid posting | Paid run not canonicalized | `mark_paid()` transitions state only; no canonical FPE transaction creation | ⚠️ Open |
| Roster status mapping | `confirmed` mapped to `draft` | `confirmed` now mapped as first-class roster status | ✅ Fixed |
| Roster auto-sync on lifecycle | Missing on key transitions | Auto-sync exists on draft save and confirmation; release path upserts roster | ✅ Improved |
| Roster source-of-truth | Direct roster edits and synthetic program IDs | Still supported in code | ⚠️ Open |
| Internal notifications vs safe mode | Suppression risk | Bridge poller uses internal-notification gate independent from customer autoreply; not fully uniform across all app entry points | ⚠️ Partial |
| WBOM-FPE employee status filter | Case mismatch (`inactive` vs `Inactive`) | Bug still present in WBOM cross-lookup filter | ❌ Open |
| WBOM->FPE sync adapter | Outdated concern | Adapter modernized and idempotent backfill exists | ✅ Fixed (adapter) |
| Automatic adapter invocation | Not wired everywhere | Still not globally wired to every legacy WBOM write path (especially payroll paid) | ⚠️ Open |

---

## 4. What Was Fixed Since Prior Audit

### 4.1 Transactional Release Finalization

Release finalization now follows a safer sequence under one DB transaction:

1. Find active program
2. Validate/compute days and shifts
3. Close program
4. Backfill attendance
5. Create/update escort payment draft
6. Upsert roster projection

Result: the prior partial-close risk is materially reduced.

### 4.2 Admin-Gated Financial Release

Release execution now requires explicit admin confirmation. Employee text/OCR alone no longer finalizes monetary actions.

### 4.3 Schema/Query Breakages Resolved

- No dependency on missing `shift_type`
- No dependency on missing `wbom_employees.contact_id` for release confirmation lookup

### 4.4 Canonical Transaction Write in Payment Workflow

Draft payment approval through `finalize_payment()` now resolves employee and writes through canonical FPE accounting (`create_transaction()`), with transaction reference persistence in draft row.

### 4.5 Roster Lifecycle Mapping Improvement

`confirmed` is now mapped correctly to roster `confirmed` (not forced to `draft`).

### 4.6 Payroll Summary Read Path Improvement

Employee payroll summary logic now reads canonical FPE transaction history for payment amounts and totals.

---

## 5. What Is Still Open (High Priority)

### 5.1 Payment Approval Command Split (PAID vs APPROVED)

There are two active command families:

- `APPROVED` path through `modules/draft_approval` (designed canonical flow)
- `PAID`/`ADVANCE` path through `modules/payment_workflow` + extra draft status updates

Risk:

- Divergent draft lifecycle states (`sent`, `approved`, `completed`) and operator confusion
- Harder reconciliation and support

### 5.2 Payroll Paid-State Is Not Canonicalized

`modules/payroll.mark_paid()` updates payroll state/audit only. It does not create immutable FPE transaction events for salary settlement.

Risk:

- Payroll paid status and canonical ledger can drift
- Reporting parity issues

### 5.3 Roster Still Writable as an Independent Surface

Roster module still allows direct edits, manual creation, and draft deletion paths that may not route through operational escort program service.

Risk:

- Double source-of-truth
- Drift between `wbom_escort_programs` and `escort_roster_entries`

### 5.4 WBOM Status Case Bug in FPE Cross Lookup

WBOM cross-lookup still contains `status != 'inactive'` while WBOM uses `Active/Inactive`.

Risk:

- Inactive WBOM rows may be considered as active in linkage logic

### 5.5 Notification Policy Is Not Uniform in Every Entry Path

Bridge poller path uses internal notification queue behavior independent from customer auto-reply. Some app-level notifier usage still checks customer safe mode directly.

Risk:

- Inconsistent admin delivery depending on entry route

### 5.6 Settlement Formula Unification Still Incomplete

Release draft and roster are more structured, but payroll still uses fixed per-program rate and does not fully consume approved settlement components as canonical salary additions/deductions.

---

## 6. Architecture Reality Check (2026-07-04)

Current reality is improved but still overlapping:

- WBOM operational tables remain active for workflow control
- FPE is canonical for immutable money event records in many, but not all, terminal payment paths
- Roster is projection plus direct editable interface
- Draft systems are active and useful but have mixed command pathways

Conclusion:

- This is now a **partially unified** system, not yet a **single write-path** system.

---

## 7. Immediate P0/P1 Recommendations

### P0

1. **Unify financial approval command path now**
   - Keep one command contract (`APPROVED`-style canonical path) and make `PAID/ADVANCE` aliases call the same internal service.

2. **Post payroll paid events to canonical FPE ledger**
   - When `mark_paid()` succeeds, create exactly one immutable transaction event with payroll period references.

3. **Fix WBOM inactive status comparison bug**
   - Replace lowercase inequality checks with explicit WBOM status normalization (`Active/Inactive`) in cross-system linkage queries.

### P1

1. **Make roster projection-first**
   - Keep UI edits but route all mutating actions through operational escort service.

2. **Unify settlement source for payroll + release + roster**
   - Compute from one structured settlement model with versioned DB configuration rates.

3. **Standardize internal notification delivery path**
   - Ensure admin/accountant notifications never depend on customer auto-reply gate in any path.

4. **Run controlled data reconciliation**
   - Backfill old missing `escort_employee_id`, stale drafts, unsynced roster rows, and payroll/ledger parity checks.

---

## 8. Updated Final Assessment

The most dangerous June breakages in release and payment drafting were addressed in code. Operational reliability has improved.

The next highest-value work is now **unification and consistency hardening**, not feature expansion:

- one canonical money write path for all approvals and payroll paid transitions,
- one source-of-truth discipline between operational escort records and roster projection,
- one consistent admin notification delivery policy,
- and one clean WBOM/FPE identity linkage behavior without case-related mismatch bugs.

Until these are completed, the system remains vulnerable to data drift, duplicate business handling, and reconciliation overhead.
