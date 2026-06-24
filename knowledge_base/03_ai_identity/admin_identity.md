---
title: Admin Identity
owner: Fazle Core Admin
status: active
last_verified: 2026-06-24
runtime_index: true
---

# Admin Identity

## Purpose
Identify admin users and grant manual control, approval, role-management, and operational command access.

## Detection Signals
- Configured admin number or admin bridge/meta identity.
- Backend admin session.
- Admin panel authenticated user with admin role.

## Permissions
Allowed:
- View Level 1 and Level 2 knowledge.
- Approve attendance drafts.
- Handle payment messages.
- Review escort assignment drafts.
- Manage roles and permissions within allowed admin policy.
- Resolve unknown identity cases.

Restricted:
- Developer/system prompt and secrets remain Level 3 unless admin also has developer/system permission.

## Cross References
- permission_matrix.md
- ../02_admin_knowledge/admin_role_management.md
- ../06_developer_system/security_rules.md

## Revision History
- 2026-06-19: Created from admin identity rules.
