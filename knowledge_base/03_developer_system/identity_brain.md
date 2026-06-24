---
title: Identity Brain
owner: Fazle Core Admin
status: active
last_verified: 2026-06-24
runtime_index: true
---

# Identity Brain

## Purpose
Define role detection, priority order, unknown sub-role classification, and visibility routing for Fazle AI identity logic.

## Scope
Level 3 developer/system knowledge. Never expose to external users.

## Priority Role Order
1. Admin
2. Family
3. Accountant
4. VIP Client
5. Client Escort Buyer
6. Employee
7. Supervisor
8. Repeat Client
9. Vendor
10. Candidate
11. Unknown

## Detection Signals
- Admin: configured admin numbers or admin bridge/meta identity.
- Family: seeded family contact roles.
- Accountant: seeded accountant contact number.
- VIP Client: contact book name includes client signal.
- Client Escort Buyer: contact book name includes escort/client buyer signal.
- Employee: employee database, cash payment records, or escort roster mobile match.
- Supervisor: contact name includes supervisor marker such as SV.
- Repeat Client: conversation history indicates known/repeat business context.
- Vendor: contact book vendor markers such as Safe Security or Dalal.
- Candidate: unknown number with job, salary, joining, office, training, application, or recruitment intent.
- Unknown: no reliable match or intent.

## Unknown Sub-Roles
- unknown_candidate: job, salary, joining, office, training intent -> recruitment reply.
- unknown_employee_alt_number: claims employee identity or asks salary/duty/payment -> employee verification draft.
- unknown_new_client: asks for security, guard, escort, or service -> client draft/manual review as needed.
- unknown_existing_client_new_number: appears like old client by context/history/name -> manual review.
- unknown_employee_family: asks about husband/brother/son salary -> verification draft.
- unknown_relative_friend: personal tone or old relationship -> admin manual review.
- unknown_general: unclear -> clarification or admin fallback.
- unknown_sensitive: internal/personal/database/system request -> refuse or skip per disclosure rules.

## Phone Normalization
Normalize local Bangladeshi numbers such as 01XXXXXXXXX to international format 8801XXXXXXXXX before matching.

## Silent Skip / Manual Mode Markers
If contact book includes certain internal or operational markers such as Al-Aqsa, Operation, TCIS, GMS, Dalal, or Office, block auto-reply and hold for manual review unless a specific safe route is configured.

## Business Rules
- Route by role/sub-role, not by a loose intent badge.
- Identity mismatch should be surfaced for admin review.
- Do not serve employee-specific requests from unverified alternate numbers.

## Cross References
- ai_system_prompt.md
- security_rules.md
- ../02_admin_system/admin_business_rules.md

## Revision History
- 2026-06-19: Created from 11-tier identity brain and unknown sub-role rules.
