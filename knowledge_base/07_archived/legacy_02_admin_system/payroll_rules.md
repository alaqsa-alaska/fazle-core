---
title: Payroll Rules
owner: Fazle Core Admin
status: active
last_verified: 2026-06-24
runtime_index: true
---

# Payroll Rules

## Purpose
Define admin-only payroll rules connecting attendance, leave, late arrival, advance, bonus, transport, and final salary calculation.

## Scope
Level 2 admin system knowledge. Visible to Admin, HR, Accountant, and Management only.

## Payroll Chain
Attendance -> Leave -> Late -> Advance -> Bonus -> Salary -> Final Payment.

## Attendance Inputs
- Security guard: 12-hour shift equals one duty day.
- Escort: 24-hour day/night duty equals one duty day.
- Escort attendance starts from duty slip and ends with release slip.
- Attendance must be approved before payroll eligibility.

## Salary Inputs
Security guard probation package may be up to ৳17,000/month.
Security guard permanent package may be up to ৳24,700/month.
Escort training pay is ৳10,000-৳15,000/month for 45 days.
Escort after training is duty-based, generally ৳12,000-৳18,000 range.

## Deductions
- Unauthorized absence: 1 day may cause up to 2 days deduction.
- 3 consecutive late arrivals may cause 1 day salary deduction.
- Advance payments are deducted from total earned amount.
- Less than 30 working days may shift calculation to daily basis.

## Bonus Rules
- Attendance bonus: ৳2,000/month for probation and permanent security guard salary structures.
- Unauthorized absence may reduce or cancel attendance bonus.

## Transport Rules
- Probation security guard package includes ৳700 transport component.
- Permanent transport support may be region-based: Dhaka ৳600/month, outside Dhaka ৳900/month.
- Escort conveyance is destination/release-point based and finalized after release slip review.

## Business Rules
- Client billing/service charge is not employee salary.
- Advance range ৳500-৳1,000 is a normal guideline, not a hard cap.
- Payroll should not expose internal formulas to employees.

## Examples
Escort final payment concept:
Duty days x applicable rate + food/conveyance - advance = net payable.

## Exceptions
Management may approve exceptions for hardship, emergency advance, or special duty conditions.

## AI Notes
Use only employee-safe summaries when responding to employees. Keep formulas and ledger dependencies internal.

## Cross References
- attendance_workflow.md
- payment_workflow.md
- ../01_employee_knowledge/salary_policy.md

## Revision History
- 2026-06-19: Created from payroll rules and resolved conflict decisions.
