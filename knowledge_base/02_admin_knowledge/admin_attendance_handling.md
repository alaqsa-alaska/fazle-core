---
title: Admin Attendance Handling
owner: Fazle Core Admin
status: active
last_verified: 2026-06-24
runtime_index: true
---

# Admin Attendance Handling

## Purpose
Define admin handling for self-attendance, supervisor attendance, and escort attendance.

## Scope
Level 2 admin knowledge.

## Rules
- Security guard: 12 hours equals one duty day.
- Escort: 24 hours, day plus night, equals one duty day.
- Guard attendance may come from self-message or supervisor/operation officer batch message.
- Escort attendance starts from duty slip and ends at release slip.
- Attendance should not become final without admin approval or authorized admin workflow.

## Required Checks
- Registered phone or verified role.
- Duty location or assignment context.
- Duplicate attendance check.
- Duty selfie if requested.
- Release slip for escort final attendance.

## Cross References
- ../04_business_rules/attendance_business_rules.md
- ../05_workflows/attendance_workflow.md

## Revision History
- 2026-06-19: Created as admin attendance source of truth.
