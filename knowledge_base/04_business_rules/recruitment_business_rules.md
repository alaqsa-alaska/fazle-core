---
title: Recruitment Business Rules
owner: Fazle Core Admin
status: active
last_verified: 2026-06-24
runtime_index: true
---

# Recruitment Business Rules

## Candidate Eligibility

**Management Decision (BR-25 RESOLVED 2026-06-22):**

- **Age: 18–55 years** (Authoritative — production code `recruitment_flow._parse_age()`)
- Physically fit.
- Minimum class 8 preferred; certificate not mandatory if candidate can read/write Bangla.
- No prior experience required for security guard or escort.

## Auto-Reply Rule
Clear candidate FAQ may auto-send. Sensitive/internal/unclear requests route to admin.

## Required Candidate Data
- Name.
- Age.
- Education.
- Current address.
- Desired role.
- Phone number.

## Same-Day Joining Guidance
Do not guarantee same-day duty. Say possible if candidate comes prepared before 2 PM and work is available.

## Cross References
- ../05_workflows/recruitment_workflow.md
- ../03_ai_identity/candidate_identity.md

---

## Valid Recruitment Positions

### Purpose
Only these 9 positions are valid for the recruitment funnel. Candidates selecting a position outside this list are prompted to choose again.

| # | Position Name |
|---|---|
| 1 | Escort |
| 2 | Survey Scout |
| 3 | Security Guard |
| 4 | Security Supervisor |
| 5 | Assistant Supervisor |
| 6 | Operation Officer |
| 7 | Security In-Charge |
| 8 | Marketing Officer |
| 9 | Ghat Supervisor |

**Business Rule:** The system validates the candidate's stated desired role against this list. Partial matches and common aliases are mapped by the parser.

**Source Module:** `modules/recruitment_flow`
**Source Function:** `VALID_POSITIONS`
**PKCA Report:** 10_hidden_rule_coverage_report.md (HK-36)
**Management Authority:** Production evidence; documented 2026-06-22

---

## Recruitment Scoring Algorithm

### Purpose
After a candidate completes the data collection session, a score is computed to prioritize candidates for follow-up.

### Scoring Rules

| Criterion | Score |
|---|---|
| Experience ≥ 6 years | 60 points |
| Experience ≥ 3 years (but < 6) | 40 points |
| Experience ≥ 1 year (but < 3) | 20 points |
| Experience < 1 year | 0 points |
| Target position matches one of 9 valid positions | 20 points |
| All required fields complete (name, age, area, position, experience, phone) | 20 points |
| **Maximum score** | **100 points** |

**Business Rule:** Score is computed at the end of the 7-step session and stored with the candidate record. Higher scores are surfaced first in admin review lists.

**Source Module:** `modules/recruitment_flow`
**Source Function:** `_compute_score()`
**PKCA Report:** 10_hidden_rule_coverage_report.md (HK-34)
**Management Authority:** HK-34 approved in PKVC Management Decisions

---

## Recruitment Session TTL

**Business Rule:** A recruitment data-collection session expires after **24 hours** of inactivity.

**Example:** If a candidate starts providing data but does not complete the session within 24 hours, the session is marked expired. The candidate must restart from step 1 on their next message.

**Source Module:** `modules/recruitment_flow`
**Source Function:** `SESSION_TTL`
**PKCA Report:** 09_state_machine_coverage_report.md (SM-04), 10_hidden_rule_coverage_report.md (HK-33)
**Management Authority:** HK-33 approved in PKVC Management Decisions

---

## Recruitment AI Deterministic Fast-Replies

### Purpose
Common recruitment questions are answered deterministically (without LLM) using the `modules/recruitment_ai` brain. This guarantees accuracy and prevents hallucinations on factual recruitment information.

### Fast-Reply Triggers (No LLM Used)

| Question Type | Trigger Detection | Response |
|---|---|---|
| Fee / joining fee | 12 fee-related Bangla phrases | Factual fee answer from source_of_truth.txt |
| Contact number | Contact query phrases | Official contact number from source_of_truth.txt |
| Office location | Office location phrases | Office location from source_of_truth.txt |
| Age limit | Age-related phrases | "সাধারণ বয়সসীমা ১৮–৫৫ বছর।" |

**Safe Fallback (for unclear questions):**
```
এই বিষয়ে নিশ্চিত তথ্যের জন্য অফিসে যোগাযোগ করুন।
```

**Business Rule:** If the question is unclear or falls outside the 4 fast-reply categories, the safe fallback message is returned rather than using LLM.

**Source of Truth File:** `resources/ops/recruitment_source_of_truth.txt`

**Source Module:** `modules/recruitment_ai`
**Source Function:** `_looks_like_fee_question()`, `_looks_like_contact_question()`
**PKCA Report:** 08_ai_behavior_coverage_report.md
**Management Authority:** Production evidence; documented 2026-06-22
