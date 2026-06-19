# AI Identity Overview

## Purpose
Define the role-centric identity foundation for all AI answers, workflows, permissions, and frontend/backend role management.

## Scope
Level 3 identity architecture with Level 2 admin usage. Never expose internal mechanics to public users.

## Principle
The AI must answer by role and visibility first, then by topic. Domain-only retrieval is not enough because the same topic may have different safe answers for candidate, employee, admin, accountant, or developer.

## Priority Role Order
1. Admin
2. Family
3. Accountant
4. VIP Client
5. Escort Client
6. Employee
7. Supervisor
8. Operation Officer
9. Female Operation Officer
10. Recruitment Officer
11. Vendor
12. Candidate
13. Unknown

## Required Role Operations
- View and verify role.
- Add role record.
- Edit role record.
- Delete or deactivate role record.
- Promote unknown to known role.
- Sync every frontend change to backend.
- Maintain audit trail for every change.

## Cross References
- permission_matrix.md
- response_rules.md
- ../06_developer_system/role_permissions.md

## Revision History
- 2026-06-19: Created as dedicated identity domain.
