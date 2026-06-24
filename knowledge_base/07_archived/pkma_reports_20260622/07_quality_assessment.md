---
title: PKMA Report 07 — Quality Assessment
owner: Fazle Core Admin
status: archived
last_verified: 2026-06-24
runtime_index: false
---

# PKMA Report 07 — Quality Assessment

**Program:** PKMA v1.0
**Date:** 2026-06-22
**Mode:** READ-ONLY

---

## Purpose

Measure the quality of the Knowledge Base across six dimensions: accuracy, completeness, consistency, precision, currency, and usability. This is distinct from coverage (PKCA) and maturity (PKMA matrix) — it assesses HOW WELL the documented knowledge is expressed.

---

## Quality Dimensions

| # | Dimension | Definition |
|---|---|---|
| Q1 | Accuracy | Does the KB reflect actual production behavior? |
| Q2 | Completeness | Does the KB cover all aspects of each documented domain? |
| Q3 | Consistency | Are the same facts expressed consistently across articles? |
| Q4 | Precision | Are rules precise enough to make deterministic decisions? |
| Q5 | Currency | Is the KB up to date with the latest production state? |
| Q6 | Usability | Can an admin/developer/AI use the KB to make decisions? |

Scoring: 0–10 per dimension per domain group.

---

## Q1 — Accuracy Assessment

### Wave-1 Enriched Articles (21 articles)

All Wave-1 enrichments followed the mandatory pre-condition: **production code read before writing to KB**. Evidence:
- Scheduler: all 15 jobs read from `modules/scheduler/__init__.py` before documenting
- FPE workers: all 5 read from `modules/fazle_payroll_engine/workers.py` before documenting
- Business rules: source modules read for each HK/CON rule
- BR-25: production code `modules/recruitment_flow._parse_age()` confirmed 18–55 before updating KB

**Accuracy Score for Wave-1 articles: 9.5/10**
Deduction: 0.5 for the 6/18 OCR fields documented without full TypedDict verification.

### Pre-Wave-1 Articles (44 articles)

Pre-Wave-1 articles were not all production-verified. PKVC (run before Wave-1) found conflicts including BR-25 (age 18-45 vs 18-55 in code). Unknown number of other pre-existing accuracy issues remain in non-enriched articles.

**Accuracy Score for pre-Wave-1 non-enriched articles: 5/10**
High uncertainty; some articles may have been written from design documents rather than production code.

**Overall Accuracy Score: 7/10**

---

## Q2 — Completeness Assessment

| Article Group | Completeness Score | Notes |
|---|---|---|
| 04_business_rules/ (4 articles, enriched) | 8/10 | Good coverage; OCR full TypedDict missing |
| 05_workflows/ (6 articles, enriched) | 8/10 | State machines complete; parser regex missing |
| 06_developer_system/ (5 of 7 enriched) | 7/10 | database_rules and rag_strategy not enriched |
| 02_admin_knowledge/ (2 articles, enriched) | 9/10 | 37 commands complete; edge cases not covered |
| 03_ai_identity/ (2 articles, enriched) | 8/10 | Algorithm complete; role_classifier injection missing |
| 01_employee_knowledge/ (BR-25 only) | 7/10 | Recruitment policy updated; others not Wave-1 scope |
| 06_developer_system/database_rules.md | 1/10 | Abstract only; 43 tables undocumented |
| 06_developer_system/rag_strategy.md | 2/10 | Abstract; BM25 params absent |
| FPE domain (no article) | 0/10 | No article exists |
| Social Auto Reply (no article) | 0/10 | No article exists |
| **Overall Completeness** | **5.5/10** | Weighted across all 65 articles |

---

## Q3 — Consistency Assessment

### Positive — Consistent Across Articles

| Fact | Articles Referencing It | Consistency |
|---|---|---|
| Age range 18–55 | recruitment_policy.md, recruitment_business_rules.md | Consistent (BR-25 fixed) |
| Payment formula 12000/30×days | salary_workflow.md, escort_business_rules.md | Consistent |
| Mongla transport ৳800 | escort_business_rules.md | Single source; consistent |
| Silent-skip 3 conditions | ai_response_rules.md, permission_matrix.md | Consistent |
| 9 safe auto-send intents | ai_response_rules.md, permission_matrix.md | Consistent |
| Draft-always 4 roles | ai_response_rules.md, role_permissions.md, permission_matrix.md | Consistent |
| Bootstrap admin | admin_role_management.md, role_permissions.md | Consistent |

### Pending — Consistency Unresolved

| Fact | Conflict | DUP ID |
|---|---|---|
| Salary display format | KB format vs. other source | DUP-03 |
| Phone format guidance | KB vs. other channel | DUP-04 |
| Recruitment FAQ wording | KB vs. FB page | DUP-06 |

**Consistency Score: 7.5/10** (high within Wave-1 articles; 3 pending conflicts)

---

## Q4 — Precision Assessment

Precision = Can a rule be applied deterministically without human interpretation?

| Knowledge Type | Precision Level | Example |
|---|---|---|
| Transport rate table | PRECISE | Dhaka=600, Mongla=800 — exact BDT values |
| Food calculation formula | PRECISE | 150/day with exact time exceptions |
| Payroll formula | PRECISE | 12000/30 × duty_days |
| Recruitment scoring | PRECISE | Experience 60/40/20/0; position 20; completeness 20 |
| Session TTL | PRECISE | 24 hours |
| Reply cooldown | PRECISE | 60 seconds |
| Loop protection | PRECISE | 3 messages in 120s → pause 600s |
| LLM chain order | PRECISE | GitHub → Groq → Ollama (reply); Groq → GitHub → Ollama (intent) |
| 11-step identity resolution | PRECISE | Ordered table with winner-takes-first |
| Admin command syntax | PRECISE | All 37 commands have syntax documented |
| Silent-skip tokens | PRECISE | 11 exact display-name tokens |
| State machine transitions | PRECISE | ALLOWED_TRANSITIONS table |
| Draft-always roles | PRECISE | 4 specific roles listed |
| Identity confidence thresholds | PRECISE | 1.0 / 0.95 / 0.7 / 0.5 / 0.0 |
| OCR document types | PRECISE (4 types) | But 12 TypedDict fields undocumented |
| Business rule descriptions | IMPRECISE (some) | General language in pre-Wave-1 articles |
| Database schema | IMPRECISE | Abstract; no column-level precision |
| RAG behavior | IMPRECISE | Abstract description only |

**Precision Score: 8/10** (for Wave-1 articles); **4/10** (for non-enriched articles)
**Overall Precision Score: 6.5/10**

---

## Q5 — Currency Assessment

Currency = KB reflects the current production state (not a historical or planned state).

| Category | Currency | Evidence |
|---|---|---|
| All Wave-1 enrichments | CURRENT | Production code verified before documenting |
| Scheduler jobs (15) | CURRENT | Verified ACTIVE in start_scheduler() 2026-06-22 |
| FPE workers (5) | CURRENT | Verified ACTIVE in start_workers() 2026-06-22 |
| BR-25 age range | CURRENT | Updated to 18–55 matching production |
| Pre-Wave-1 articles (non-enriched) | UNCERTAIN | Not re-verified; may lag production changes |
| Social Auto Reply | MISSING | Not in KB; production behavior unknown |
| Database schema | STALE RISK | 43 tables undocumented; schema drift undetected |
| OCR TypedDict (12 fields) | MISSING | May have changed since last audit |
| RAG parameters | MISSING | BM25 params not in KB; cannot detect drift |

**Currency Score: 8/10** (for what is documented); **4/10** (for undocumented domains where drift is undetectable)
**Overall Currency Score: 6/10**

---

## Q6 — Usability Assessment

Usability = Can the target audience (admin, developer, AI) use the KB to make decisions?

| Target User | Usability for Available Domains | Gaps |
|---|---|---|
| AI (RAG retrieval) | GOOD — enriched articles are precise enough for bilingual retrieval | Missing: FPE, social auto reply, database |
| Admin (WhatsApp commands) | GOOD — all 37 commands documented with syntax and required roles | Gap: edge cases for command failures |
| Developer (onboarding) | MODERATE — business logic documented; schema missing | Gap: database_rules, rag_strategy, FPE |
| Management (oversight) | GOOD — state machines, rules, management decisions all in KB | Gap: no executive dashboard article |
| New employee (self-serve) | LIMITED — employee articles exist but social channels undocumented | Gap: recruitment FAQ wording (DUP-06) |
| Auditor | GOOD — traceability exists for Wave-1 enrichments | Gap: pre-Wave-1 articles lack traceability |

**Usability Score:**
- For enriched domains: 8/10
- For undocumented domains: 0/10
- **Overall Usability: 6.5/10**

---

## Overall Quality Summary

| Dimension | Score | Grade |
|---|---|---|
| Q1 Accuracy | 7.0 / 10 | B |
| Q2 Completeness | 5.5 / 10 | C |
| Q3 Consistency | 7.5 / 10 | B+ |
| Q4 Precision | 6.5 / 10 | B- |
| Q5 Currency | 6.0 / 10 | C+ |
| Q6 Usability | 6.5 / 10 | B- |
| **Composite Quality Score** | **6.5 / 10** | **B-** |

---

## Quality Improvement Roadmap

| Action | Dimension Improved | Impact |
|---|---|---|
| Create fpe_overview.md | Q2, Q5, Q6 | +0.5 on Completeness, Currency |
| Create social_auto_reply_system.md | Q2, Q5, Q6 | +0.5 on Completeness |
| Enrich database_rules.md (43 tables) | Q2, Q4, Q6 | +1.0 on Completeness |
| Enrich rag_strategy.md (BM25 params) | Q2, Q4, Q5 | +0.3 on each |
| Resolve DUP-03/04/06 | Q3 | +0.5 on Consistency |
| Run PKVC post-Wave-1 | Q1 | +0.5–1.0 on Accuracy |
| Enrich ocr_engine.md (full TypedDict) | Q2, Q4 | +0.3 on each |
| Document parser regex patterns | Q4 | +0.2 on Precision |
| **Target after Wave-2** | All dimensions | **~7.5/10** |

---

*PKMA v1.0 | READ-ONLY | 2026-06-22*
