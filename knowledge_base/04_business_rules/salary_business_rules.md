---
title: Salary Business Rules
owner: Fazle Core Admin
status: active
last_verified: 2026-07-15
runtime_index: true
---

# Salary Business Rules

## Security Guard Salary
Probationary package may be up to ৳17,000/month. Permanent package may be up to ৳24,700/month.

## Escort Salary
Training: 45 days paid ৳10,000-৳15,000/month. After training: duty-based ৳12,000-৳18,000 range.

## Payment Date
Normal salary is paid between the 10th and 12th. Unauthorized absence during the 1st-10th may delay salary to the 20th-25th.

## Deductions
- 1 unauthorized absent day may cause up to 2 days deduction.
- 3 consecutive late days may cause 1 day salary deduction.
- Advance is deducted from final salary/payment.

## Clarification
Client service charge such as ৳13,000 is billing/service charge, not employee salary.

## Settlement Formula — Office-Staff Basic Salary Proration (added 2026-07-15)

There are two independent, deliberately separate pay formulas in `modules/payroll`:

- **Escort-program pay** — `per_program_rate × completed_program_days` (default rate
  ৳400/day, PAY-01). This is tied to real completed escort-duty days and was never a gap;
  it stays flat-rate regardless of the fix below.
- **Basic-salary component** (office staff only) — was previously always the full flat
  monthly `basic_salary`, with no proration for actual days worked. Fixed 2026-07-15:
  `basic_salary ÷ calendar_days_in_month × approved_duty_days`. `calendar_days_in_month`
  is the real length of the month (28/29/30/31, leap-year aware). `approved_duty_days`
  comes only from `wbom_attendance` rows with `entry_source IN ('manual_office',
  'message_parser')` — WhatsApp-originated escort/release attendance never counts toward
  office-staff basic pay.

**Scope of the fix:** this proration only applies via
`modules.payroll.compute_run(..., prorate_basic=True)`, called exclusively from
`compute_all_for_period_office_only()` — itself gated behind
`settings.office_payroll_authoritative_enabled` (default `False`) and
`modules.payroll.eligibility.classify_office_staff()` (escort-designated employees are
structurally excluded and always paid the old way). The currently-authoritative
`compute_all_for_period()` path (called daily by `job_daily_payroll`) is unaffected —
every employee it touches still gets the full flat `basic_salary`, unprorated, exactly as
before.

**Why the authoritative flag stays off:** per `docs/NEXT_IMPLEMENTATION_QUEUE.md`, flipping
`office_payroll_authoritative_enabled=true` requires real production data showing at least
one structurally-eligible office employee with real approved attendance — as of 2026-07-15
there are 0 of ~188 employees eligible (184 are Escort-designated; the remaining 4 each fail
the active/salary/placeholder-record checks). The formula code is implemented and tested
against synthetic data, but the flag should not be flipped live until a real eligible
employee actually exists to validate against.

## Cross References
- ../01_employee_knowledge/salary_policy.md
- ../05_workflows/salary_workflow.md
