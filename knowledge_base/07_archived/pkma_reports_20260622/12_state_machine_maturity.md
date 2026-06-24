---
title: PKMA Report 12 — State Machine Maturity
owner: Fazle Core Admin
status: archived
last_verified: 2026-06-24
runtime_index: false
---

# PKMA Report 12 — State Machine Maturity

**Program:** PKMA v1.0
**Date:** 2026-06-22
**Mode:** READ-ONLY

---

## Purpose

Assess the maturity of every state machine identified in the Fazle AI Platform. A state machine is mature when all states, all transitions, all triggering commands, all actors, and all audit behaviors are documented, production-verified, and management-approved.

---

## State Machine Inventory (10 identified by PKCA)

---

## SM-01 — Payroll Run State Machine

**KB Article:** `05_workflows/salary_workflow.md`
**Maturity: Level 3 (Management Approved)**

| Dimension | Status |
|---|---|
| States | 6 documented: draft, reviewed, approved, locked, paid, cancelled |
| Transitions | 5 documented: submit, approve, lock, mark-paid, cancel |
| Source | `modules/payroll.ALLOWED_TRANSITIONS` |
| Admin Commands | 7 PAYROLL commands with syntax and required role (Wave-1) |
| Actors | accountant for all transitions; viewer for list |
| Audit Log | `wbom_payroll_approval_log` — every transition recorded |
| Idempotency | UNIQUE(employee_id, period_year, period_month) WHERE status != 'cancelled' |
| Production Verified | Yes — `ALLOWED_TRANSITIONS` dict read in PKCA |
| Management Decision | CON-01 (payment formula applies to locked→paid transition) |
| PKCA Report | 09_state_machine_coverage_report.md (SM-01) |

**Missing for Level 4:** No post-Wave-1 PKVC certification. Edge cases (partial month, rollback, correction-after-lock) not documented.
**Risk:** Medium — state machine is correct and documented; missing edge cases only.

---

## SM-02 — Escort Program State Machine

**KB Article:** `05_workflows/escort_workflow.md`
**Maturity: Level 3 (Management Approved)**

| Dimension | Status |
|---|---|
| States | 6 documented: draft, confirmed, Assigned, Running, Completed, Cancelled |
| Transitions | 5 documented |
| Source | `modules/escort`, `modules/escort_lifecycle` |
| Trigger | Client message → AI parser → draft; Admin ESCORTCONFIRM → confirmed |
| Cancellation Rule | Any pre-Completed state → Cancelled via ESCORTCANCEL |
| Stale Detection | 30-day Active/Assigned alert via scheduler |
| Production Verified | Yes (Wave-1) |
| Management Decision | CON-01–04 (financial rules attached to Completed transition) |
| PKCA Report | 09_state_machine_coverage_report.md (SM-02) |

**Missing for Level 4:** Sub-state of Running (escort_on_site vs escort_in_transit) not formalized. PKVC not run post-Wave-1.
**Risk:** Low — core states correct and production-verified.

---

## SM-03 — Payment Draft State Machine

**KB Article:** `05_workflows/payment_workflow.md`
**Maturity: Level 3 (Management Approved)**

| Dimension | Status |
|---|---|
| States | 4 documented: pending, sent, rejected, expired |
| Transitions | 3 documented: PAID/ADVANCE → sent; REJECT → rejected; 24h TTL → expired |
| Source | `modules/payment_workflow`, `modules/outbound` |
| Expiry Mechanism | combined_draft_cleanup scheduler job (hourly) |
| Production Verified | Yes (Wave-1) |
| Management Decision | HK-43 (advance keywords force draft); CON-01/02 (payment formula at sent transition) |
| PKCA Report | 09_state_machine_coverage_report.md (SM-03) |

**Missing for Level 4:** PKVC post-Wave-1. Rejected path notification behavior not documented.
**Risk:** Low.

---

## SM-04 — Recruitment Session State Machine

**KB Article:** `05_workflows/recruitment_workflow.md`
**Maturity: Level 3 (Management Approved)**

| Dimension | Status |
|---|---|
| States | 9 documented: new, step:name, step:age, step:area, step:position, step:experience, step:phone, scored, expired |
| Transitions | 7 forward + 1 expiry |
| Source | `modules/recruitment_flow` |
| Session TTL | 24h — HK-33 approved |
| Age Validation | 18–55 — BR-25 resolved |
| Invalid Input | System re-prompts rather than advancing |
| Production Verified | Yes (Wave-1) |
| Management Decisions | BR-25, HK-33, HK-34 (scoring), HK-36 (valid positions) |
| PKCA Report | 09_state_machine_coverage_report.md (SM-04) |

**Missing for Level 4:** Concurrent session handling (two devices same phone) not documented. DUP-06 unresolved.
**Risk:** Medium — concurrent session edge case is a real operational scenario.

---

## SM-05 — Attendance Draft State Machine

**KB Article:** `05_workflows/attendance_workflow.md`
**Maturity: Level 3 (Management Approved)**

| Dimension | Status |
|---|---|
| States | 4 documented: pending, approved, rejected, expired |
| Transitions | 3: APPROVE → wbom_attendance write; REJECT → closed; TTL → expired |
| Idempotency | ON CONFLICT UPDATE on (employee_id, attendance_date) |
| Multi-ID Approve | APPROVE 165 166 167 — documented (HK-38) |
| Bangla Digits | APPROVE ১৬৫ — documented (HK-39) |
| Production Verified | Yes (Wave-1) |
| Management Decisions | DUP-05 (attendance vs attendance_parser split); HK-38, HK-39 |
| PKCA Report | 09_state_machine_coverage_report.md (SM-05) |

**Missing for Level 4:** Role requirement for REJECT not explicitly stated. PKVC post-Wave-1.
**Risk:** Low.

---

## SM-06 — Employee Verification Session State Machine

**KB Article:** `05_workflows/payment_workflow.md`
**Maturity: Level 2 (Production Verified)**

| Dimension | Status |
|---|---|
| States | 5 documented: STEP_SELFIE, STEP_SLIP, STEP_METHOD, STEP_DONE, rejected |
| Transitions | 4: selfie → slip → method → done; or → rejected |
| Storage | fazle_draft_replies (intent='verification') |
| Identity Mismatch | Triggers rejection path; admin notified |
| Source | `modules/employee_verification` |
| Production Verified | Yes (Wave-1, partial) |
| Management Decision | None formal for verification flow |
| PKCA Report | 09_state_machine_coverage_report.md (SM-06) |

**Gap to Level 3:** No management decision specifically ratifying the 5-step verification requirement.
**Risk:** High — payment verification is a financial control; lack of formal approval is a governance gap.

---

## SM-07 — Draft Reply General State Machine

**KB Article:** Partial in `06_developer_system/automation_pipeline.md`
**Maturity: Level 2 (Production Verified)**

| Dimension | Status |
|---|---|
| States | 6 states: pending, approved, rejected, rejected_quality, rejected_fallback, expired |
| Coverage | rejected_quality and rejected_fallback (new states from draft_quality module) not yet in KB |
| Source | `modules/drafts`, `modules/draft_quality`, `modules/admin_commands` |
| Production Verified | Partially (draft_quality states added post-audit but not fully documented) |
| Management Decision | None for draft quality gate |
| PKCA Report | 09_state_machine_coverage_report.md (SM-07) |

**Gap to Level 3:** Draft quality gate states (rejected_quality, rejected_fallback) not documented in KB. No management decision for quality gate behavior.
**Risk:** High — admins may not understand why drafts are rejected with these new states.

---

## SM-08 — Admin User State Machine

**KB Article:** `02_admin_knowledge/admin_role_management.md` (brief)
**Maturity: Level 2 (Production Verified)**

| Dimension | Status |
|---|---|
| States | 2: active, disabled |
| Transitions | 1: USER REMOVE → disabled (soft-disable) |
| Source | `modules/rbac` |
| Soft-disable | Record preserved for audit; user loses access |
| Production Verified | Yes (Wave-1 — USER REMOVE documented) |
| Management Decision | None formal |
| PKCA Report | 09_state_machine_coverage_report.md (SM-08) |

**Risk:** Low — simple two-state machine; well-understood behavior.

---

## SM-09 — Outbound Queue State Machine

**KB Article:** `06_developer_system/automation_pipeline.md`
**Maturity: Level 2 (Production Verified)**

| Dimension | Status |
|---|---|
| States | 5 documented: pending, sending, sent, failed, dlq |
| Transitions | 4: sweep → sending; confirm → sent; error → failed; max_attempts → dlq |
| Backoff | Exponential per attempt |
| DLQ Alert | Every 15 min via dlq_alert scheduler job |
| Idempotency | idempotency_key prevents duplicate enqueue |
| Source | `modules/outbound` |
| Production Verified | Yes (Wave-1) |
| Management Decision | None formal |
| PKCA Report | 09_state_machine_coverage_report.md (SM-09) |

**Risk:** Medium — DLQ handling is operationally critical; no management decision on retry policy or DLQ response time.

---

## SM-10 — FPE Processing State Machine

**KB Article:** None (no fpe_overview.md exists)
**Maturity: Level 0 (Unknown)**

| Dimension | Status |
|---|---|
| States | 6: pending, parsing, parsed, accounting, done, failed, skipped |
| Source | `modules/fazle_payroll_engine/models.py.ProcessingStatus` |
| KB Article | Not created (Wave-2 scope) |
| Production Verified | No — not documented |
| Management Decision | None |
| PKCA Report | 09_state_machine_coverage_report.md (SM-10) |

**Risk:** Critical — FPE processes financial transactions. Its state machine is entirely undocumented.

---

## State Machine Maturity Summary

| State Machine | Level | Risk | Missing for Next Level |
|---|---|---|---|
| SM-01: Payroll Run | 3 | Medium | PKVC + edge cases |
| SM-02: Escort Program | 3 | Low | PKVC |
| SM-03: Payment Draft | 3 | Low | PKVC |
| SM-04: Recruitment Session | 3 | Medium | DUP-06 + concurrent session edge case |
| SM-05: Attendance Draft | 3 | Low | PKVC |
| SM-06: Employee Verification | 2 | High | Management decision for verification flow |
| SM-07: Draft Reply General | 2 | High | quality gate states + management decision |
| SM-08: Admin User | 2 | Low | Management decision |
| SM-09: Outbound Queue | 2 | Medium | Management decision for retry policy |
| SM-10: FPE Processing | 0 | Critical | Create fpe_overview.md (Wave-2) |

**State Machine Domain Average: 2.4 / 5.0**
**Level 3 count: 5 / 10**
**Level 0 count: 1 / 10 (FPE — critical risk)**

---

*PKMA v1.0 | READ-ONLY | 2026-06-22*
