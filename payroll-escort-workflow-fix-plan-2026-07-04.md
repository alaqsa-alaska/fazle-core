# Payroll Escort Workflow Fix Plan

**Plan date:** 2026-07-04  
**Objective:** Unify payroll, employee finance, escort lifecycle, attendance, and payment operations under one canonical write path with no data loss, no double entry, and full auditability.

---

## 1. Target Outcomes

1. One source of truth per business entity and one canonical write path per business action.
2. Frontend pages can search and update employee, attendance, escort program, advance, and payment status from the same DB without duplicate entry.
3. Every approved money event is immutable, idempotent, and linked to employee + program + approval + source message.
4. Historical data remains intact; migration is additive and reversible.
5. Operational dashboards can detect drift, stale drafts, missing links, and sync gaps automatically.

---

## 2. Non-Negotiable Safety Rules

1. No destructive migration on live tables in early phases.
2. No hard delete for financial or lifecycle records.
3. Every write must have actor, source, timestamp, and correlation ID.
4. All new financial writes must be idempotent.
5. Dual-read or compatibility-view period is required before retiring legacy write paths.

---

## 3. Canonical Data Model (Same DB, Unified Usage)

Keep PostgreSQL as the same database. Use schema unification, not multi-DB split.

### 3.1 Canonical ownership

| Domain | Canonical table(s) | Notes |
| --- | --- | --- |
| Employee identity | `wbom_employees` + `fpe_employees` linkage | Keep both during transition; enforce strong link constraints |
| Escort lifecycle | `wbom_escort_programs` | Operational source for assignment and release lifecycle |
| Immutable money events | `fpe_cash_transactions` | Single canonical money event table |
| Employee ledger | `fpe_employee_ledger` | Derived from immutable events |
| Payment approval workflow | `fazle_payment_drafts` | Approval queue and traceability |
| Roster display | `escort_roster_entries` | Projection only, not independent source |
| Attendance | `wbom_attendance` | Operational attendance fact table |

### 3.2 Mandatory linkage fields

1. `fpe_employees.wbom_employee_id` must be present for operationally active employees.
2. `fpe_cash_transactions.program_id` required for escort settlement-related events.
3. `fazle_payment_drafts.transaction_id` and `txn_ref` required after approval completion.
4. `wbom_escort_programs.escort_employee_id` required for confirmed and completed records.

### 3.3 Required DB constraints and indexes (additive)

1. Unique index on normalized employee mobile identity path.
2. Partial unique index for idempotency keys in transaction creation path.
3. FK-safe indexes for `program_id`, `employee_id`, `wbom_employee_id`, `transaction_id`.
4. Check constraints on status enums to avoid case mismatch (`Active/Inactive` normalized policy).

---

## 4. Unified Write Path Design

### 4.1 Employee create/update

1. Write operational employee in `wbom_employees`.
2. Resolve or create linked `fpe_employees` in same transaction boundary where possible.
3. Persist normalization artifacts and alias evidence.
4. Reject updates if identity would break link integrity.

### 4.2 Escort order to assignment

1. Parse and create draft `wbom_escort_programs`.
2. On admin confirmation, resolve `escort_employee_id`.
3. Auto-sync projection to `escort_roster_entries`.
4. Log lifecycle transition event.

### 4.3 Release settlement

Single transaction flow:

1. Validate active program and employee linkage.
2. Close program and write release fields.
3. Backfill attendance.
4. Compute structured settlement components.
5. Create or update escort payment draft.
6. Upsert roster projection.

### 4.4 Payment approval (critical unification)

1. Consolidate `PAID`, `ADVANCE`, and `APPROVED` commands to one internal approval service.
2. Approval service must always create canonical `fpe_cash_transactions` event.
3. Draft status progression must be single-state-machine only.
4. Notification send status must be separate from accounting finality status.

### 4.5 Payroll paid transition

1. Payroll run can remain in `wbom_payroll_runs` for process state.
2. On paid transition, create canonical payroll payout transaction event in `fpe_cash_transactions`.
3. Link payroll run ID and period in metadata for traceability.

---

## 5. Frontend Unification Plan

## 5.1 New operational workspace page

Create one unified page: `Operations Finance Console`

### Core tabs

1. Employee 360
2. Attendance
3. Escort Programs
4. Payment Drafts
5. Cash and Ledger
6. Reconciliation and Alerts

### 5.2 Employee 360 requirements

Search by:

1. Employee name
2. Mobile
3. Employee ID
4. Program ID

Display in one panel:

1. WBOM profile + status
2. FPE identity linkage status
3. Active programs and last completed program
4. Attendance summary (7/30 days)
5. Advances taken this month
6. Cash received this month
7. Net payable trend and last payment references

Actions:

1. Safe profile update
2. Link or relink FPE identity with audit reason
3. Open unresolved mismatch queue

### 5.3 Attendance page requirements

1. Daily list by employee and date range.
2. Program-linked attendance trace.
3. Missing attendance detector for completed programs.
4. Controlled correction action with audit log.

### 5.4 Escort Programs page requirements

1. Draft, confirmed, running, completed lifecycle filter.
2. Must show `escort_employee_id` resolution health.
3. Program timeline with assignment and release events.
4. Sync-to-roster health indicator.
5. Disallow roster-only edits from this page.

### 5.5 Payment Drafts page requirements

1. Single queue for advance, escort settlement, and payroll payment drafts.
2. One approval action that always uses canonical backend path.
3. Display expected vs approved amount diff.
4. Show resulting transaction ID and txn_ref after approval.
5. Show delivery status to accountant/employee separately.

### 5.6 Cash and Ledger page requirements

1. Unified transaction list from `fpe_cash_transactions`.
2. Filter by employee/program/date/category/source.
3. Program settlement detail panel.
4. Reversal and correction actions with strict audit controls.

### 5.7 Reconciliation and Alerts page requirements

1. WBOM-FPE employee linkage mismatch count.
2. Unsynced program to roster count.
3. Completed program without payment draft count.
4. Payroll paid runs without canonical transaction count.
5. Stale and expired draft health metrics.

---

## 6. Backend API and Service Plan

### 6.1 Service boundaries

1. `EmployeeService`
2. `EscortProgramService`
3. `SettlementService`
4. `PaymentApprovalService`
5. `PayrollService`
6. `ReconciliationService`

### 6.2 Required APIs (add or harden)

1. `GET /api/ops/employee-360/{id_or_mobile}`
2. `GET /api/ops/attendance` and `POST /api/ops/attendance/correct`
3. `GET /api/ops/programs` and `POST /api/ops/programs/{id}/sync`
4. `GET /api/ops/payment-drafts` and `POST /api/ops/payment-drafts/{id}/approve`
5. `GET /api/ops/transactions`
6. `GET /api/ops/reconciliation/summary`
7. `POST /api/ops/reconciliation/run`

### 6.3 Idempotency and audit requirements

1. Approve endpoints require idempotency key.
2. Every mutating endpoint writes audit row with before and after payload.
3. Outbound message queue writes delivery receipt state; no silent send failures.

---

## 7. DB Migration Plan (No Data Loss)

### 7.1 Phase A: additive schema changes

1. Add missing indexes and constraints without dropping tables.
2. Add normalized status checks and compatibility views.
3. Add correlation IDs and source metadata columns where absent.

### 7.2 Phase B: data reconciliation jobs

1. Backfill `fpe_employees.wbom_employee_id` where deterministic match exists.
2. Backfill missing `escort_employee_id` on confirmed/completed programs.
3. Backfill roster projection for all operational programs.
4. Backfill canonical transactions for legacy WBOM-only events where safe.

### 7.3 Phase C: dual-run validation

1. Run old and new read views side by side.
2. Compare totals by employee, date, and category.
3. Produce signed reconciliation report before cutover.

### 7.4 Phase D: controlled write cutover

1. Route all payment approvals to unified canonical path.
2. Keep legacy command aliases but map to same service.
3. Keep rollback toggle to old command handler for limited window.

### 7.5 Phase E: legacy path retirement

1. Freeze direct roster mutation APIs or reroute through operational service.
2. Deprecate non-canonical payment state transitions.
3. Lock outdated admin command behaviors after observation period.

---

## 8. Search and Update Flow (Single DB, No Double Entry)

### 8.1 Global lookup flow

1. User enters employee name, mobile, employee ID, or program ID.
2. Backend resolves canonical employee identity.
3. Backend aggregates WBOM + FPE + roster projection in one response DTO.
4. UI displays entity health badges before allowing updates.

### 8.2 Update flow guardrails

1. Every update endpoint validates linkage preconditions.
2. Financial updates cannot proceed without canonical employee resolution.
3. Program settlement updates require active or completed program context.
4. Conflict detection with optimistic versioning for concurrent edits.

### 8.3 Preventing double entry

1. Disable free-form duplicate create on roster side.
2. Force all payment approvals through one action handler.
3. Add dedup keys for draft-to-transaction conversion.
4. Add duplicate detector job for suspicious same-day same-amount same-employee events.

---

## 9. Rollback and Recovery Strategy

1. Keep DB backup snapshot before each migration phase.
2. Use feature flags per write path:
   - `UNIFIED_PAYMENT_APPROVAL_ENABLED`
   - `PAYROLL_CANONICAL_POSTING_ENABLED`
   - `ROSTER_PROJECTION_WRITE_GUARD_ENABLED`
3. If drift detected, disable new writer flag and continue read-only verification.
4. Never delete legacy records during rollout window.

---

## 10. Implementation Phases and Timeline

### Phase 1 (Week 1 to 2): stabilization

1. Fix known status-case mismatch bug in employee cross-lookup.
2. Unify admin approval command internals.
3. Add reconciliation counters and health endpoints.

### Phase 2 (Week 3 to 4): canonical posting completion

1. Add canonical posting on payroll paid transition.
2. Add strict audit + idempotency enforcement for all financial actions.
3. Release payment draft queue UI with unified approval action.

### Phase 3 (Week 5 to 6): frontend unification

1. Ship Employee 360 and Program Timeline pages.
2. Ship Attendance and Reconciliation pages.
3. Reduce direct roster mutation capabilities.

### Phase 4 (Week 7 to 8): migration hardening

1. Execute backfills and compare reports.
2. Cut over remaining write paths.
3. Freeze and deprecate legacy flows.

---

## 11. Acceptance Criteria

1. 100 percent approved financial events produce canonical FPE transactions.
2. 0 payroll paid runs without canonical transaction reference.
3. 0 confirmed/completed programs without `escort_employee_id`.
4. 0 operational writes that require manual dual entry across WBOM and FPE.
5. Roster totals reproducible from operational source with deterministic sync.
6. Reconciliation dashboard shows zero critical drift for 7 consecutive days.

---

## 12. Step 2 Deliverable Summary

This plan defines:

1. Unified frontend operations pages for employee, attendance, escort programs, advances, and cash received.
2. Backend service and API unification to enforce one write path.
3. Same-DB migration strategy with additive changes, backfills, dual-run validation, and rollback controls.
4. No-data-loss execution model with immutable financial history and full audit trails.
