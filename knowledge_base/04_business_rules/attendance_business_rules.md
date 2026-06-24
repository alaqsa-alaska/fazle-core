---
title: Attendance Business Rules
owner: Fazle Core Admin
status: active
last_verified: 2026-06-24
runtime_index: true
---

# Attendance Business Rules

## Source Of Truth
- Security guard: 12 hours equals one duty day.
- Escort: 24 hours/two shifts equals one duty day.
- Guard attendance may be self-reported or supervisor/operation officer reported.
- Escort attendance starts at duty slip and ends at release slip.

## Validation Rules
- Verify role and phone.
- Check duplicate attendance.
- Require admin approval or authorized workflow before final attendance save.
- Duty selfie may be requested.

## Payroll Impact
- Approved attendance feeds salary, bonus, late, leave, and deduction calculations.
- Unauthorized absence may cause up to 2 days deduction per 1 absent day.
- 3 consecutive late arrivals may cause 1 day salary deduction.

## Cross References
- ../05_workflows/attendance_workflow.md
- ../02_admin_knowledge/admin_attendance_handling.md
