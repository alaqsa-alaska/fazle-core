---
title: AI Identity Overview
owner: Fazle Core Admin
status: active
last_verified: 2026-08-18
runtime_index: true
---

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
- 2026-06-22: Enriched with production resolution algorithm, evidence sources, and confidence scoring (Wave-1).
- 2026-08-18: Re-synced against `identity_brain/__init__.py` after the
  `HERMES_IDENTITY_ROLE_INTENT_CONTENT_CAPABILITY_AUDIT_2026-08-18.md` audit found this section had
  drifted from a real 2026-08-01 code fix (function name, confidence scale, step structure, and one
  step describing a code path that no longer exists were all corrected below).

---

## Identity Resolution Algorithm (Production)

### Purpose
The production identity engine resolves each contact to a single role using a priority cascade.
The first step that produces a match wins.

### Ordered Resolution Steps

**Removed (Owner decision, 2026-07-11):** A step 0 pre-check (`blocked` role, hard silent-skip) previously existed here. There is no "blocked number" concept in this system — a sender is either a known role below or unknown. See migration 042.

| Step | Resolved Role | Evidence Source | Table |
|---|---|---|---|
| 1 | admin | Admin settings (env), then `fazle_contact_roles` where role='admin' | settings, `fazle_contact_roles` |
| 2 | *(single seed lookup — see below)* family / accountant / vip_client / client_escort_buyer / vendor / supervisor — whichever `fazle_contact_roles` row for this phone has the highest `priority` value | `fazle_contact_roles` |
| 3 | employee | Phone match, `wbom_employees` primary, `fpe_employees` fallback | See below |
| 4 | employee (via operational evidence, gated) | `fpe_cash_transactions` or `wbom_attendance` — only checked when the phone is **not** in the `ESCORT_CLIENT_PHONES` env allowlist (2026-08-01 fix, see note below) | `fpe_cash_transactions`, `wbom_attendance` |
| 5 | escort / repeat_client | `wbom_escort_programs` — `escort_mobile` match → `escort`; `master_mobile`-only match → `repeat_client` | `wbom_escort_programs` |
| 6 | client_escort_buyer | Explicit `ESCORT_CLIENT_PHONES` env allowlist match | env |
| 7 | client_escort_buyer / other | `wbom_contacts` — name marker match → `client_escort_buyer`; otherwise mapped from `relation` column | `wbom_contacts` |
| 8 | candidate | Text-hint keyword match on message content (unverified) | message text only |
| 9 | repeat_client | Escort-content keyword regex on an unknown sender's message (unverified) | message text only |
| 10 | unknown | No match found | Fallback |

**2026-08-01 fix note (why step 4 is gated):** a one-off cash payment or attendance record must
never override an explicit, Owner-configured `ESCORT_CLIENT_PHONES` entry — a verified escort-client
buyer who received a single incidental cash payment was previously misidentified as `role="employee"`
forever after. See the inline comment at `identity_brain/__init__.py:207-220` for the full incident.

**Step 2 is not six separate hardcoded steps.** `identity_brain` runs one query against
`fazle_contact_roles` ordered by that table's own `priority DESC` column
(`_lookup_seed()`, `identity_brain/__init__.py:346-366`) — the relative ordering between
family/accountant/vip_client/client_escort_buyer/vendor/supervisor is whatever the seed data's
`priority` values say today, not a fixed sequence baked into code. Editing `fazle_contact_roles.priority`
changes this ordering without a code change.

**Superseded step removed:** an earlier version of this doc listed a step "repeat_client ← repeat
payment history (`fpe_cash_transactions`)." No such code path exists — the actual cash-payment
branch (step 4 above) resolves to an **employee-designation** role, never literally `repeat_client`.
The two real sources of `repeat_client` are listed at steps 5 and 9 above.

### Employee Evidence (Step 3)

Primary: `wbom_employees` — phone match on 3 variants (01X, 880X, +880X)

Fallback: `fpe_employees` (payroll-engine-only employees, e.g. auto-created through the cash/payroll
flow) joined to `wbom_employees` where linked — `identity_brain/__init__.py:386-401`.

**Source Module:** `modules/identity_brain`
**Source Function:** `detect_identity(phone, text)` — *not* `resolve_identity()` (a previous version
of this doc named the wrong function; there is no `resolve_identity` in this module).
**PKCA Report:** 11_identity_coverage_report.md
**Management Authority:** Production evidence; documented 2026-06-22, re-verified 2026-08-18

---

## Identity Confidence Scoring

`identity_brain.detect_identity()` returns **two** confidence fields in every result
(`_build()`/`_build_contact()`, `identity_brain/__init__.py:575-628`) — use `identity_confidence`
as the primary one; `confidence` is a legacy-compatible mirror:

| Field | Scale | Example values |
|---|---|---|
| `identity_confidence` | integer 0–100 | 100 (admin/settings), 88 (`wbom_employees` primary), 86 (cash/attendance evidence), 85 (escort roster / contact name marker), 95 (env escort-phone allowlist), 80 (contact book relation match), 50 (text-hint candidate), 40 (escort-content regex), 0 (unknown) |
| `confidence` (legacy) | float 0.0–1.0 | `identity_confidence / 100` — kept only for backward compatibility with pre-identity_brain callers |

**Business Rule:** Low-confidence identities (`identity_confidence` < 70, i.e. legacy `confidence` <
0.7) may cause the system to create a draft for admin review rather than auto-send, even for
normally safe intents.

---

## Phone Normalization

When looking up any contact, the system always tries three phone format variants simultaneously:

| Variant | Example |
|---|---|
| Local | `01711234567` (11 digits) |
| International without + | `8801711234567` (13 digits) |
| International with + | `+8801711234567` |

**Canonical storage format:** `8801XXXXXXXXXX` (13 digits, no `+`)

**Source Module:** `modules/phone_normalizer`
**PKCA Report:** 11_identity_coverage_report.md
