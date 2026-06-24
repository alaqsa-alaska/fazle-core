---
title: Attendance Workflow
owner: Fazle Core Admin
status: active
last_verified: 2026-06-24
runtime_index: true
---

# Attendance Workflow

## Purpose
Define the admin-only workflow for security guard and escort attendance handling.

## Scope
Level 2 admin system knowledge. Visible to Admin, HR, Operation Officer, Supervisor, Accountant, and Management only.

## Definitions
- Self Attendance: attendance submitted by employee from registered number.
- Batch Attendance: location-wise attendance submitted by Supervisor or Operation Officer.
- Draft: AI-prepared attendance message awaiting admin confirmation.
- Approved Attendance: attendance accepted for payroll use.

## Workflow
1. Attendance message arrives from employee, supervisor, or operation officer.
2. Sender identity is checked by registered phone and role context.
3. AI creates attendance draft.
4. Admin reviews, edits if needed, and approves.
5. Approved attendance is saved for payroll use.
6. Employee may receive confirmation after approval.

## Business Rules
- No attendance is final without admin approval.
- Security guard attendance is based on 12-hour duty day.
- Escort attendance is based on escort duty record, starting from duty slip and ending at release slip.
- Batch attendance from supervisors or operation officers must still be admin-approved.
- Duty selfie may be requested before approval.
- Duplicate attendance must be checked before final save.

## Examples
Guard self attendance:
Attendance Message -> Draft -> Admin Review -> Approve -> Attendance Save -> Confirmation.

Batch attendance:
Supervisor Message -> Location/Employee Parse -> Draft -> Admin Review -> Attendance Save.

## Exceptions
Unknown numbers claiming employee attendance should be routed to identity verification before any attendance draft is approved.

## AI Notes
Do not expose approval workflow, database names, duplicate logic, or internal validation rules to employees.

## Cross References
- ../01_employee_knowledge/attendance_policy.md
- payroll_rules.md
- admin_business_rules.md

## Revision History
- 2026-06-19: Created from admin attendance workflow and management attendance decision.
