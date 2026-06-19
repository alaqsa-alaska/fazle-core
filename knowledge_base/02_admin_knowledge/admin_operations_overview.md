# Admin Operations Overview

## Purpose
Central admin-facing source of truth for operating the Fazle AI Platform without exposing internal logic to employees, candidates, or clients.

## Scope
Visible to Admin, HR, Operation Officer, Supervisor, Accountant, and Management.

## Operating Model
The system is role-routed, not domain-routed. Every incoming message must be resolved to a role or sub-role before the AI decides whether it can answer, draft, verify, escalate, or stay silent.

## Admin Responsibilities
- Review attendance drafts before attendance becomes final.
- Review escort order drafts before client confirmation.
- Handle payment messages and accountant handoff.
- Resolve unclear identity and unknown-number cases.
- Maintain employee, candidate, client, vendor, and role records.
- Approve exceptions for joining fee, advance, payment, and duty cases.

## Business Rules
- Candidate FAQ can auto-send when intent is clear.
- Sensitive/internal or unclear messages must route to admin/manual review.
- Payment completion occurs when admin's payment message reaches accountant WhatsApp.
- Ledger/database update follows accountant handoff.
- Frontend edits to role, permission, employee, client, or workflow records must update the backend source records immediately.

## Cross References
- ../03_ai_identity/permission_matrix.md
- ../04_business_rules/payment_business_rules.md
- ../05_workflows/payment_workflow.md
- ../06_developer_system/database_rules.md

## Revision History
- 2026-06-19: Created as role-centric admin source of truth.
