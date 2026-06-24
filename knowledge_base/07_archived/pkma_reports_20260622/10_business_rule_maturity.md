---
title: PKMA Report 10 — Business Rule Maturity
owner: Fazle Core Admin
status: archived
last_verified: 2026-06-24
runtime_index: false
---

# PKMA Report 10 — Business Rule Maturity

**Program:** PKMA v1.0
**Date:** 2026-06-22
**Mode:** READ-ONLY

---

## Purpose

Deep assessment of business rule knowledge maturity: are the rules that govern money, people, and decisions documented, verified, and approved?

---

## Business Rule Inventory

### Category A — Financial Rules (CON series)

| Rule ID | Rule | Value | KB Location | Production Source | Mgmt Decision | Maturity |
|---|---|---|---|---|---|---|
| CON-01 | Base payroll rate | ৳12,000/month base | salary_workflow.md, escort_business_rules.md | modules/payroll, modules/escort_lifecycle | YES | L3 |
| CON-02 | Per-day calculation method | 12000/30 × duty_days | escort_business_rules.md | modules/escort_lifecycle | YES | L3 |
| CON-03 | Mongla transport rate | ৳800 BDT | escort_business_rules.md | `_TRANSPORT_RATES` | YES | L3 |
| CON-04 | Food rate + time exceptions | ৳150/day; before 10AM no food on release day; after 3PM no food on boarding day | escort_business_rules.md | `_calc_duty_days()` | YES | L3 |
| CON-X | Dhaka/Narayanganj transport | ৳600 BDT | escort_business_rules.md | `_TRANSPORT_RATES` | Implied | L3 |
| CON-X | Faridpur transport | ৳700 BDT | escort_business_rules.md | `_TRANSPORT_RATES` | Implied | L3 |
| CON-X | Barishal/coastal transport | ৳900 BDT | escort_business_rules.md | `_TRANSPORT_RATES` | Implied | L3 |
| CON-X | Khulna/Jessore transport | ৳1000 BDT | escort_business_rules.md | `_TRANSPORT_RATES` | Implied | L3 |
| CON-X | Default transport rate | ৳600 BDT | escort_business_rules.md | `_TRANSPORT_RATES` | Implied | L3 |

**Financial Rule Maturity: 9/9 documented; 9/9 production verified; 4/9 formally approved**

---

### Category B — Eligibility Rules (BR series)

| Rule ID | Rule | Value | KB Location | Production Source | Mgmt Decision | Maturity |
|---|---|---|---|---|---|---|
| BR-25 | Candidate age range | 18–55 years (RESOLVED from 18–45) | recruitment_policy.md, recruitment_business_rules.md | `modules/recruitment_flow._parse_age()` | YES (RESOLVED) | L3 |
| BR-X | Valid positions (9) | Vessel Escort, Security Guard, Security Supervisor, Asst Security Supervisor, Operation Officer, Female Marketing Officer, Female Operation Officer, Recruitment Officer, Admin | recruitment_business_rules.md | `VALID_POSITIONS` | YES (HK-36) | L3 |
| BR-X | Min education | Class 8 preferred; Madrasa acceptable | recruitment_policy.md | — | Implied | L2 |
| BR-X | Physical fitness | Required for all positions | recruitment_policy.md | — | Implied | L2 |
| BR-X | Prior experience | Not required for escort/guard | recruitment_policy.md | — | Implied | L2 |

**Eligibility Rule Maturity: 5/5 documented; 2/5 production verified; 2/5 formally approved**

---

### Category C — Hidden Rules (HK series)

| Rule ID | Rule | Value | KB Location | Production Source | Mgmt Decision | Maturity |
|---|---|---|---|---|---|---|
| HK-01 | Silent-skip display-name tokens | 11 tokens | ai_response_rules.md | `_should_silent_skip()` | YES | L3 |
| HK-02 | Silent-skip blocked role | role='blocked' → no reply | ai_response_rules.md, identity_overview.md | `_should_silent_skip()` | Implied | L2 |
| HK-03 | Safe auto-send intents | 9 intents | ai_response_rules.md, permission_matrix.md | `_SAFE_AUTOSEND_INTENTS` | YES | L3 |
| HK-04 | Intent required for auto-send | Explicit intent only | ai_response_rules.md | `app/message_router` | YES | L3 |
| HK-05 | Recruitment gate by role | Non-recruiters can't recruit | recruitment_workflow.md | `app/message_router` | Implied | L2 |
| HK-09 | Draft-always roles | accountant, client_escort_buyer, vip_client, repeat_client | ai_response_rules.md, role_permissions.md | `_is_draft_always()` | YES | L3 |
| HK-10 | Complaint phrases (11) → draft | Force draft on any of 11 phrases | ai_response_rules.md | `_COMPLAINT_PHRASES` | Implied | L2 |
| HK-11 | Advance request phrases (5) → draft | Force draft on short-form advance requests | ai_response_rules.md, payment_business_rules.md | `_ADVANCE_REQUEST_PHRASES` | Implied | L2 |
| HK-12 | Outbound poison filter | 16 strings filtered from outbound | security_rules.md | `_OUTBOUND_POISON` | NOT FORMALLY | L2 |
| HK-13 | Loop detection | 3/120s → 600s pause | security_rules.md | `_LOOP_*` constants | YES | L3 |
| HK-14 | Keyword flood protection | 3 keywords in 5min → 15min block | security_rules.md | `_KW_FLOOD_*` | NOT FORMALLY | L2 |
| HK-15 | Prompt injection | 18 patterns → outbound_safety_incidents | security_rules.md | `_PROMPT_INJECTION_PATTERNS` | NOT FORMALLY | L2 |
| HK-16 | Group/broadcast skip | Skip group/broadcast messages | security_rules.md | bridge_poller | Implied | L2 |
| HK-19 | Mongla transport = ৳800 | (same as CON-03) | escort_business_rules.md | `_TRANSPORT_RATES` | YES | L3 |
| HK-22 | Release date validation | No future date; no >1yr past | release_slip_workflow.md | `_validate_release_date()` | Implied | L2 |
| HK-23 | Suspicious duty days | duty_days > 90 = SUSPICIOUS flag | escort_business_rules.md | `build_release_draft()` | Implied | L2 |
| HK-24 | Stale program alert | 30-day threshold | escort_workflow.md | ESCORT_STALE_DAYS | YES | L3 |
| HK-27 | Payroll idempotency | UNIQUE constraint on payroll | salary_workflow.md | `compute_run()` | Implied | L2 |
| HK-31 | RAG chunk safety filter | 30+ patterns | security_rules.md | bridge_poller | Implied | L2 |
| HK-33 | Recruitment session TTL | 24h | recruitment_business_rules.md | `SESSION_TTL` | YES | L3 |
| HK-34 | Recruitment scoring | 100-point scale | recruitment_business_rules.md | `_compute_score()` | YES | L3 |
| HK-36 | Valid positions | 9 positions | recruitment_business_rules.md | `VALID_POSITIONS` | YES | L3 |
| HK-37 | Admin command dedup | SHA1(text+phone) 30s TTL 256 entries | security_rules.md | bridge_poller | Implied | L2 |
| HK-38/39 | Multi-ID APPROVE + Bangla digits | Multi-ID approve; Bangla digit normalization | attendance_workflow.md | admin_commands | Implied | L2 |
| HK-40 | RBAC enforcement | COMMAND_ROLE per command | role_permissions.md | modules/rbac | Implied | L2 |
| HK-41 | Bootstrap admin | ADMIN_NUMBERS → superadmin on first msg | admin_role_management.md | `ensure_bootstrap_admins()` | YES | L3 |
| HK-42 | API key SHA-256 | Keys stored as SHA-256 hash | security_rules.md | modules/rbac | Implied | L2 |
| HK-43 | Advance keywords | 18 advance trigger keywords | payment_business_rules.md | `ADVANCE_KEYWORDS` | Implied | L2 |
| HK-44 | Reply cooldown | 60s Redis + fallback | security_rules.md | `REPLY_COOLDOWN` | YES | L3 |
| HK-47 | office_location fast path | KB-only, no LLM | ai_response_rules.md | message_router | Implied | L2 |

**HK Rule Maturity: 30/47 documented (Wave-1 + prior); ~52% coverage; 12/30 formally approved**

---

### Category D — Operational Rules (Validated in Production)

| Rule | Value | KB Location | Production Source | Maturity |
|---|---|---|---|---|
| Payment formula for advance | 12000/30 × days | payment_business_rules.md | modules/payment_workflow | L3 |
| Payment draft TTL | 24h | payment_business_rules.md | combined_draft_cleanup | L2 |
| Duty day calculation | release_date - boarding_date | release_slip_workflow.md | `_calc_duty_days()` | L2 |
| ESCORTCONFIRM syntax | `ESCORTCONFIRM <order_id> [CONFIRMED/REJECTED]` | escort_workflow.md | modules/admin_commands | L2 |
| Payroll state machine | 6 states + ALLOWED_TRANSITIONS | salary_workflow.md | modules/payroll | L3 |
| Escort state machine | 6 states: draft → completed / cancelled | escort_workflow.md | modules/escort_lifecycle | L3 |
| Attendance on-conflict | ON CONFLICT UPDATE (upsert) | attendance_workflow.md | modules/attendance | L2 |
| Phone normalization canonical | 8801XXXXXXXXXX | identity_overview.md | modules/phone_normalizer | L2 |

---

## Business Rule Maturity Score

| Category | Total Rules | Documented | Production Verified | Formally Approved | Maturity |
|---|---|---|---|---|---|
| Financial (CON) | 9 | 9 | 9 | 4 | L3 average |
| Eligibility (BR) | 5 | 5 | 2 | 2 | L2-3 average |
| Hidden Rules (HK) | 47 | ~30 | ~30 | 12 | L2-3 average |
| Operational | 8 | 8 | 8 | 3 | L2-3 average |
| **Total** | ~69 | ~52 | ~49 | ~21 | **L2.7 average** |

---

## Business Rule Gaps

| Gap | Impact | Recommendation |
|---|---|---|
| HK-12, HK-14, HK-15 not formally approved | MEDIUM — compliance risk | Request formal ratification |
| 17 HK rules still undocumented (post-Wave-1) | MEDIUM — ~36% of HK rules missing | Wave-2 target |
| DUP-06 (recruitment FAQ wording) pending | HIGH — inconsistency in customer-facing content | Resolve in next management session |
| DUP-03 (salary format) pending | HIGH — financial display inconsistency | Resolve in next management session |
| Payment correction module (DORMANT) | LOW — 0 callers; no operational impact | Document as DORMANT in Wave-2 |

---

## Business Rule Verdict

**Domain Maturity: Level 3 (Management Approved)**

Rationale: All critical financial rules (CON-01–04) are management-approved and production-verified. BR-25 was formally resolved. Most HK rules documented in Wave-1 are either formally approved or implied by system behavior. The 3 pending DUP decisions affect quality but not operational correctness.

**Path to Level 4:** Resolve DUP-03/06 → run PKVC post-Wave-1 → no new conflicts → Certified.

---

*PKMA v1.0 | READ-ONLY | 2026-06-22*
