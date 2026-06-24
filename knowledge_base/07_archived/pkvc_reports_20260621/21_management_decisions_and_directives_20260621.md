---
title: Management Decisions and Directives Record (Approved)
owner: Fazle Core Admin
status: archived
last_verified: 2026-06-24
runtime_index: false
---

# Management Decisions and Directives Record (Approved)

- Program: Fazle AI Platform PKVC
- Classification: Developer/System Only, Highly Confidential
- Date: 2026-06-21
- Authority: Management

## Approved Conflict Decisions

### CON-01: Escort Daily Payment Formula
- Approved Formula: 12000 / 30 * duty_days
- Effective Daily Rate: 400 BDT/day
- Note: Daily rate is derived from fixed monthly base salary, not a separate fixed daily slab.

### CON-02: Payroll and Payment Formula Unification
- Approved: Same formula in both Payroll and Payment modules.
- Unified Rule: 12000 / 30 * duty_days

### CON-03: Mongla Transport Rate
- Approved Rate Table:
  - Dhaka / Narayanganj: 600
  - Faridpur: 700
  - Mongla: 800
  - Barishal / Coastal: 900
  - Khulna / Jessore: 1000
  - Default: 600

### CON-04: Food Cost Rule
- Base Rule: 150 BDT/day
- Payment Recipient: Master/Sukani
- Exception A: If release slip arrives before 10:00 AM, release-day food is excluded.
- Exception B: If escort boards after 3:00 PM, boarding-day food is excluded.
- Accounting Rule: Paid food amount is recorded as escort advance and deducted in payroll.

## Approved Duplicate Decisions

### DUP-01
- Source of Truth for office address: message_router
- Social reply must consume router source, not hardcoded address.

### DUP-02
- Escort keywords in router and identity brain are intentional dual-purpose design.

### DUP-05
- attendance: Security Guard attendance flow
- attendance_parser: Escort-style structured attendance parsing/approval flow
- Intentional split design.

### DUP-07
- Source of Truth for salary content: salary_structure.txt
- Social salary reply must match salary_structure.txt.

## Approved Hidden Rule Decisions

- HK-01: Silent skip token list includes office; blocked role remains hard skip.
- HK-03: Safe autosend intent list remains approved.
- HK-04: advance_request can autosend only for employee/security_guard/escort roles.
- HK-09: Draft-always roles remain approved.
- HK-13: Loop protection remains 3 replies/120s -> pause 600s.
- HK-19: Transport rate update approved (Mongla 800).
- HK-24: Payroll per-program legacy rate replaced by base-salary-derived daily formula.
- HK-33: Recruitment session TTL remains 24h.
- HK-34: Recruitment scoring remains approved.
- HK-44: Reply cooldown remains 60s (Redis primary, memory fallback).

## Pending Decisions (Need Developer Evidence)

### DUP-03
- Required evidence:
  - modules/phone_normalizer function responsibilities
  - modules/number_identity function responsibilities
  - input/output equivalence or differences

### DUP-04
- Required evidence:
  - whether router performs direct keyword duplication or delegates to recruitment_flow.recruitment_eligibility
  - recommended consolidation choice (A/B/C)

### DUP-06
- Required evidence:
  - schema and usage mapping of fazle_draft_replies vs fazle_payment_drafts
  - approval path by draft type
  - scenarios that touch one table vs both

## Implementation Notes
- This record captures approved governance decisions.
- Production code/database updates are separate implementation work and not executed in this documentation step.
