---
title: Family Identity
owner: Fazle Core Admin
status: active
last_verified: 2026-06-24
runtime_index: true
---

# Family Identity

## Purpose
Identify family contacts and route them according to limited personal access rules.

## Detection Signals
- Seeded family contact role.
- Backend family role record.

## Permissions
Allowed:
- Limited personal data only if explicitly configured.
- Manual review for unclear or sensitive requests.

Not allowed:
- Employee payroll data unless separately authorized.
- Developer/system knowledge.

## Cross References
- permission_matrix.md
- response_rules.md

## Revision History
- 2026-06-19: Created from family role identity rules.
