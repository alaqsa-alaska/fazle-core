# Database Rules

## Purpose
Define backend data rules required to keep AI, frontend, and operations synchronized.

## Core Principles
- Frontend changes must update backend immediately.
- Backend is source of truth for role, employee, candidate, payment, attendance, escort, and client records.
- Every mutation requires audit trail.
- Never expose table/schema names to external users.

## Required Entities
Role, Contact, Candidate, Employee, Escort Assignment, Attendance, Release Slip, Payment, Cash Ledger, Conversation, Draft, Audit Log, Permission.

## Mutation Rules
- Create: validate permission, normalize phone, prevent duplicate.
- Update: preserve old value and reason.
- Delete: prefer deactivate/archive; hard delete requires explicit management permission.
- Merge: keep audit trail and source references.

## Frontend Sync
Any frontend add/edit/delete for role mapping must update backend and invalidate AI identity cache.
