Source: knowledge_base/00_governance/README.md
SHA256: 24c42d26fdc056f06d7cfd54bca5be5814bcd7ee5fd159aaead3abf4ed11da8e
Version: 24c42d26fdc0

# 00 Governance — Fazle AI Platform
**Version:** 1.0.0
**Created:** 2026-06-22

This folder contains the permanent governance documents for the Fazle AI Platform.

These are NOT Knowledge Base articles. They do not document business rules or implementation.

They document **project state, governance policy, and continuity instructions** — everything a new AI assistant or team member needs to understand before starting any work.

---

## Contents

| File | Purpose | Read When |
|---|---|---|
| `project_context.md` | Complete project overview — what it is, what's done, what's frozen, how to work | **Always first** |
| `final_management_directive.md` | Permanent rules — what is prohibited, what is allowed, absolute safety rules | Before any implementation |
| `management_decisions.md` | Active management decisions — authoritative rules that override production | Before any feature work |
| `active_development_plan.md` | Current session objectives, phase sequence, proposal queue | Before any task |
| `phase1_rag_analysis.md` | PHASE 1 — RAG architecture analysis: verified parameters, gap report, RAG-001/RAG-002 proposals | When working on RAG module |
| `phase1_5_hybrid_rag_design.md` | PHASE 1.5 — Complete Hybrid RAG architecture design package: 14 sections, 14-task roadmap | When RAG-002 is approved |
| `module_alignment_report.md` | Phase 4 Step 5 — 52-module audit: coverage %, gaps, visibility risk, conflict risk per module | Before any module-specific work |
| `organizational_brain_gap_report.md` | Phase 4 Step 5 — 5 critical conflicts, 7 gap categories, Brain Readiness Score | Before KB enrichment or management decisions |
| `kb_enrichment_plan_v2.md` | Wave-3 enrichment plan — P0/P1/P2/P3 items, content templates, delivery sequence | When executing Wave-3 KB work |
| `claude_session3_audit_report.md` | Session 3 post-execution audit — code verification, governance gaps, 3 test failures, Unicode finding | When reviewing Session 3 work |
| `session4_audit_report_2026_06_23.md` | Session 4 audit — 16 test failures, uncommitted changes, THREE-WAY rate conflict, future dev plan | Latest audit — read after the above |
| `README.md` | This file — index of this folder | When navigating the folder |

---

## Quick Start for a New AI Session

**Read in this order:**

1. `project_context.md` — understand the project
2. `final_management_directive.md` — understand what you must not do
3. `management_decisions.md` — understand authoritative rules
4. `active_development_plan.md` — understand what to work on
5. Relevant `knowledge_base/06_developer_system/` article — understand the specific domain
6. Production code — only for comparison against KB

**Never skip steps 1–5.**

---

## What This Folder Is NOT

- Not the Knowledge Base (that is in `01_employee_knowledge/` through `06_developer_system/`)
- Not business rule documentation
- Not workflow documentation
- Not developer system documentation
- Not AI identity documentation

This folder is the **project governance layer** that sits above the Knowledge Base.

---

## Change Policy

This folder follows the same Controlled Freeze policy as KB v1.0.

Allowed changes: corrections, append-only updates, management decision additions, revision history.
Not allowed: restructuring, deletion, authority changes.

All changes require a revision history entry in the affected file.
