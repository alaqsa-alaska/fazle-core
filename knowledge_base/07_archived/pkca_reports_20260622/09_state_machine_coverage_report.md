---
title: PKCA Report 09: State Machine Coverage Report
owner: Fazle Core Admin
status: archived
last_verified: 2026-06-24
runtime_index: false
---

# PKCA Report 09: State Machine Coverage Report

**Date:** 2026-06-22
**Mode:** Read-Only Analysis

---

## State Machine Inventory

### SM-01: Payroll Run State Machine — 0% Covered

**Source:** `modules/payroll.ALLOWED_TRANSITIONS`

```
                    cancelled ←─────────────────────────┐
                   ↗ ↗ ↗ ↗                             │
draft → reviewed → approved → locked → paid            │
  └──────────────────────────────────────┘ (cancel from any non-paid)
```

| Transition | Function | Required Actor |
|---|---|---|
| draft → reviewed | `submit_run()` | operator+ |
| reviewed → approved | `approve_run()` | admin+ |
| approved → locked | `lock_run()` | admin+ |
| locked → paid | `mark_paid()` | accountant+ |
| any → cancelled | `cancel_run()` | accountant+ |

**Audit:** Every transition writes to `wbom_payroll_approval_log` (run_id, action, actor, from_status, to_status, reason, payload_json)

**KB Coverage:** `05_workflows/salary_workflow.md` lists "Attendance Approved → Leave/Late Review → Advance Deduction → ... → Ledger Update" but does NOT document the 6-state payroll machine or ALLOWED_TRANSITIONS.

**Enrichment Target:** `05_workflows/salary_workflow.md` — add 6-state machine diagram and transition table.

---

### SM-02: Escort Program State Machine — 30% Covered

**Source:** `modules/escort`, `modules/escort_lifecycle`

```
draft → confirmed → Assigned → Running → Completed
              ↘ Cancelled (from any pre-Completed state)
```

| State | Meaning |
|---|---|
| draft | Client order received, escort not yet assigned |
| confirmed | Admin assigned escort via ESCORTCONFIRM |
| Assigned | Program assigned, escort not yet on site |
| Running | Escort on active duty |
| Completed | Release confirmed, attendance backfilled, payment drafted |
| Cancelled | Program cancelled before completion |

**KB Coverage:** `05_workflows/escort_workflow.md` describes "Escort Assignment → Duty Start → Release Slip → Payment Draft" flow which partially maps to the states, but doesn't document all state names or cancellation rule.

**Enrichment Target:** `05_workflows/escort_workflow.md` — add explicit state diagram.

---

### SM-03: Payment Draft State Machine — 0% Covered

**Source:** `modules/payment_workflow`, `modules/outbound`

```
pending → sent (after admin PAID/ADVANCE command + finalize_payment())
pending → rejected (after admin REJECT command)
pending → expired (after 24h TTL cleanup job)
```

**KB Coverage:** `05_workflows/payment_workflow.md` describes the payment flow but not the draft state names or TTL expiry.

**Enrichment Target:** `05_workflows/payment_workflow.md` — add draft state machine.

---

### SM-04: Recruitment Session State Machine — 0% Covered

**Source:** `modules/recruitment_flow`

```
new → step:name → step:age → step:area → step:position → step:experience → step:phone → scored
                                                                                      ↘ expired (24h TTL)
```

**KB Coverage:** `05_workflows/recruitment_workflow.md` describes the 6-step collection but not the session state names or expiry.

**Enrichment Target:** `05_workflows/recruitment_workflow.md` — add session state machine.

---

### SM-05: Attendance Draft State Machine — 0% Covered

**Source:** `modules/attendance`, `modules/admin_commands._cmd_approve`

```
pending → approved (admin APPROVE <id>) → saved to wbom_attendance
        → rejected (admin REJECT <id>)
        → expired (draft_ttl_cleanup job)
```

**KB Coverage:** `05_workflows/attendance_workflow.md` mentions "Admin Review → Attendance Save" but not state names or expiry.

**Enrichment Target:** `05_workflows/attendance_workflow.md` — add state machine.

---

### SM-06: Employee Verification Session State Machine — 0% Covered

**Source:** `modules/employee_verification`

```
→ STEP_SELFIE ("pending_selfie")
   → STEP_SLIP ("pending_slip") — after employee sends selfie image
      → STEP_METHOD ("pending_payment_method") — after employee sends slip
         → STEP_DONE ("verified") — after payment method confirmed
→ rejected (if session conflict)
```

**KB Coverage:** `01_employee_knowledge/faq_employee.md` mentions the selfie + slip + payment method steps but not as a formal state machine.

**Enrichment Target:** `05_workflows/payment_workflow.md` or `02_admin_knowledge/admin_payment_handling.md` — add verification step states.

---

### SM-07: Draft Reply General State Machine — 5% Covered

**Source:** `modules/drafts`, `modules/admin_commands._cmd_approve`

```
pending → approved (admin APPROVE) → sent_at timestamp set
        → rejected (admin REJECT)
        → rejected_quality (draft_quality gate fails)
        → rejected_fallback (LLM fallback detected)
        → expired (draft_ttl_cleanup, configurable TTL)
```

**New States from draft_quality module (not in original audit):**
- `rejected_quality` — failed quality gate
- `rejected_fallback` — LLM fallback exact match detected

**KB Coverage:** `06_developer_system/automation_pipeline.md` mentions "Expire old pending drafts after 48 hours" but not all state names.

**Enrichment Target:** `06_developer_system/automation_pipeline.md` — add full draft state machine.

---

### SM-08: Admin User State Machine — 0% Covered

**Source:** `modules/rbac`

```
active → disabled (USER REMOVE command by superadmin)
```

**KB Coverage:** `02_admin_knowledge/admin_role_management.md` mentions "Delete or deactivate" but not the state machine.

---

### SM-09: Outbound Queue State Machine (NEW — Not in Original Audit) — 0% Covered

**Source:** `modules/outbound`

```
pending → sending (sweep picks up row)
        → sent (bridge confirmed delivery, external_id set)
        → failed (bridge error, retry after exp backoff)
        → dlq (max_attempts exceeded)
```

**KB Coverage:** 0% — outbound queue is entirely undocumented.

**Enrichment Target:** `06_developer_system/automation_pipeline.md` — add outbound queue states.

---

### SM-10: FPE Processing State Machine (NEW) — 0% Covered

**Source:** `modules/fazle_payroll_engine.models.ProcessingStatus`

```
pending → parsing (message_processor_worker picks up)
        → parsed (parser result stored)
        → accounting (accounting_worker picks up)
        → done (transaction created, ledger updated)
        → failed (error after MAX_ATTEMPTS=5)
        → skipped (not a payment message)
```

**KB Coverage:** 0%

---

## State Machine Coverage Summary

| State Machine | States | Transitions | KB Coverage |
|---|---|---|---|
| SM-01: Payroll Run | 6 | 5 | 0% |
| SM-02: Escort Program | 6 | 5 | 30% |
| SM-03: Payment Draft | 4 | 3 | 0% |
| SM-04: Recruitment Session | 7 steps + scored/expired | 7 | 0% |
| SM-05: Attendance Draft | 4 | 3 | 0% |
| SM-06: Employee Verification | 5 steps | 4 | 0% |
| SM-07: Draft Reply General | 6 (incl. quality states) | 5 | 5% |
| SM-08: Admin User | 2 | 1 | 0% |
| SM-09: Outbound Queue | 5 | 4 | 0% |
| SM-10: FPE Processing | 6 | 5 | 0% |

**Average State Machine Coverage: 3.5%**

**Note:** 2 new state machines (SM-09, SM-10) discovered that were not in the original PKM audit.
