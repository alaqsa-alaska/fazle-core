---
title: PKMA Report 16 — Identity Brain Maturity
owner: Fazle Core Admin
status: archived
last_verified: 2026-06-24
runtime_index: false
---

# PKMA Report 16 — Identity Brain Maturity

**Program:** PKMA v1.0
**Date:** 2026-06-22
**Mode:** READ-ONLY

---

## Purpose

Assess the maturity of the identity resolution system. The identity brain is mature when the resolution algorithm, evidence sources, confidence scoring, all roles, all gate behaviors, and all edge cases are documented, production-verified, and management-approved.

---

## Identity System Architecture

| Component | Detail |
|---|---|
| Source | `modules/identity_brain.resolve_identity()` |
| KB Articles | `03_ai_identity/identity_overview.md`, `03_ai_identity/permission_matrix.md` (both enriched Wave-1) |
| Role Count | 11 official + 1 implicit (blocked) |
| Evidence Sources | 8 sources across 5 database tables |
| Algorithm | 11-step ordered resolution with confidence scoring |
| Phone Normalization | 3 input variants → canonical `8801XXXXXXXXXX` (13 digits) |

---

## Identity Component Assessments

---

## ID-01 — 11-Step Resolution Algorithm

**KB Article:** `03_ai_identity/identity_overview.md`
**Maturity: Level 2 (Production Verified)**

| Dimension | Status |
|---|---|
| Steps | 11 steps documented: step 0 (blocked pre-check), steps 1–11 (ordered evidence sources) |
| Source | `modules/identity_brain.resolve_identity()` |
| Step Order | Explicitly documented with priority rationale |
| Exit Condition | Returns on first confident match (confidence ≥ 0.5) |
| Fallback | role=unknown, confidence=0.0 |
| Documented in KB | Yes (Wave-1 — full table) |
| Production Verified | Yes |
| Management Decision | None formally approving the step order |

**Gap to Level 3:** No management decision ratifying the 11-step algorithm as the authoritative identity resolution method. The algorithm is production-verified but not formally approved.
**Risk:** High — identity resolution determines access control; informal authority is a governance gap.

---

## ID-02 — 8 Evidence Sources

**KB Article:** `03_ai_identity/identity_overview.md`
**Maturity: Level 2 (Production Verified)**

| Evidence Source | Table | Confidence | Documented |
|---|---|---|---|
| blocked_numbers | fazle_blocked_numbers | Hard stop (1.0) | Yes |
| direct admin entry | fazle_admins | 1.0 | Yes |
| employee phone match | wbom_employees | 0.95 | Yes |
| cash/attendance record | wbom_cash_transactions / wbom_attendance | 0.7 | Yes |
| escort roster | wbom_escort_programs | 0.7 | Yes |
| contact directory | wbom_contacts | 0.5 | Yes |
| recruitment session active | fazle_recruitment_sessions | 0.7 | Yes |
| external contact registry | fazle_contacts | 0.5 | Yes |

All 8 sources documented with table name and confidence score in Wave-1.
**Production Verified:** Yes.
**Management Decision:** None for the confidence score thresholds.

**Gap to Level 3:** Confidence thresholds (0.95, 0.7, 0.5) are production values but not formally ratified by management.

---

## ID-03 — Phone Normalization

**KB Article:** `03_ai_identity/identity_overview.md`
**Maturity: Level 2 (Production Verified)**

| Input Format | Example | Canonical Output |
|---|---|---|
| +8801XXXXXXXXXX | +8801712345678 | 8801712345678 |
| 01XXXXXXXXXX | 01712345678 | 8801712345678 |
| 8801XXXXXXXXXX | 8801712345678 | 8801712345678 (unchanged) |

| Dimension | Status |
|---|---|
| Source | `modules/phone_normalizer.normalize_bd_phone()` |
| Documented in KB | Yes (Wave-1 — 3 variants table) |
| Production Verified | Yes |
| Management Decision | None explicit |

**Risk:** Low — simple deterministic rule; well-documented.

---

## ID-04 — Role Gate Behaviors (12 roles)

**KB Article:** `03_ai_identity/permission_matrix.md`, `06_developer_system/role_permissions.md`
**Maturity: Level 3 (Management Approved)**

| Role | Draft-Always | Silent-Skip | Recruiting-Blocked | Auto-Reply Gate |
|---|---|---|---|---|
| superadmin | No | No | Yes | Yes |
| admin | No | No | Yes | Yes |
| accountant | Yes | No | Yes | Yes |
| operator | No | No | Yes | Yes |
| viewer | No | No | Yes | Yes |
| client_escort_buyer | Yes | No | No | Yes |
| vip_client | Yes | No | No | Yes |
| repeat_client | Yes | No | No | Yes |
| employee | No | No | No | Yes |
| supervisor | No | No | Yes | Yes |
| candidate | No | No | No | Yes |
| unknown | No | No | No | Yes |

All 12 roles documented with all 4 gate behaviors (Wave-1).
**Production Verified:** Yes.
**Management Decision:** HK-09 (draft-always gate), HK-01 (silent-skip token list), HK-05 (recruiting-blocked), HK-03 (auto-reply gate).

**Maturity: Level 3** — strongest governance component in the identity system.

---

## ID-05 — Confidence Scoring System

**KB Article:** `03_ai_identity/identity_overview.md`
**Maturity: Level 2 (Production Verified)**

| Dimension | Status |
|---|---|
| Threshold to Act | ≥ 0.5 (documented) |
| Threshold for Employee | 0.95 for direct match; 0.7 for secondary evidence |
| Below Threshold | role=unknown returned |
| Conflict Resolution | Multiple high-confidence matches — highest confidence wins |
| Documented in KB | Yes (Wave-1 — confidence table) |
| Production Verified | Yes |
| Management Decision | None for threshold values |

**Risk:** Medium — confidence thresholds determine access decisions; informal values are a governance risk.

---

## ID-06 — Secondary Employee Evidence

**KB Article:** `03_ai_identity/identity_overview.md`
**Maturity: Level 2 (Production Verified)**

| Evidence Type | Source | Confidence |
|---|---|---|
| Cash transaction record | wbom_cash_transactions | 0.7 |
| Attendance record | wbom_attendance | 0.7 |
| Escort roster entry | wbom_escort_programs | 0.7 |
| Contact directory | wbom_contacts | 0.5 |

Four secondary sources documented in Wave-1.
**Production Verified:** Yes.
**Management Decision:** None explicitly for secondary evidence hierarchy.

---

## ID-07 — Blocked Role Pre-Check

**KB Article:** `03_ai_identity/identity_overview.md`, `04_business_rules/ai_response_rules.md`
**Maturity: Level 3 (Management Approved)**

| Dimension | Status |
|---|---|
| Step | 0 (before all other resolution steps) |
| Source | `app/message_router._should_silent_skip()` with role=blocked check |
| Behavior | Hard silent-skip — no draft, no reply, no log entry |
| Management Decision | HK-02 (blocked role behavior approved) |
| Documented in KB | Yes (Wave-1) |
| Production Verified | Yes |

---

## ID-08 — Bangla Prompt Injection Prevention (Role-Level)

**KB Article:** None (P2 Wave-2 target)
**Maturity: Level 0 (Unknown)**

| Dimension | Status |
|---|---|
| Source | `modules/role_classifier` |
| Behavior | Injects role-specific Bangla system prompt to prevent prompt injection |
| KB Documentation | 0% |
| Production Verified | No |

**Risk:** High — role-level injection is a secondary safety mechanism after the global injection filter.

---

## Identity Maturity Summary

| Component | Level | Risk |
|---|---|---|
| ID-01: 11-Step Algorithm | 2 | High |
| ID-02: 8 Evidence Sources | 2 | Medium |
| ID-03: Phone Normalization | 2 | Low |
| ID-04: Role Gate Behaviors | 3 | Low |
| ID-05: Confidence Scoring | 2 | Medium |
| ID-06: Secondary Employee Evidence | 2 | Medium |
| ID-07: Blocked Role Pre-Check | 3 | Low |
| ID-08: Bangla Prompt Injection | 0 | High |

**Identity Domain Average: 2.0 / 5.0**
**Level 3 count: 2 / 8**
**Level 0 count: 1 (Bangla prompt injection)**

---

## Identity Domain Verdict

**Domain Maturity: Level 2 (Production Verified)**

The role gate behaviors and blocked role pre-check are at Level 3 (well-governed). The core identity algorithm itself (11-step resolution, confidence scoring) is only Level 2 — documented and production-verified, but without formal management ratification of the algorithm or threshold values. This is a governance gap for an access-control system.

**Fastest path to Level 3:**
1. Management formally approves the 11-step resolution algorithm as authoritative — 1 decision
2. Management approves confidence thresholds (1.0 / 0.95 / 0.7 / 0.5 / 0.0) — 1 decision
3. Document Bangla prompt injection behavior in identity_brain.md — KB update (Wave-2)

---

*PKMA v1.0 | READ-ONLY | 2026-06-22*
