# Admin Role Management

## Purpose
Define how roles can be viewed, verified, added, edited, deleted, and synchronized between frontend and backend.

## Scope
Level 2 admin knowledge with Level 3 implementation references.

## Role Records
The admin panel must support role management for Candidate, Employee, Escort, Security Guard, Operation Officer, Female Operation Officer, Supervisor, Accountant, Admin, Escort Client, Vendor, Family, VIP, and Unknown.

## Required Frontend Functions
- View role list and role priority.
- Search by name, phone, role, sub-role, source, and status.
- Verify identity match.
- Add new role record.
- Edit role, phone, name, permission, status, and notes.
- Delete or deactivate role record with audit log.
- Merge duplicate contacts.
- Promote Unknown to a confirmed role.

## Backend Sync Rules
- Every frontend create/edit/delete must write to the backend source table/service immediately.
- Every role mutation must create an audit record: actor, timestamp, old value, new value, reason.
- Backend must reject frontend role changes that violate permission rules.
- AI must reload role mapping after successful role update.

## Cross References
- ../03_ai_identity/permission_matrix.md
- ../06_developer_system/database_rules.md
- ../06_developer_system/role_permissions.md

## Revision History
- 2026-06-19: Created from role CRUD and frontend/backend sync requirement.
