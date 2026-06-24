---
title: PKMA Report 04 — Knowledge Governance Report
owner: Fazle Core Admin
status: archived
last_verified: 2026-06-24
runtime_index: false
---

# PKMA Report 04 — Knowledge Governance Report

**Program:** PKMA v1.0
**Date:** 2026-06-22
**Mode:** READ-ONLY

---

## Purpose

Assess the quality and completeness of knowledge governance: management decisions, conflict resolution, authority chains, pending approvals, and governance gaps.

---

## Governance Framework Status

| Governance Layer | Status |
|---|---|
| Management Decision Log | Active — 12 decisions formally recorded |
| Conflict Resolution System | Active — BR-25 RESOLVED; 3 DUPs pending |
| Production Verification Requirement | Enforced — Wave-1 pre-condition applied |
| PKCA Program | Completed — 20 reports generated 2026-06-22 |
| PKVC Program | Completed prior to Wave-1 |
| KSP Wave-1 | Completed — 21 articles enriched |
| PKMA Program | In Progress (this report) |
| KB Freeze Decision | Pending PKMA final decision |

---

## Management Decisions — Full Inventory

### Confirmed / Approved (12 decisions)

| Decision ID | Topic | Decision | Authority Level | Applied in KB |
|---|---|---|---|---|
| CON-01 | Payroll formula base | 12000/30 × duty_days | Management Approved | Yes (salary_workflow.md, escort_business_rules.md) |
| CON-02 | Payroll formula method | Per-day calculation, same formula | Management Approved | Yes (escort_business_rules.md) |
| CON-03 | Mongla transport rate | ৳800 BDT | Management Approved | Yes (escort_business_rules.md) |
| CON-04 | Food allowance | ৳150/day with time exceptions | Management Approved | Yes (escort_business_rules.md) |
| BR-25 | Candidate age range | 18–55 (was 18–45; RESOLVED) | Management Approved | Yes (recruitment_policy.md, recruitment_business_rules.md) |
| DUP-01 | Office address source of truth | KB canonical; FB page decorative | Management Approved | Not explicitly updated in Wave-1 (pre-existing) |
| DUP-02 | Salary ranges | KB as canonical | Management Approved | Not updated in Wave-1 |
| DUP-05 | Attendance vs attendance_parser | Separate modules; both valid | Management Approved | Yes (attendance_workflow.md) |
| DUP-07 | Joining fee display | KB simplified answer is correct | Management Approved | Confirmed; recruitment_policy.md reflects this |
| HK-01 | Silent-skip token list | 11 display-name tokens approved | Management Approved | Yes (ai_response_rules.md) |
| HK-03 | Safe auto-send intents | 9 intent types approved | Management Approved | Yes (ai_response_rules.md, permission_matrix.md) |
| HK-04 | Auto-send gate | Explicit intent required | Management Approved | Yes (ai_response_rules.md) |
| HK-09 | Draft-always roles | accountant + 3 client roles | Management Approved | Yes (ai_response_rules.md, role_permissions.md) |
| HK-13 | Loop protection | 3/120s → 600s pause | Management Approved | Yes (security_rules.md) |
| HK-19 | Mongla transport | ৳800 BDT (= CON-03) | Management Approved | Yes |
| HK-24 | Stale program alert | 30-day threshold | Management Approved | Yes (escort_workflow.md) |
| HK-33 | Recruitment session TTL | 24h | Management Approved | Yes (recruitment_business_rules.md) |
| HK-34 | Recruitment scoring | 100-point scale approved | Management Approved | Yes (recruitment_business_rules.md) |
| HK-44 | Reply cooldown | 60s (Redis + fallback) | Management Approved | Yes (security_rules.md) |

**Total approved decisions: 19 (some overlap CON/HK for same topic)**

---

### Pending / Unresolved (3 decisions)

| Decision ID | Topic | Conflict | Status | Impact |
|---|---|---|---|---|
| DUP-03 | Salary display format | KB uses one format; another source uses different | PENDING | Salary articles cannot be Certified until resolved |
| DUP-04 | Phone number format guidance | Potential inconsistency between user-facing and system-facing | PENDING | Identity articles cannot be Certified until resolved |
| DUP-06 | Recruitment FAQ wording | Minor wording differences between channels | PENDING | Recruitment articles cannot be Certified until resolved |

**Governance Blocker:** The 3 pending decisions prevent Level 4 (Certified) for the affected domains. Domains affected: Salary/Payroll, Identity, Recruitment.

---

## Production Verification Governance

### Pre-Condition Enforcement

The user explicitly required before Wave-1: **"Before editing any article, first verify that the production behavior is still active and not legacy or deprecated."**

This was enforced by:
1. Reading `modules/scheduler/__init__.py` `start_scheduler()` — confirmed all 15 jobs ACTIVE
2. Reading `modules/fazle_payroll_engine/workers.py` `start_workers()` — confirmed all 5 workers ACTIVE
3. Only then documenting behaviors

**Governance Rating:** PASS — production verification pre-condition was enforced for all Wave-1 enrichments.

### Verification Gaps

| Domain | Verification Status | Risk |
|---|---|---|
| Social Auto Reply | NOT VERIFIED — no KB article | HIGH — production behavior unknown |
| Database Behavior | NOT VERIFIED — 43 tables undocumented | HIGH — schema drift risk |
| RAG (BM25 params) | IDENTIFIED in PKCA but not verified against live code | MEDIUM |
| OCR Engine (full TypedDict) | PARTIALLY VERIFIED (6/18 fields) | MEDIUM |
| Messenger/Facebook | NOT VERIFIED | HIGH |

---

## Governance Authority Chain

| Level | Authority | Scope |
|---|---|---|
| Management Decision | Management of ASLS Ltd | Business rules, conflict resolution, approved behaviors |
| PKCA Auditor | Knowledge governance auditor | Coverage measurement, gap identification |
| PKVC Validator | Validation program | Accuracy verification, conflict detection |
| KSP Wave Leader | Knowledge synchronization | Enrichment execution (production verified only) |
| PKMA Assessor | Maturity assessor (this role) | Maturity scoring, freeze recommendation |

---

## Governance Quality Assessment

| Governance Dimension | Score | Evidence |
|---|---|---|
| Decision Log Completeness | 7/10 | 19 decisions recorded; some HKs not in formal log |
| Conflict Resolution Speed | 6/10 | BR-25 resolved promptly; DUP-03/04/06 still open |
| Production Verification Enforcement | 9/10 | Pre-condition enforced in Wave-1; Wave-0 had gaps |
| Traceability | 8/10 | Wave-1 changes.md has full evidence table |
| Revision History | 8/10 | All enriched articles have revision history entries |
| PKCA Coverage | 9/10 | 20 reports; comprehensive |
| Domain Completeness | 4/10 | 3 domains at Level 0 |
| **Overall Governance Score** | **7.3/10** | — |

---

## Open Governance Actions Required

| Action | Priority | Blocking |
|---|---|---|
| Resolve DUP-03 (salary display format) | HIGH | Level 4 for Payroll |
| Resolve DUP-04 (phone format guidance) | HIGH | Level 4 for Identity |
| Resolve DUP-06 (recruitment FAQ wording) | HIGH | Level 4 for Recruitment |
| Management ratification of identity algorithm priority order | MEDIUM | Level 3 for Identity |
| Management ratification of RBAC 5-level hierarchy | MEDIUM | Level 3 for RBAC |
| Management ratification of LLM provider selection | LOW | Level 3 for AI Behavior |
| Formal ratification of HK-12, HK-14, HK-15 | MEDIUM | Level 4 for Security |
| Create fpe_overview.md and ratify FPE behavior | HIGH | Level 2+ for Cash/FPE |
| Create social_auto_reply_system.md | HIGH | Level 1 for Social/Messenger/Facebook |
| Wave-2 enrichment of database_rules.md | HIGH | Level 2 for Database |

---

## KB Freeze Governance Prerequisites

For a KNOWLEDGE BASE FREEZE to be authorized:
1. All P1 domains must reach Level 3 (Management Approved) — **NOT MET** (FPE, Social Auto Reply at Level 0–1)
2. All DUP conflicts must be resolved — **NOT MET** (DUP-03, DUP-04, DUP-06 open)
3. No new PKVC conflicts unresolved — **NOT VERIFIED** (PKVC not re-run post-Wave-1)
4. All revision histories updated — **PARTIALLY MET** (Wave-1 articles updated; others not)
5. Management sign-off on freeze — **NOT RECEIVED**

**Governance Verdict: NOT READY FOR FREEZE**

---

*PKMA v1.0 | READ-ONLY | 2026-06-22*
