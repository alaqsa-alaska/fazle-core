# Role Permissions

## Purpose
Backend-enforced permissions for AI and frontend role management.

## Requirements
- Admin can manage operational roles within policy.
- Developer can manage system roles/config with audit.
- Accountant can update payment/accounting states only.
- Supervisor/Operation roles can submit attendance/duty reports, not final financial writes.
- Candidate and employee cannot write internal records directly.

## CRUD Rules
View, add, edit, delete/deactivate, merge, verify, and promote unknown roles must be permission-controlled and audited.
