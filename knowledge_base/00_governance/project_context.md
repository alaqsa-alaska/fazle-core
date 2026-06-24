---
title: Project Context — Fazle AI Platform
owner: Fazle Core Admin
status: active
last_verified: 2026-06-24
runtime_index: true
---

# Project Context — Fazle AI Platform
**Version:** 1.0.0
**Created:** 2026-06-22
**Last Updated:** 2026-06-22
**Status:** AUTHORITATIVE — Read this file first before any analysis, planning, implementation, or proposal.

---

## Section 1 — Project Overview

**Project Name:** Fazle AI Platform
**Company:** Al-Aqsa Security & Logistics Services Ltd.
**Location:** Bangladesh

**Main Objective:** A bilingual (Bangla/English) WhatsApp-based AI automation platform that manages employee communications, recruitment, attendance, payroll, escort operations, and client interactions — all through WhatsApp messages, with AI-generated replies, human-supervised draft approvals, and a backend that integrates all business operations.

**Organizational Brain Concept:** The Fazle AI Platform is not just a software project. It has a certified Knowledge Base (KB v1.0) that serves as the Organizational Brain — the permanent source of truth for all business rules, workflows, identity behavior, AI behavior, and developer documentation. The platform's AI, production code, and future features all follow the Knowledge Base, not the other way around.

---

## Section 2 — Current Project Status

| Area | Status |
|---|---|
| Knowledge Base v1.0 | ✅ MANAGEMENT APPROVED — CONTROLLED FREEZE |
| Production Platform | ✅ ACTIVE — UNCHANGED (Development Freeze) |
| PKVC Certification | ✅ PASSED (96/96 critical claims) |
| Session 1 (Knowledge Engineering) | ✅ COMPLETE |
| Session 2 (Knowledge-Driven Development) | 🔄 IN PROGRESS |
| AI Behavioral Validation | ⏳ NEXT |
| Production Feature Refactoring | ⏳ DEFERRED (after AI Behavioral Validation) |
| Hybrid RAG Integration | ⏳ DEFERRED |
| Organizational Brain Integration | ⏳ DEFERRED |

---

## Section 3 — Completed Milestones

| Milestone | What Was Achieved |
|---|---|
| Production Knowledge Mining (PKM) | All production modules read; 84 DB tables identified; business rules extracted from code |
| Production Knowledge Coverage Audit (PKCA) | Every KB article measured against production; gaps identified by severity |
| Production Knowledge Validation & Certification (PKVC) | Every KB claim cross-referenced to production source; inaccuracies corrected |
| Production Knowledge Maturity Assessment (PKMA) | All articles graded on maturity scale; 43 undocumented tables identified (later corrected to 84) |
| Knowledge Synchronization Wave-1 | Core automation pipeline, LLM chains, scheduler documented |
| Knowledge Synchronization Wave-2 | FPE, Social Auto Reply, RAG, OCR, developer notes, identity/role classifier documented |
| Entity Ownership Audit | 84 tables classified into 10 domains; 6 contested cases resolved; 3 left as pending |
| Database Domain Classification | All domains documented in database_rules.md |
| Visibility Matrix | Every KB article assigned visibility levels (Public/Employee/Supervisor/Admin/Developer/Restricted) |
| Traceability Validation | All new/enriched articles: source module, source function, PKMA/PKCA refs, revision history |
| Final PKVC Re-run | 5 inaccuracies found and corrected; all 96 critical claims re-certified |
| KB v1.0 Certification | Decision Tree passed all 3 gates; Management approved KB v1.0 Freeze |

---

## Section 4 — Current Development Phase

**Session 1 — Knowledge Foundation and Governance Program:** COMPLETE

Session 1 ran the full PKM → PKCA → PKVC → PKMA → Wave-1 → Wave-2 → Certification cycle. The Knowledge Base is now the Organizational Brain.

**Session 2 — Knowledge-Driven Application Development:** IN PROGRESS

The objective is to gradually align the Fazle AI Platform production codebase with the certified Knowledge Base. Work is incremental. Every implementation proposal requires:

1. Read KB article(s) first
2. Read production code second
3. Compare and identify gaps
4. Produce a formal proposal (6-part structure — see Section 11)
5. Stop and wait for management approval
6. Implement only after explicit approval

**Current objective:** AI Behavioral Validation — testing that the AI's live responses match the Knowledge Base's documented behavior.

---

## Section 5 — Knowledge Base Status

| Property | Value |
|---|---|
| Version | v1.0.0 |
| Freeze Status | CONTROLLED FREEZE |
| Certification Status | PASSED — PKVC Re-run 2026-06-22 |
| Certified Articles | 8 (all in `06_developer_system/`) |
| Critical Claims Verified | 96 / 96 |
| Overall KB Coverage | ~65–70% of platform knowledge |
| Developer System Maturity | Level 4 (production-verified) for 8 articles |
| Authority | Organizational Source of Truth — supersedes production code for business decisions |

**Certified articles:** `fpe_overview.md`, `social_auto_reply_system.md`, `database_rules.md`, `automation_pipeline.md`, `rag_strategy.md`, `ocr_engine.md`, `developer_notes.md`, `identity_brain.md`

**Not yet certified (stub articles remain):** `conversation_parser.md`, `event_pipeline.md`, `hybrid_search.md`, `parser_engine.md`, `system_prompt.md`, `visibility_rules.md`, `workflow_engine.md`

---

## Section 6 — Source of Truth Order

```
1. Management Decisions      ← Policy authority — always wins
        ↓
2. Knowledge Base v1.0       ← Organizational Brain — source of truth
        ↓
3. Current Production Code   ← Technical implementation — describes what is currently running
        ↓
4. Archived Resources        ← Historical reference — use with caution
```

**Critical rule:** When Management conflicts with Production, Management wins as policy. Production reality must still be documented as "Current Implementation" until production is updated. Never hide production reality — never silently ignore what production does.

---

## Section 7 — Current Architecture (Relationship Overview)

```
WhatsApp / Facebook / Meta
          ↓
   Bridge Pollers (bridge1:8082, bridge2:8081)
          ↓
   Message Router (identity + intent)
      ↓           ↓
Identity Brain   Intent Classifier
   (role)          (Ollama → Groq → GitHub)
      ↓
   RAG Layer (BM25 in-process, bilingual)
          ↓
   LLM Reply Chain (Ollama → Groq → GitHub → holding msg)
          ↓
   Draft Quality Gate
          ↓
   Outbound Queue (persistent, retry, DLQ)
          ↓
   WhatsApp Bridge → End User

Supporting systems (run in parallel):
- FPE (Fazle Payroll Engine) — 5 asyncio workers, immutable ledger
- Social Auto Reply — 21-file system, separate from main router
- OCR Engine — escort slips + future candidate CV
- Scheduler — 15 background jobs
- wa_chat_frontend — 23 REST endpoints + SSE stream
- Observability — Prometheus gauges, bridge health loop
```

**Note:** This overview shows relationships only. For implementation details, read the relevant KB article.

---

## Section 8 — Completed Audits Summary

| Audit | Key Output |
|---|---|
| PKM | All 84 production tables identified; all modules mapped; business rules extracted |
| PKCA | Coverage gaps by domain; 8 articles flagged as Level 1 stubs needing enrichment |
| PKVC | All KB claims cross-referenced to source; 5 inaccuracies corrected; 96 claims certified |
| PKMA | Maturity scores by domain; developer_system articles upgraded from Level 0–1 to Level 4 |

PKVC reports are archived at: `knowledge_base/07_archived/pkvc_reports_20260622/`
PKMA reports are archived at: `knowledge_base/07_archived/pkma_reports_20260622/`
Final completion report: `knowledge_base/07_archived/final_kb_completion_report_20260622.md`

---

## Section 9 — Active Management Decisions

Full list: see `00_governance/management_decisions.md`

Key decisions in effect:
- **BR-25:** Employee age range = 18–55 (not 18–45)
- **Escort payment formula:** 12,000 ÷ 30 × duty_days (management-approved; not count of days present)
- **Mongla transport:** ৳800 per assignment (not variable)
- **Food cost:** ৳150/day
- **OCR scope:** Escort release slips (current) + candidate CV extraction (future, approved)
- **FPE and Social Auto Reply:** Documented as separate subsystems; no merging
- **KB v1.0 Freeze:** Management approved 2026-06-22 under Controlled Freeze policy
- **Documentation-First Policy:** No production feature may begin without KB Update → Management Approval

---

## Section 10 — Current Technical Debt

These are non-blocking. They do not affect production stability or KB certification.

| Item | Risk | Resolution Path |
|---|---|---|
| U-01: `wbom_candidates` — exists in migration 003 FK but not in current schema | Low | Single psql `\d+` check in future v1.0.x |
| U-02: `fpe_transaction_repairs` — referenced in FPE docs but no DDL found | Low | Single psql `\dt` check in future v1.0.x |
| U-03: `wbom_staging_payments` naming vs `fpe_staging_payments` | Low | Schema verification in future v1.0.x |
| 9 stub articles in `06_developer_system/` | Low | Wave-3 if approved |
| `03_developer_system/` legacy path duplication | Low | Consolidation decision if approved |

---

## Section 11 — Development Rules

**1. Documentation-First Policy**
No production feature may begin without: Business Requirement → KB Update → Management Approval → Implementation → Validation → Production Release.

**2. KB-First Reading Policy**
Every implementation task must begin by reading the relevant KB article(s) first, then production code. Never read production first.

**3. Approval-First Policy**
Every implementation proposal must stop and wait for explicit management approval before any production code changes.

**4. Production Freeze**
The Development Freeze remains in effect until management explicitly authorizes implementation work on a specific feature.

**5. Controlled KB Freeze**
KB v1.0 is under Controlled Freeze. Only allowed: typo/grammar corrections, broken links, incorrect references, append-only production discoveries (after verification + approval), management decision updates, security corrections.

**6. 6-Part Proposal Structure**
Every implementation proposal must include:
- Current production behavior
- Knowledge Base reference
- Gap analysis
- Proposed implementation
- Risk assessment
- Rollback strategy

**7. Role-Based Visibility**
Never expose to candidates or employees: database internals, API routes, worker implementation, financial constants, security mechanisms, prompt injection protection, developer-only architecture.

---

## Section 12 — AI Working Rules

Every future AI assistant assigned to this project must follow these rules:

1. **Read this file first.** Before any analysis, planning, or implementation.
2. **Read `00_governance/final_management_directive.md`.** Understand what is prohibited.
3. **Read relevant KB articles before production code.** KB is the source of truth.
4. **Never restart completed audits.** PKM, PKCA, PKVC, PKMA are done. Do not reopen them.
5. **Never redesign the Knowledge Base.** It is under Controlled Freeze. Only maintenance is allowed.
6. **Never invent knowledge.** Every claim must be traceable to a production source or management decision.
7. **Never implement without approval.** Always stop and present a proposal.
8. **Never hide production reality.** If production differs from KB, document it as a gap — don't pretend it doesn't exist.
9. **Never bypass visibility.** Respect the visibility classification of every knowledge item.
10. **Ask for a management decision when uncertain.** Do not assume. Do not invent.

---

## Section 13 — Future Roadmap

| Phase | Description | Status |
|---|---|---|
| AI Behavioral Validation | Test live AI responses against KB behavior | Next |
| KB v1.0 Freeze Maintenance | Typo corrections, U-01/U-02/U-03 verification | Ongoing |
| Hybrid RAG Integration | Align RAG behavior with rag_strategy.md | Deferred |
| Identity Brain Alignment | Align message_router with identity_brain.md | Deferred |
| Organizational Brain Integration | Connect KB directly into AI runtime context | Deferred |
| Production Feature Refactoring | Align production modules with KB documentation | Deferred (after validation) |
| KB v1.1.x Expansion | Wave-3 stub articles + legacy path consolidation | Deferred |

---

## Section 14 — Current Priorities (Maximum 5)

1. **AI Behavioral Validation** — Verify live AI behavior matches KB-documented behavior
2. **U-01/U-02/U-03 Schema Verification** — Single psql queries to resolve 3 low-risk pending items
3. **Production Stability** — No changes that risk the current working platform
4. **KB Controlled Freeze Maintenance** — Respond to append-only discoveries as they arise
5. **Documentation-First on all future features** — Every new feature begins with KB update

---

## Section 15 — Files Every AI Should Read First

Read in this exact order before any work:

| Order | File | Purpose |
|---|---|---|
| 1 | `knowledge_base/00_governance/project_context.md` | This file — project state overview |
| 2 | `knowledge_base/00_governance/final_management_directive.md` | What is prohibited and what is allowed |
| 3 | `knowledge_base/00_governance/management_decisions.md` | Active management decisions in force |
| 4 | `knowledge_base/00_governance/active_development_plan.md` | Current session objectives and task queue |
| 5 | Relevant KB article(s) in `knowledge_base/06_developer_system/` | Domain-specific knowledge |
| 6 | Production code — only the module relevant to the task | Compare against KB |

Never skip steps 1–5 before reading production code.

---

## Section 16 — Change Control

**Controlled Freeze Allowed Changes:**
Typo/grammar corrections, broken link fixes, incorrect reference corrections, append-only production discoveries (after verification + management approval), management decision updates, security corrections, revision history additions.

**Append-Only Policy:**
New knowledge may only be added to existing articles. Articles may not be restructured, reordered, or deleted.

**Versioning:**
- `v1.0.x` — Documentation corrections only (no approval required for typos; management approval for factual changes)
- `v1.1.x` — Knowledge expansion (requires Wave-3 authorization)
- `v2.x` — Architecture changes (requires full new certification cycle)

**Approval Workflow:**
Discover → Verify against production → Classify (Current/Legacy/Deprecated/Experimental) → Prepare append-only proposal → Wait for management approval → Apply with revision history entry.

---

## Section 17 — Version History

| Date | Event | Version |
|---|---|---|
| 2026-06-22 | Knowledge Base v1.0 management approved; Session 1 complete | KB v1.0.0 |
| 2026-06-22 | Session 2 (Knowledge-Driven Application Development) opened | — |
| 2026-06-22 | This document created | project_context.md v1.0.0 |

---

## Section 18 — Session Bootstrap

**Every future AI assistant must read this file before performing any analysis, planning, implementation, or proposal.**

If you are starting a new session on this project:
1. Read this file completely.
2. Read `final_management_directive.md`.
3. Read `management_decisions.md`.
4. Read `active_development_plan.md`.
5. Ask: "Which KB article is relevant to today's task?"
6. Read that article.
7. Only then read production code — for comparison, not as the source of truth.

You are operating an **Organizational Brain**. The Knowledge Base is not documentation. It is the law of this platform.

---

## Section 19 — Final Reminder

**This document is NOT the Knowledge Base.**

This document explains the project: its state, its governance, its history, and its rules.

**The Knowledge Base remains the Organizational Brain.**

The Knowledge Base is in `knowledge_base/` — specifically the certified articles in `knowledge_base/06_developer_system/` and the supporting articles in `01_employee_knowledge/` through `05_workflows/`.

Do not confuse governance documents (this folder) with knowledge articles (the KB). They serve different purposes.

---

*End of Project Context v1.0.0*
