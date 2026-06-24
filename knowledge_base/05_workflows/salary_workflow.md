---
title: Salary Workflow
owner: Fazle Core Admin
status: active
last_verified: 2026-06-24
runtime_index: true
---

# Salary Workflow

## Flow
Attendance Approved -> Leave/Late Review -> Advance Deduction -> Bonus Eligibility -> Transport/Allowance -> Salary Calculation -> Admin/Accountant Payment Handling -> Ledger Update.

## Inputs
Approved attendance, leave, late records, advance payments, role salary package, transport/conveyance, bonus eligibility.

## Cross References
- ../04_business_rules/salary_business_rules.md
- payment_workflow.md

---

## Payroll State Machine

### Purpose
Payroll runs follow a strict state machine with immutable transitions. Each transition requires the appropriate RBAC role and is permanently logged.

### States and Transitions

```
draft → reviewed → approved → locked → paid
  ↓         ↓          ↓         ↓
  └─────────┴──────────┴─────────┘ → cancelled (from any non-paid state)
```

| From State | To State | Command | Required Role | Action |
|---|---|---|---|---|
| (create) | draft | `PAYROLL COMPUTE` | accountant | Compute payroll run for period/employee |
| draft | reviewed | `PAYROLL SUBMIT` | accountant | Submit for review |
| reviewed | approved | `PAYROLL APPROVE` | accountant | Approve the run |
| approved | locked | `PAYROLL LOCK` | accountant | Lock amounts (no further edits) |
| locked | paid | `PAYROLL PAID` | accountant | Mark as paid; finalize |
| any → | cancelled | `PAYROLL CANCEL` | accountant | Cancel from any non-paid state |

**Business Rule:** Transitions not in `ALLOWED_TRANSITIONS` are rejected. A locked payroll run cannot be edited. A paid run cannot be cancelled.

**Audit:** Every transition is recorded in `wbom_payroll_approval_log`:
- Fields: `run_id`, `action`, `actor`, `from_status`, `to_status`, `reason`, `payload_json`

**Idempotency:** Payroll compute is idempotent on `UNIQUE(employee_id, period_year, period_month) WHERE status != 'cancelled'`. Running PAYROLL COMPUTE twice for the same period and employee does not create a duplicate.

**Source Module:** `modules/payroll`
**Source Function:** `ALLOWED_TRANSITIONS`, `compute_run()`, `compute_all_for_period()`
**PKCA Report:** 09_state_machine_coverage_report.md (SM-01), 10_hidden_rule_coverage_report.md (HK-26, HK-27)
**Management Authority:** Production evidence; documented 2026-06-22

---

## PAYROLL Admin Commands

| Command | Syntax | Required Role | Action |
|---|---|---|---|
| PAYROLL COMPUTE | `PAYROLL COMPUTE <YYYY-MM> [employee_id]` | accountant | Compute payroll run |
| PAYROLL SUBMIT | `PAYROLL SUBMIT <run_id>` | accountant | draft → reviewed |
| PAYROLL APPROVE | `PAYROLL APPROVE <run_id>` | accountant | reviewed → approved |
| PAYROLL LOCK | `PAYROLL LOCK <run_id>` | accountant | approved → locked |
| PAYROLL PAID | `PAYROLL PAID <run_id> <amount> bkash\|nagad\|cash [ref=X]` | accountant | locked → paid |
| PAYROLL CANCEL | `PAYROLL CANCEL <run_id> <reason>` | accountant | → cancelled |
| PAYROLL LIST | `PAYROLL LIST <YYYY-MM> [status]` | viewer | List runs for period |

**Business Rule:** All PAYROLL commands require at minimum accountant role. Viewer can list but cannot change state.

**Source Module:** `modules/admin_commands`
**PKCA Report:** 12_command_coverage_report.md

---

## Daily Auto-Compute

**Business Rule:** The `daily_payroll_compute` scheduler job runs at 02:00 daily (`PAYROLL_AUTO_COMPUTE_HOUR` env), computing payroll for all active employees for the current month. A one-line summary is sent to the primary admin.

**Source Module:** `modules/scheduler`
**Source Function:** `job_daily_payroll()`
**PKCA Report:** 07_scheduler_coverage_report.md
