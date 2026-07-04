---
title: Workflow Engine
owner: Fazle Core Admin
status: active
last_verified: 2026-06-24
runtime_index: true
---

# Workflow Engine

## Purpose
Define internal event triggers, maintenance rules, and workflow dependencies for Fazle AI operations.

## Scope
Level 3 developer/system knowledge. Never expose to external users.

## Core Engines
- Identity Brain
- Role Detection
- Employee Matching
- Duplicate Detection
- Draft Engine
- Approval Queue
- Event Pipeline
- Attendance Engine
- Escort Engine
- Payroll Engine
- Cash Ledger Integration

## Event Triggers
Internal workflow may use event triggers such as:
- Attendance Trigger
- Escort Trigger
- Payroll Trigger
- Cash Trigger

## Draft Lifecycle
- Draft is created when workflow requires human review.
- Draft quality is checked before admin review or auto-send eligibility.
- Pending drafts older than 48 hours may be expired by background maintenance.

## Workflow Dependencies
Attendance approved -> payroll eligible -> bonus/deduction calculation -> salary calculation -> final payroll.

Escort client confirmation -> escort roster update -> duty tracking -> release slip -> payment calculation -> accountant handoff.

Admin payment message reaches accountant -> payment complete state -> ledger/database update.

## Business Rules
- Admin approval or admin handling is mandatory for payment-risk workflows.
- Candidate FAQ can auto-send if clear and non-sensitive.
- Ambiguous, sensitive, or identity-risk workflows must route to manual review.

## Cross References
- ai_system_prompt.md
- ../02_admin_system/payment_workflow.md
- ../02_admin_system/attendance_workflow.md
- security_rules.md

## Revision History
- 2026-06-19: Created from workflow engine and event trigger rules.
