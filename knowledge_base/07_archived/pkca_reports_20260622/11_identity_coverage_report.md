---
title: PKCA Report 11: Identity Coverage Report
owner: Fazle Core Admin
status: archived
last_verified: 2026-06-24
runtime_index: false
---

# PKCA Report 11: Identity Coverage Report

**Date:** 2026-06-22
**Mode:** Read-Only Analysis

---

## Identity System Overview

The Fazle AI platform uses a multi-source identity resolution system to classify every inbound contact before routing messages. Identity determines tone, language, access gates, draft-always rules, silent-skip rules, and LLM system prompt injection.

---

## 11 Contact Roles

| Role | Code Name | Source of Truth | KB Coverage |
|---|---|---|---|
| 1 | admin | fazle_admins + fazle_contact_roles | 40% — admin_identity.md exists |
| 2 | family | fazle_contact_roles (role='family') | 30% — family_identity.md exists |
| 3 | accountant | fazle_contact_roles (role='accountant') | 30% — accountant_identity.md exists |
| 4 | vip_client | fazle_contact_roles | 30% — vip_identity.md exists |
| 5 | client_escort_buyer | fazle_contact_roles | 20% — partially in identity_overview.md |
| 6 | repeat_client | wbom_cash_transactions history | 20% — partially in identity_overview.md |
| 7 | vendor | fazle_contact_roles | 10% — mentioned in identity_overview.md |
| 8 | employee | wbom_employees + 4 secondary sources | 40% — employee_identity.md exists |
| 9 | supervisor | fazle_contact_roles (role='supervisor') | 20% — partially in identity_overview.md |
| 10 | candidate | fazle_recruitment_sessions | 30% — candidate_identity.md exists |
| 11 | unknown | fallback — no evidence found | 10% — briefly in identity_overview.md |

**Special roles not in routing priority:**
- `blocked` — hard silent-skip (role='blocked' in fazle_contact_roles) — 0% covered
- `escort` — subset of employee, not a distinct role in ROLE_PRIORITY — 30% in escort_identity.md

---

## 8 Identity Evidence Sources

| Source | Used For | KB Coverage |
|---|---|---|
| `fazle_contact_roles` | admin, family, accountant, vip_client, client_escort_buyer, vendor, supervisor, blocked | 0% — table never named in KB |
| `fazle_admins` | admin role confirmation + RBAC level | 0% — never named in KB |
| `wbom_employees` | employee role (phone match across 3 variants) | 0% — table never named in KB |
| `wbom_cash_transactions` | repeat_client + employee secondary evidence | 0% |
| `wbom_attendance` | employee secondary evidence | 0% |
| `escort_roster_entries` | employee secondary evidence | 0% |
| `wbom_contacts` | Best-name lookup; contact DB secondary evidence | 0% |
| `fazle_recruitment_sessions` | candidate role (active recruitment session exists) | 0% |

---

## Identity Resolution Algorithm

**Step-by-step resolution order (from `identity_brain.py`):**

```
Step 1: Is this an admin? → check fazle_admins + fazle_contact_roles(role=admin)
Step 2: Is this family? → check fazle_contact_roles(role=family)
Step 3: Is this accountant? → check fazle_contact_roles(role=accountant)
Step 4: Is this a VIP? → check fazle_contact_roles(role=vip_client)
Step 5: Is this client_escort_buyer? → check fazle_contact_roles(role=client_escort_buyer)
Step 6: Is this repeat_client? → check wbom_cash_transactions history
Step 7: Is this vendor? → check fazle_contact_roles(role=vendor)
Step 8: Is this employee?
  - Primary: wbom_employees WHERE mobile = 01XXXXXXXXX OR 880XXXXXXXXX OR +880XXXXXXXXX
  - Secondary A: wbom_cash_transactions (any transaction with this phone)
  - Secondary B: wbom_attendance (any attendance with this phone)
  - Secondary C: escort_roster_entries (any roster entry)
  - Secondary D: wbom_contacts (contact DB match)
Step 9: Is this supervisor? → check fazle_contact_roles(role=supervisor)
Step 10: Is this a candidate? → check fazle_recruitment_sessions (active session)
Step 11: fallback → unknown
```

**KB Coverage:** `03_ai_identity/identity_overview.md` lists the 11 roles with descriptions but does NOT document the resolution algorithm, step order, or secondary evidence sources.

---

## Confidence Scoring

Identity resolution returns a confidence float (0.0–1.0):

| Evidence Type | Confidence |
|---|---|
| fazle_contact_roles exact match | 1.0 |
| wbom_employees primary phone match | 0.95 |
| Secondary evidence (cash/attendance/roster) | 0.7 |
| Contact DB match only | 0.5 |
| No evidence / fallback | 0.0 (unknown) |

**KB Coverage:** `03_ai_identity/permission_matrix.md` does not mention confidence scores. No KB article documents how confidence affects routing decisions.

---

## Role-Based Behavioral Gates

| Role | Draft-Always | Silent Skip | Auto-Reply | Recruiting Blocked |
|---|---|---|---|---|
| admin | No | No | N/A (admin sender) | No |
| family | No | No | Yes | No |
| accountant | Yes | Depends on phone | No | Yes |
| vip_client | Yes | No | No (draft only) | Yes |
| client_escort_buyer | Yes | No | No (draft only) | Yes |
| repeat_client | Yes | No | No (draft only) | Yes |
| vendor | No | No | Yes | Yes |
| employee | No | No | Yes | No |
| supervisor | No | No | Yes | No |
| candidate | No | No | Yes | No (this is recruitment) |
| unknown | No | No | Yes (greeting intent) | No |
| blocked | N/A | Yes (hard) | No | N/A |

**KB Coverage:** `03_ai_identity/permission_matrix.md` exists but does NOT map roles to these specific gate behaviors.

---

## Bangla System Prompt Injection

The `role_classifier` module injects a Bangla context prompt per contact role into every LLM call:

| Role | Injected Bangla Context |
|---|---|
| vip_client | এই ব্যক্তি একজন VIP ক্লায়েন্ট। সর্বোচ্চ সম্মান ও সেবা নিশ্চিত করুন। |
| employee | এই ব্যক্তি একজন কর্মচারী। তাঁর বেতন, ড্যুটি এবং পেমেন্ট সংক্রান্ত প্রশ্নে সহায়তা করুন। |
| candidate | এই ব্যক্তি একজন চাকরিপ্রার্থী। নিয়োগ প্রক্রিয়া সম্পর্কে তথ্য দিন। |
| unknown | এই ব্যক্তি অপরিচিত। বিনম্রভাবে প্রাথমিক সহায়তা প্রদান করুন। |

**KB Coverage:** 0% — No KB article documents LLM system prompt injection per role.

---

## Identity Coverage Summary

| Component | Coverage |
|---|---|
| 11 roles documented in KB | 30% average (identities exist but incomplete) |
| Resolution algorithm (step order) | 0% |
| 8 evidence sources documented | 0% |
| Confidence scoring | 0% |
| Role-gate behavioral matrix | 0% |
| Bangla system prompt injection | 0% |
| blocked role behavior | 0% |

**Average Identity Coverage: 8%**

## Enrichment Targets

1. **`03_ai_identity/identity_overview.md`** — Add resolution algorithm, step order, secondary evidence sources, confidence scores
2. **`03_ai_identity/permission_matrix.md`** — Add draft-always, silent-skip, auto-reply behavioral gate table
3. **`06_developer_system/identity_brain.md`** — Add all 8 evidence source tables, Bangla prompt injection, confidence scoring algorithm
