---
title: PKMA Report 19 — Organizational Brain Readiness
owner: Fazle Core Admin
status: archived
last_verified: 2026-06-24
runtime_index: false
---

# PKMA Report 19 — Organizational Brain Readiness

**Program:** PKMA v1.0
**Date:** 2026-06-22
**Mode:** READ-ONLY

---

## Purpose

Assess whether the Fazle AI Platform Knowledge Base has reached sufficient maturity to serve as the organizational brain — the authoritative, self-sufficient knowledge system that can onboard new team members, train new AI models, survive staff turnover, guide incident response, and pass external audits without requiring access to source code.

This is distinct from coverage (PKCA) and maturity score (PKMA). Organizational Brain Readiness answers: **can the KB alone be trusted to run this platform?**

---

## 5 Readiness Dimensions

---

## Dimension 1: Onboarding Readiness

**Question:** Can a new team member (developer, admin, accountant) understand their role, their tools, and the platform rules from the KB alone?

| Role | KB Readiness |
|---|---|
| Superadmin | 65% — role gate behaviors documented; command reference complete; missing: edge case resolution |
| Admin | 65% — 37 commands documented; workflows documented; missing: incident escalation path |
| Accountant | 55% — payment workflows documented; FPE entirely undocumented; cash shorthand parser missing |
| Operator | 50% — basic role documented; full permission scope unclear |
| Developer | 45% — 5 articles enriched; 11 articles are stubs; database entirely undocumented |
| Employee | 70% — recruitment, attendance, payment all documented; language accessibility unclear |
| Candidate | 75% — recruitment workflow fully documented; age/position/scoring all current |

**Average Onboarding Readiness: 61%**
**Verdict: CONDITIONAL** — Suitable for admins and employees. Insufficient for developers and accountants.

---

## Dimension 2: Incident Response Readiness

**Question:** When something fails in production, can an on-call person diagnose and respond using the KB alone?

| Scenario | KB Coverage |
|---|---|
| Outbound messages stuck in DLQ | 80% — DLQ, dlq_alert job, SCHEDULE STATUS documented |
| Escort payment calculation wrong | 85% — Transport table, food rules, formula all documented |
| Recruitment session expired early | 80% — 24h TTL, session state machine documented |
| Employee identity wrong role | 60% — 11-step algorithm documented; confidence thresholds known; SQL patterns not in KB |
| Payroll draft not created | 75% — daily_payroll_compute job, PAYROLL START documented |
| RAG answers wrong | 20% — rebuild schedule known; BM25 parameters entirely undocumented |
| Bridge down (HR/OPS) | 65% — ports documented, watchdog job documented; restart procedure missing |
| FPE stuck | 0% — FPE entirely undocumented in KB |
| Social auto-reply wrong | 0% — entirely undocumented |
| Draft quality rejection | 10% — quality gate mentioned; states and criteria not documented |

**Average Incident Response Readiness: 58%**
**Verdict: CONDITIONAL** — Standard operational incidents (payments, recruitment, escort) can be handled. Infrastructure failures and undocumented systems (FPE, social) cannot be diagnosed from KB.

---

## Dimension 3: AI Training Readiness

**Question:** Can a new AI model be trained or configured from the KB alone to replicate the current platform behavior?

| AI Behavior | KB Coverage |
|---|---|
| Silent-skip rules | 95% — 11 tokens, 3 conditions, all documented |
| Role gate behaviors | 90% — 12 roles, 4 gate behaviors, management-approved |
| 9 safe auto-send intents | 90% — intent list and advance_request exclusion documented |
| Draft-always gate | 90% — 4 roles documented with rationale |
| Recruitment AI (4 categories) | 85% — categories, fallback, age, positions documented |
| LLM chain order (reply) | 70% — provider order documented; model names not in KB |
| LLM chain order (intent) | 70% — provider order documented; model names not in KB |
| RAG retrieval parameters | 5% — algorithm named; no parameters |
| Prompt injection patterns | 50% — 18 patterns known; exact patterns not in KB |
| Office location fast path | 85% — bypass documented |

**Average AI Training Readiness: 73%**
**Verdict: CONDITIONAL** — Routing and safety rules are highly documentable. RAG configuration and model-specific prompts are not ready for AI handoff.

---

## Dimension 4: Audit Readiness

**Question:** Could an external auditor verify that the platform's behavior matches its stated rules and management decisions?

| Audit Domain | Evidence Available in KB |
|---|---|
| Recruitment age policy (BR-25) | Yes — management decision recorded; KB and code aligned |
| Payroll formula (CON-01/02) | Yes — formula documented; state machine documented |
| Escort payment rules (CON-03/04) | Yes — transport table, food rules documented |
| Admin command access control | Yes — role requirements documented for all 37 commands |
| Security rules (HK-13, HK-44) | Yes — loop, flood, injection, cooldown all documented |
| FPE financial processing | No — 0% documented |
| Backup retention compliance | Partial — policy exists; no management approval for retention terms |
| Data deletion / soft-delete | No — soft-delete pattern undocumented |
| GDPR / data subject rights | No — no data rights documentation exists |

**Audit Readiness Score: 55%**
**Verdict: NOT READY FOR EXTERNAL AUDIT**
FPE, data lifecycle, and soft-delete are not audit-ready. A financial audit of the FPE system would fail immediately.

---

## Dimension 5: Staff Turnover Readiness

**Question:** If the platform owner (Fazle) becomes unavailable for 1 week, can the team maintain the platform from the KB?

| Function | KB Readiness |
|---|---|
| Normal operations (admin commands) | 80% |
| Escort order processing | 75% |
| Payroll run | 75% |
| Attendance management | 75% |
| Recruitment processing | 80% |
| Emergency payment | 65% |
| DLQ recovery | 70% |
| Bridge restart | 60% |
| FPE recovery | 0% |
| Social auto-reply management | 0% |
| Database repair | 10% |

**Average Turnover Readiness: 63%**
**Verdict: CONDITIONAL** — Normal operations can be maintained. FPE, social auto-reply, and database repair require the original author.

---

## Organizational Brain Score

| Dimension | Score | Verdict |
|---|---|---|
| Onboarding Readiness | 61% | CONDITIONAL |
| Incident Response Readiness | 58% | CONDITIONAL |
| AI Training Readiness | 73% | CONDITIONAL |
| Audit Readiness | 55% | NOT READY |
| Staff Turnover Readiness | 63% | CONDITIONAL |
| **Overall Brain Readiness** | **62%** | **CONDITIONAL** |

---

## CONDITIONAL → READY Requirements

To achieve full Organizational Brain Readiness, the following must be completed:

### Must-Have (Wave-2)

| Item | Impact |
|---|---|
| Create `fpe_overview.md` (5 workers, state machine, 4 tables, API routes) | Unblocks Audit, Incident, Turnover |
| Create `social_auto_reply_system.md` (20-file system) | Unblocks Incident, Turnover |
| Enrich `database_rules.md` (43 tables) | Unblocks Developer Onboarding, Audit |
| Document Draft Quality Gate (4 criteria, states) | Unblocks Incident Response |
| Management approval: identity resolution algorithm | Unblocks Audit |
| Management approval: payroll compute schedule | Unblocks Audit |
| Management approval: backup retention policy | Unblocks Audit |

### Should-Have (Wave-3)

| Item | Impact |
|---|---|
| Enrich `rag_strategy.md` with BM25 params | Unblocks AI Training, Incident (RAG) |
| Document system prompts per role | Unblocks AI Training |
| Document data lifecycle / soft-delete | Unblocks Audit (GDPR) |
| Document bridge restart procedure | Unblocks Incident Response |
| Document contact sync canonical format | Unblocks Onboarding |

---

## Organizational Brain Gap Map

```
Domain              | L0 | L1 | L2 | L3 | L4 | L5 | Blocking Readiness?
--------------------|----|----|----|----|----|----|---
FPE                 | ██ |    |    |    |    |    | YES — Audit, Turnover
Social Auto Reply   | ██ |    |    |    |    |    | YES — Turnover, Incident
Database            | ██ | █  |    |    |    |    | YES — Onboarding, Audit
Parser Engine       |    | ██ |    |    |    |    | YES — Onboarding, AI Training
RAG                 |    | ██ |    |    |    |    | YES — AI Training, Incident
OCR Engine          |    | █  |    |    |    |    | Partial — Incident
Scheduler           |    |    | ██ |    |    |    | Conditional
AI Behavior         |    |    | ██ |    |    |    | Conditional
Identity Brain      |    |    | ██ |    |    |    | Conditional
Workflows           |    |    |    | ██ |    |    | READY
Business Rules      |    |    |    | ██ |    |    | READY
Security Rules      |    |    |    | ██ |    |    | READY
Recruitment         |    |    |    | ██ |    |    | READY
Payroll             |    |    |    | ██ |    |    | READY
```

---

## Overall Assessment

**Organizational Brain Status: CONDITIONAL — NOT READY FOR FREEZE**

The Knowledge Base is functional for normal operations but is not yet an authoritative self-sufficient organizational brain. Three specific gaps prevent readiness: FPE (entirely undocumented financial engine), social auto-reply (entirely undocumented channel), and the database layer (43 undocumented tables).

Wave-1 achieved remarkable coverage improvement (14% → 40%) and elevated 10 domains to Level 3. The platform can now be maintained day-to-day by experienced admins. But the organizational resilience goal — platform survives staff absence without institutional knowledge loss — requires Wave-2.

---

*PKMA v1.0 | READ-ONLY | 2026-06-22*
