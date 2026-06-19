# Identity Brain

## Purpose
Implementation-facing identity engine rules.

## Runtime Contract
Every message must resolve to role/sub-role before answer generation, draft creation, or backend write.

## Role Priority
Admin, Family, Accountant, VIP Client, Escort Client, Employee, Supervisor, Operation Officer, Female Operation Officer, Recruitment Officer, Vendor, Candidate, Unknown.

## Backend Requirements
- Normalize phone numbers.
- Match seeded contacts, employee records, payroll records, escort roster, contact book, and conversation memory.
- Allow admin frontend to add/edit/delete/deactivate/merge roles.
- Audit every role mutation.
- Reload role cache after mutation.

## Cross References
- ../03_ai_identity/identity_overview.md
- role_permissions.md
