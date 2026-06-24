---
title: FAZLE AI PLATFORM
owner: Fazle Core Admin
status: active
last_verified: 2026-06-24
runtime_index: true
---

# FAZLE AI PLATFORM
# Knowledge Base Transformation & Integration Program (KBTI)
## Pre-Execution Deliverables — Version 1.0

**Date:** 2026-06-21
**Status:** AWAITING MANAGEMENT APPROVAL — Zero KB files modified
**Authority:** KBTI Program v1.0
**Sources Reviewed:**
- `latestaudit21062026.txt` — 28-report Production Knowledge Mining result
- `07_archived/pkvc_reports_20260621/` — 22 PKVC validation reports
- `07_archived/pkvc_reports_20260621/21_management_decisions_and_directives_20260621.md`
- `07_archived/pkvc_reports_20260621/22_pending_dup_evidence_20260621.md`
- `conflict_resolution_record.md`
- All existing KB articles in `01_employee_knowledge/` through `06_developer_system/`

---

# DELIVERABLE 1 — Knowledge Transformation Plan

## Current State Summary

| Layer | Source | Volume |
|---|---|---|
| Layer 0 — Authority | Management Decisions (21_management_decisions) + conflict_resolution_record | 9 resolved conflicts, 10 hidden-rule approvals, 3 pending DUPs |
| Layer 1 — Production | latestaudit21062026.txt (28 reports, 47 hidden rules, 8 state machines, 12 parsers, 38 commands, 14 scheduler jobs, 35+ tables) | ~1,200 lines of knowledge |
| Layer 2 — Validation | 22 PKVC reports (PKVC quality score 42/100; 23 missing articles; 4 conflicts resolved by mgmt) | 22 report files |
| Layer 3 — Existing KB | 65 existing articles across 6 folders | 65 files |
| Layer 4 — Archived | 3 source rewrites + 1 old_structure_mapping | 4 files |

## Transformation Principles

1. Every knowledge item from Layer 1 (production) that is missing from Layer 3 (KB) becomes a new article.
2. Every knowledge item that exists in Layer 3 but contradicts Layer 1 or Layer 0 gets updated using management-approved values.
3. Every knowledge item duplicated across multiple Layer 3 articles gets merged.
4. Every legacy/pre-reorganization Layer 3 article gets archived if superseded.
5. All 4 conflicts (CON-01 to CON-04) have management-approved resolutions and must be reflected.
6. Pending DUP decisions (DUP-03, DUP-04, DUP-06) have developer evidence and proposed resolutions — require formal approval below.

## Transformation Scope

| Category | Count | Action |
|---|---|---|
| New articles to create | 23 | CREATE (from missing knowledge MIS-01 to MIS-23) |
| New Knowledge Books to create | 14 | CREATE (KB-01 to KB-14) |
| Existing articles to enrich | 11 | UPDATE (partial coverage → complete) |
| Existing articles to correct | 4 | UPDATE (conflicted values → management-approved) |
| Existing articles to merge | 3 pairs | MERGE |
| Existing articles to archive | 2 folders | ARCHIVE (02_admin_system/, 03_developer_system/) |
| Authority folder to create | 1 | CREATE (00_authority/) |

## Pending DUP Decisions — Requires Formal Approval

Developer evidence has been gathered (see `22_pending_dup_evidence_20260621.md`). Proposed resolutions:

| DUP ID | Finding | Proposed Decision |
|---|---|---|
| DUP-03 | `phone_normalizer` produces one canonical string; `number_identity` produces a list of variants for DB matching — not identical | Mark as intentional layered design: phone_normalizer = canonicalization layer, number_identity = identity/variant matching layer |
| DUP-04 | Router calls `recruitment_flow.recruitment_eligibility()` — does NOT maintain its own keyword list | Confirm router as delegator; `recruitment_flow` is the single authority for recruitment keywords |
| DUP-06 | `fazle_draft_replies` = conversational/admin-review drafts; `fazle_payment_drafts` = financial approval/ledger workflow drafts | Confirm split-by-purpose architecture; document each table's explicit responsibility |

**ACTION REQUIRED: Management must formally approve or override the three proposed DUP decisions above before the KBTI execution phase begins.**

---

# DELIVERABLE 2 — Knowledge Integration Matrix

For each knowledge domain, this matrix shows how the four layers converge into the final authoritative article.

| Domain | Layer 0 (Authority) | Layer 1 (Production) | Layer 2 (Validation) | Layer 3 (Current KB) | Final Article Action |
|---|---|---|---|---|---|
| Escort Payment Formula | CON-01: 12000/30×days approved | `payment_workflow.create_escort_payment_draft` | PKVC Conflict closed | `payment_business_rules.md` — may reflect old rate | UPDATE — inject approved formula |
| Payroll Formula | CON-02: unified 12000/30 formula | `payroll.DEFAULT_PER_PROGRAM_RATE` | PKVC Conflict closed | `payroll_rules.md` — incomplete state machine | UPDATE — add state machine + approved formula |
| Mongla Transport Rate | CON-03: Mongla=800 BDT approved | `escort_lifecycle._TRANSPORT_RATES` | PKVC Conflict closed | `transport_allowance.md` — may be stale | UPDATE — replace with approved rate table |
| Food Cost Policy | CON-04: 150/day+time exceptions approved | `escort_lifecycle._calc_duty_days` | PKVC Conflict closed | Missing | CREATE `food_cost_policy.md` |
| Silent-Skip Rules | HK-01, HK-02 approved | `message_router._should_silent_skip` | Missing from KB | Missing | CREATE `silent_skip_rules.md` |
| Draft-Always Gate | HK-09 approved | `bridge_poller._is_draft_always` | Missing from KB | Missing | CREATE `draft_always_gate.md` |
| Complaint Phrase Protection | HK-10, HK-11 approved | `bridge_poller._COMPLAINT_PHRASES` | Missing from KB | Missing | CREATE `complaint_protection_rules.md` |
| Loop + Flood Detection | HK-13, HK-14 approved | `bridge_poller` loop constants | Missing from KB | Missing | CREATE `loop_flood_protection.md` |
| Prompt Injection + Outbound Poison | HK-15, HK-12 — developer scope | `bridge_poller._PROMPT_INJECTION_PATTERNS` | Missing from KB | `security_rules.md` — partial | UPDATE + add injection/poison details |
| LLM Provider Fallback Chain | N/A (operational config) | `config.py` / AI behaviour report | Missing from KB | `ai_system_prompt.md` — partial | UPDATE + CREATE `llm_provider_chain.md` |
| Admin Command Reference (38 cmds) | N/A | `admin_commands/__init__.py` | Missing from KB | Missing | CREATE `admin_command_reference.md` |
| RBAC Permission Matrix | N/A | `rbac/__init__.py` | Missing from KB | `permission_matrix.md` — incomplete | UPDATE with full 5-role × 38-command table |
| Message Routing (15 steps) | N/A | `message_router.__init__.py` | Missing from KB | `workflow_engine.md` — partial | CREATE Knowledge Book `message_router_engine.md` |
| Identity Resolution Sources | N/A | `identity_brain.__init__.py` | Missing from KB | `identity_brain.md` — partial | UPDATE `identity_overview.md` + CREATE `identity_engine.md` |
| Payroll State Machine | N/A | `payroll.ALLOWED_TRANSITIONS` | Missing from KB | `payroll_rules.md` — partial | CREATE `payroll_state_machine.md` |
| Escort State Machine | N/A | `escort_lifecycle` | Partial | `escort_workflow.md` — partial | UPDATE with full state diagram |
| Recruitment Scoring | HK-34 approved | `recruitment_flow._compute_score` | Missing from KB | `recruitment_business_rules.md` — partial | UPDATE with scoring algorithm |
| RAG Engine | N/A | `rag/__init__.py` | Missing from KB | `rag_rules.md`, `rag_strategy.md` — partial | UPDATE + CREATE `rag_engine.md` |
| Scheduler Jobs (14 jobs) | N/A | `scheduler/__init__.py` | Missing from KB | Missing | CREATE `scheduler_engine.md` |
| Social Auto-Reply (Facebook/Messenger) | DUP-07: salary_structure.txt is source of truth | `modules/social_auto_reply/` | Missing from KB | Missing | CREATE `social_engine.md` |
| Bridge Poller Architecture | N/A | `bridge_poller/__init__.py` | Missing from KB | Missing | CREATE `bridge_engine.md` |
| Accountant Payment Ingest | N/A | `modules/payment_ingest` | Missing from KB | `admin_payment_handling.md` — partial | CREATE `accountant_payment_ingest.md` |
| Advance Payment Triggers | N/A | `payment_workflow.ADVANCE_KEYWORDS` | Missing from KB | `cash_workflow.md` — partial | UPDATE + CREATE `advance_payment_rules.md` |
| OCR Release Slip Confidence | N/A | `escort_lifecycle.build_release_draft` | Missing from KB | `ocr_pipeline.md` — partial | UPDATE with confidence rules |
| Voice Processing | N/A | `modules/voice_processor` | Missing from KB | Missing | CREATE `voice_processing.md` |
| Phone Normalization (DUP-03) | Pending decision | `phone_normalizer`, `number_identity` | Pending | Missing | CREATE after DUP-03 decision |
| Draft Table Architecture (DUP-06) | Pending decision | `fazle_draft_replies`, `fazle_payment_drafts` | Pending | Missing | CREATE after DUP-06 decision |

---

# DELIVERABLE 3 — Article Mapping Report

## Existing Articles → Proposed Action

### 01_employee_knowledge/ (8 articles)

| File | Current Status | Proposed Action | Reason |
|---|---|---|---|
| `attendance_policy.md` | Exists, partial | ENRICH | Add attendance count rules: guard=12h/day, escort=24h/two shifts |
| `company_identity.md` | Exists, complete | NO CHANGE | Matches conflict resolution record (office address verified) |
| `faq_employee.md` | Exists, partial | ENRICH | Add advance payment range clarification (guideline not cap) |
| `leave_policy.md` | Exists, partial | ENRICH | Verify against production behavior |
| `recruitment_policy.md` | Exists, partial | ENRICH | Add 9 valid positions, age range 18–55, scoring algorithm |
| `release_slip.md` | Exists, partial | ENRICH | Add OCR confidence rules, required fields |
| `salary_policy.md` | Exists, partial | ENRICH | Confirm probation ৳17,000 + permanent ৳24,700 from social_auto_reply |
| `transport_allowance.md` | Exists, OUTDATED | UPDATE — CRITICAL | Must reflect CON-03 approved rates (Mongla=800 BDT) |

### 02_admin_knowledge/ (4 articles)

| File | Current Status | Proposed Action | Reason |
|---|---|---|---|
| `admin_attendance_handling.md` | Exists, partial | ENRICH | Add attendance parser distinction (guard vs escort) |
| `admin_operations_overview.md` | Exists, partial | ENRICH | Add bridge watchdog, DLQ, health summary notifications |
| `admin_payment_handling.md` | Exists, partial | ENRICH | Add PAID/ADVANCE/REVERSE/ADJUST command details |
| `admin_role_management.md` | Exists, partial | ENRICH | Add USER LIST/ADD/ROLE/REMOVE/APIKEY commands |

### 02_admin_system/ (5 articles) — LEGACY FOLDER

| File | Current Status | Proposed Action | Reason |
|---|---|---|---|
| `admin_business_rules.md` | Exists, overlaps 04_business_rules | ARCHIVE | Content merged into 04_business_rules articles |
| `attendance_workflow.md` | Exists, overlaps 05_workflows | ARCHIVE | Superseded by `05_workflows/attendance_workflow.md` |
| `escort_workflow.md` | Exists, overlaps 05_workflows | ARCHIVE | Superseded by `05_workflows/escort_workflow.md` |
| `payment_workflow.md` | Exists, overlaps 05_workflows | ARCHIVE | Superseded by `05_workflows/payment_workflow.md` |
| `payroll_rules.md` | Exists, partial, overlaps | ARCHIVE after UPDATE | Content must be merged into new `payroll_state_machine.md` |
| `release_slip_workflow.md` | Exists, overlaps 05_workflows | ARCHIVE | Superseded by `05_workflows/release_slip_workflow.md` |

### 03_ai_identity/ (8 articles)

| File | Current Status | Proposed Action | Reason |
|---|---|---|---|
| `accountant_identity.md` | Exists | ENRICH | Add draft-always gate info (HK-09) |
| `admin_identity.md` | Exists | NO CHANGE | Bootstrap admin logic is developer-only |
| `candidate_identity.md` | Exists, partial | ENRICH | Add candidate keyword list (10 Bangla/English keywords) |
| `employee_identity.md` | Exists, partial | ENRICH | Add 4 secondary evidence sources (cash, attendance, escort, contact) |
| `escort_identity.md` | Exists | NO CHANGE | Confirmed by production |
| `family_identity.md` | Exists | NO CHANGE | Confirmed by production |
| `identity_overview.md` | Exists, partial | UPDATE — CRITICAL | Add full 11-role priority table + 8 evidence sources + confidence scoring |
| `permission_matrix.md` | Exists, incomplete | UPDATE | Add full 5-role × 38-command RBAC table |
| `response_rules.md` | Exists | ENRICH | Add safe auto-send intent list (HK-03) |
| `vip_identity.md` | Exists | ENRICH | Add draft-always gate (HK-09) |

### 03_developer_system/ (6 articles) — LEGACY FOLDER

| File | Current Status | Proposed Action | Reason |
|---|---|---|---|
| `ai_system_prompt.md` | Exists, partial | ARCHIVE after content review | Superseded by `06_developer_system/system_prompt.md` |
| `identity_brain.md` | Exists, partial | ARCHIVE | Superseded by `06_developer_system/identity_brain.md` |
| `ocr_pipeline.md` | Exists, partial | ARCHIVE | Superseded by `06_developer_system/ocr_engine.md` |
| `parser_logic.md` | Exists | ARCHIVE | Superseded by `06_developer_system/parser_engine.md` |
| `rag_rules.md` | Exists, partial | ARCHIVE | Superseded by new `06_developer_system/rag_engine.md` |
| `security_rules.md` | Exists, partial | ARCHIVE | Superseded by `06_developer_system/security_rules.md` |
| `workflow_engine.md` | Exists, partial | ARCHIVE | Superseded by new Knowledge Books |

### 04_business_rules/ (8 articles)

| File | Current Status | Proposed Action | Reason |
|---|---|---|---|
| `ai_response_rules.md` | Exists, partial | ENRICH | Add AI safety mode, auto-reply gates |
| `attendance_business_rules.md` | Exists | ENRICH | Add backfill rule (ON CONFLICT DO NOTHING per day) |
| `cash_business_rules.md` | Exists | ENRICH | Add advance deduction scope (program + payroll month) |
| `escort_business_rules.md` | Exists, partial | UPDATE — CRITICAL | Update transport rates (CON-03), food rate (CON-04) |
| `joining_business_rules.md` | Exists | NO CHANGE | Verified against conflict resolution record |
| `payment_business_rules.md` | Exists, OUTDATED | UPDATE — CRITICAL | Must reflect CON-01 approved formula: 12000/30×days |
| `recruitment_business_rules.md` | Exists, partial | ENRICH | Add scoring algorithm, 9 positions, 24h TTL |
| `salary_business_rules.md` | Exists | ENRICH | Add probation ৳17,000, permanent ৳24,700 |

### 05_workflows/ (8 articles)

| File | Current Status | Proposed Action | Reason |
|---|---|---|---|
| `attendance_workflow.md` | Exists, partial | ENRICH | Add attendance parser distinction (guard vs escort-style) |
| `cash_workflow.md` | Exists, partial | ENRICH | Add advance keyword list (18 phrases) |
| `client_order_workflow.md` | Exists, partial | ENRICH | Add 4 parser formats, parser output fields |
| `escort_workflow.md` | Exists, partial | ENRICH | Add full escort state machine (6 states) |
| `payment_workflow.md` | Exists, OUTDATED | UPDATE — CRITICAL | Reflect CON-01 formula, add payment state machine |
| `recruitment_workflow.md` | Exists, partial | ENRICH | Add 6-step funnel with scoring output |
| `release_slip_workflow.md` | Exists, partial | ENRICH | Add OCR slip flow + transport rate + food deduction rules |
| `salary_workflow.md` | Exists | NO CHANGE | Verified |

### 06_developer_system/ (13 articles)

| File | Current Status | Proposed Action | Reason |
|---|---|---|---|
| `automation_pipeline.md` | Exists, partial | ENRICH | Add scheduler job list (14 jobs) |
| `conversation_parser.md` | Exists, partial | ENRICH | Add all 12 parsers from REPORT 11 |
| `database_rules.md` | Exists, partial | ENRICH | Add all 30+ tables, idempotency patterns, phone variants |
| `developer_notes.md` | Exists, partial | ENRICH | Add CON-01 to CON-04 implementation notes |
| `event_pipeline.md` | Exists, partial | ENRICH | Add bridge poll adaptive interval (1s–30s), cursor logic |
| `hybrid_search.md` | Exists, partial | ENRICH | Add BM25 params (k1=1.5, b=0.75), stop word notes |
| `identity_brain.md` | Exists, partial | UPDATE | Add full 8-source evidence chain, confidence table |
| `ocr_engine.md` | Exists, partial | ENRICH | Add OCR confidence rules (<40% warning), image criteria |
| `parser_engine.md` | Exists, partial | ENRICH | Add all 12 parsers with regex references |
| `rag_strategy.md` | Exists, partial | ENRICH | Add 3-layer safety, BM25 params, stop word rationale |
| `role_permissions.md` | Exists, incomplete | UPDATE | Add full RBAC command-to-role table |
| `security_rules.md` | Exists, partial | UPDATE | Add prompt injection patterns, outbound poison filter |
| `system_prompt.md` | Exists | NO CHANGE | Confirmed as developer-only |
| `visibility_rules.md` | Exists | NO CHANGE | Confirmed |
| `workflow_engine.md` | Exists, partial | ENRICH | Add 15-step routing priority |

---

# DELIVERABLE 4 — Knowledge Book Mapping

These are large-scope topics that require consolidated book-format articles:

| KB ID | Title | Target Folder | Estimated Size | Key Chapters | Source Reports |
|---|---|---|---|---|---|
| KB-01 | `message_router_engine.md` | `06_developer_system/` | Large (800–1200 lines) | Architecture, 15-step routing, silent-skip, draft-always gate, safe auto-send, complaint protection, loop detection, flood protection, prompt injection, outbound poison, examples | REPORT 3, 9 (WF-01), REPORT 5 |
| KB-02 | `identity_engine.md` | `06_developer_system/` | Medium (400–600 lines) | 11 roles, 8 evidence sources, role priority table, confidence scoring, candidate keywords, examples | REPORT 15 |
| KB-03 | `escort_engine.md` | `06_developer_system/` | Large (800–1200 lines) | Client order flow, 4 parser formats, vessel extraction, lifecycle, OCR slip, transport rates (CON-03), food policy (CON-04), state machine | REPORT 9 (WF-02, WF-03), REPORT 11 |
| KB-04 | `payment_engine.md` | `06_developer_system/` | Medium (400–600 lines) | Approved formula (CON-01), escort payment draft, advance flow, finalize, accountant message, idempotency, payment state machine | REPORT 9 (WF-03), REPORT 14 |
| KB-05 | `payroll_engine.md` | `06_developer_system/` | Medium (400–600 lines) | Unified formula (CON-02), state machine (6 states), per-program rate, transitions, audit log | REPORT 10 (SM-01), REPORT 14 |
| KB-06 | `admin_command_engine.md` | `02_admin_knowledge/` | Large (600–900 lines) | All 38 commands, syntax, RBAC guards, Bangla digit support, 30s dedup, examples per category | REPORT 13 |
| KB-07 | `scheduler_engine.md` | `06_developer_system/` | Medium (400–600 lines) | 14 jobs, cron schedule, env overrides, heartbeat, APScheduler integration | REPORT 20 |
| KB-08 | `rag_engine.md` | `06_developer_system/` | Medium (400–600 lines) | BM25 algorithm, bilingual tokenizer, 3-layer safety filter, stop words (with rationale), chunk config, audit ring buffer | REPORT 21 |
| KB-09 | `recruitment_engine.md` | `04_business_rules/` | Medium (400–500 lines) | 6-step funnel, 9 valid positions, scoring (HK-34), TTL, eligibility gates, state machine | REPORT 9 (WF-05), REPORT 14 |
| KB-10 | `bridge_engine.md` | `06_developer_system/` | Large (600–900 lines) | SQLite ingestion, LID resolution, dedup, cursors, OCR/voice branching, cooldown, adaptive poll interval | REPORT 9 (WF-01), REPORT 3 |
| KB-11 | `social_engine.md` | `06_developer_system/` | Medium (300–400 lines) | Facebook/Messenger auto-reply, reply content structure, salary response (aligned to salary_structure.txt per DUP-07) | REPORT 4 |
| KB-12 | `attendance_engine.md` | `05_workflows/` | Medium (300–400 lines) | Guard vs escort-style attendance, draft workflow, parser formats, save, backfill per day, summary | REPORT 9 (WF-04), REPORT 11 |
| KB-13 | `database_engine.md` | `06_developer_system/` | Large (600–900 lines) | All 30+ tables, key operations, idempotency patterns, phone variant lookup, audit tables | REPORT 16 |
| KB-14 | `rbac_engine.md` | `06_developer_system/` | Medium (400–600 lines) | 5 roles, permission table (38 commands), bootstrap admin, API key hash mechanism, audit trail | REPORT 14, REPORT 13 |

---

# DELIVERABLE 5 — Cross Reference Map

This map shows mandatory `Related Articles` links every article must carry.

## Core Reference Chains

```
EMPLOYEE KNOWLEDGE ARTICLES
├── salary_policy.md ──► salary_business_rules.md, salary_workflow.md, payroll_engine.md [KB-05]
├── transport_allowance.md ──► escort_business_rules.md, escort_engine.md [KB-03], CON-03 decision
├── recruitment_policy.md ──► recruitment_business_rules.md, recruitment_workflow.md, recruitment_engine.md [KB-09]
├── attendance_policy.md ──► attendance_business_rules.md, attendance_workflow.md, attendance_engine.md [KB-12]
├── release_slip.md ──► release_slip_workflow.md, escort_engine.md [KB-03], payment_engine.md [KB-04]
└── faq_employee.md ──► salary_policy.md, recruitment_policy.md, company_identity.md

ADMIN KNOWLEDGE ARTICLES
├── admin_command_engine.md [KB-06] ──► rbac_engine.md [KB-14], payment_engine.md [KB-04], escort_engine.md [KB-03]
├── admin_payment_handling.md ──► payment_engine.md [KB-04], cash_business_rules.md, payment_workflow.md
├── admin_attendance_handling.md ──► attendance_workflow.md, attendance_engine.md [KB-12]
└── admin_role_management.md ──► rbac_engine.md [KB-14], admin_command_engine.md [KB-06]

IDENTITY ARTICLES
├── identity_overview.md ──► identity_engine.md [KB-02], message_router_engine.md [KB-01]
├── employee_identity.md ──► identity_engine.md [KB-02], identity_overview.md
├── candidate_identity.md ──► recruitment_engine.md [KB-09], identity_overview.md
└── permission_matrix.md ──► rbac_engine.md [KB-14], admin_command_engine.md [KB-06]

BUSINESS RULE ARTICLES
├── escort_business_rules.md ──► escort_engine.md [KB-03], transport_allowance.md, food_cost_policy.md
├── payment_business_rules.md ──► payment_engine.md [KB-04], payroll_engine.md [KB-05]
├── payroll state references ──► payroll_engine.md [KB-05], payment_engine.md [KB-04]
└── recruitment_business_rules.md ──► recruitment_engine.md [KB-09], candidate_identity.md

WORKFLOW ARTICLES
├── escort_workflow.md ──► escort_engine.md [KB-03], escort_business_rules.md, release_slip_workflow.md
├── payment_workflow.md ──► payment_engine.md [KB-04], payment_business_rules.md, admin_payment_handling.md
├── release_slip_workflow.md ──► escort_engine.md [KB-03], ocr_engine.md, food_cost_policy.md
└── attendance_workflow.md ──► attendance_engine.md [KB-12], attendance_business_rules.md

DEVELOPER SYSTEM ARTICLES
├── message_router_engine.md [KB-01] ──► identity_engine.md [KB-02], bridge_engine.md [KB-10], admin_command_engine.md [KB-06]
├── identity_engine.md [KB-02] ──► message_router_engine.md [KB-01], database_engine.md [KB-13]
├── bridge_engine.md [KB-10] ──► message_router_engine.md [KB-01], rag_engine.md [KB-08]
├── rag_engine.md [KB-08] ──► database_engine.md [KB-13], scheduler_engine.md [KB-07]
├── scheduler_engine.md [KB-07] ──► payroll_engine.md [KB-05], rag_engine.md [KB-08]
├── database_engine.md [KB-13] ──► identity_engine.md [KB-02], payment_engine.md [KB-04]
└── security_rules.md ──► message_router_engine.md [KB-01], bridge_engine.md [KB-10]

AUTHORITY FOLDER (NEW)
├── 00_authority/management_decisions/ ──► All articles using management-approved values
├── 00_authority/conflict_resolution/ ──► escort_business_rules.md, payment_business_rules.md, transport_allowance.md
└── 00_authority/policy/ ──► All business rule articles
```

---

# DELIVERABLE 6 — Visibility Classification Report

Every article must carry exactly one visibility level from: PUBLIC / EMPLOYEE / SUPERVISOR / ADMIN / DEVELOPER / ARCHIVED.

## Complete Visibility Table

| Article (Proposed) | Folder | Visibility | Reason |
|---|---|---|---|
| `company_identity.md` | 01_employee | PUBLIC | Office info, safe for candidates and visitors |
| `recruitment_policy.md` | 01_employee | PUBLIC | Candidate-facing recruitment information |
| `faq_employee.md` | 01_employee | PUBLIC | FAQ for candidates and employees |
| `salary_policy.md` | 01_employee | EMPLOYEE | Salary info is employee-facing only |
| `attendance_policy.md` | 01_employee | EMPLOYEE | Employee-specific |
| `transport_allowance.md` | 01_employee | EMPLOYEE | Employee/escort-facing transport rates |
| `leave_policy.md` | 01_employee | EMPLOYEE | Employee-specific |
| `release_slip.md` | 01_employee | EMPLOYEE | Employee escort release process |
| `admin_operations_overview.md` | 02_admin | ADMIN | Admin-only operations |
| `admin_payment_handling.md` | 02_admin | ADMIN | Admin financial workflows |
| `admin_attendance_handling.md` | 02_admin | ADMIN | Admin attendance review |
| `admin_role_management.md` | 02_admin | ADMIN | Admin user management |
| `admin_command_engine.md` [KB-06] | 02_admin | ADMIN | Full command reference; operator-safe portion |
| `identity_overview.md` | 03_ai_identity | DEVELOPER | Full resolution algorithm is developer-only |
| `identity_engine.md` [KB-02] | 03_ai_identity / 06_dev | DEVELOPER | Internal routing logic |
| `permission_matrix.md` | 03_ai_identity | ADMIN | Role-level permissions are admin-safe |
| `candidate_identity.md` | 03_ai_identity | EMPLOYEE | Readable by employees managing recruitment |
| `employee_identity.md` | 03_ai_identity | SUPERVISOR | Supervisor-safe identity understanding |
| `accountant_identity.md` | 03_ai_identity | ADMIN | Accountant-specific routing rules |
| `response_rules.md` | 03_ai_identity | DEVELOPER | AI routing rules are developer-only |
| `vip_identity.md` | 03_ai_identity | ADMIN | VIP client handling |
| `family_identity.md` | 03_ai_identity | ADMIN | Family routing exception |
| `escort_identity.md` | 03_ai_identity | SUPERVISOR | Escort-client identity rules |
| `admin_identity.md` | 03_ai_identity | DEVELOPER | Bootstrap logic is developer-only |
| `escort_business_rules.md` | 04_business_rules | SUPERVISOR | Operations/supervisor-facing |
| `payment_business_rules.md` | 04_business_rules | ADMIN | Financial rules are admin/accountant |
| `recruitment_business_rules.md` | 04_business_rules | SUPERVISOR | HR-facing |
| `attendance_business_rules.md` | 04_business_rules | SUPERVISOR | Supervisor-facing |
| `salary_business_rules.md` | 04_business_rules | ADMIN | Internal salary structure |
| `cash_business_rules.md` | 04_business_rules | ADMIN | Cash ledger rules |
| `joining_business_rules.md` | 04_business_rules | ADMIN | Joining fee structure is internal |
| `ai_response_rules.md` | 04_business_rules | DEVELOPER | AI gate logic |
| `food_cost_policy.md` (NEW) | 04_business_rules | SUPERVISOR | Operations-level food cost rules |
| `recruitment_engine.md` [KB-09] | 04_business_rules | SUPERVISOR | HR scoring algorithm |
| `advance_payment_rules.md` (NEW) | 04_business_rules | ADMIN | Advance trigger keywords |
| `escort_workflow.md` | 05_workflows | SUPERVISOR | Supervisor/operations |
| `attendance_workflow.md` | 05_workflows | SUPERVISOR | Supervisor-facing |
| `payment_workflow.md` | 05_workflows | ADMIN | Admin/accountant |
| `release_slip_workflow.md` | 05_workflows | SUPERVISOR | Supervisor/operations |
| `recruitment_workflow.md` | 05_workflows | SUPERVISOR | HR-facing |
| `salary_workflow.md` | 05_workflows | ADMIN | Accountant-facing |
| `cash_workflow.md` | 05_workflows | ADMIN | Accountant-facing |
| `client_order_workflow.md` | 05_workflows | ADMIN | Admin/operations |
| `attendance_engine.md` [KB-12] | 05_workflows | SUPERVISOR | Supervisor understanding |
| `payroll_state_machine.md` (NEW) | 05_workflows | ADMIN | Accountant/admin reference |
| `escort_release_payment_workflow.md` (NEW) | 05_workflows | ADMIN | Admin/accountant |
| `accountant_payment_ingest.md` (NEW) | 05_workflows | ADMIN | Accountant-specific |
| `message_router_engine.md` [KB-01] | 06_dev | DEVELOPER | Core routing logic — internal |
| `bridge_engine.md` [KB-10] | 06_dev | DEVELOPER | Infrastructure — developer-only |
| `identity_brain.md` | 06_dev | DEVELOPER | AI identity implementation |
| `rag_engine.md` [KB-08] | 06_dev | DEVELOPER | BM25 implementation details |
| `scheduler_engine.md` [KB-07] | 06_dev | ADMIN | Job names/schedules are admin-safe; timing only |
| `database_engine.md` [KB-13] | 06_dev | DEVELOPER | Table internals — developer-only |
| `rbac_engine.md` [KB-14] | 06_dev | DEVELOPER | RBAC implementation |
| `social_engine.md` [KB-11] | 06_dev | DEVELOPER | Facebook/Messenger implementation |
| `security_rules.md` | 06_dev | DEVELOPER | Injection patterns — never expose |
| `ocr_engine.md` | 06_dev | DEVELOPER | OCR implementation |
| `parser_engine.md` | 06_dev | DEVELOPER | Parser regex — developer-only |
| `silent_skip_rules.md` (NEW) | 06_dev | DEVELOPER | Who gets silently skipped |
| `draft_always_gate.md` (NEW) | 06_dev | DEVELOPER | Who is always drafted |
| `complaint_protection_rules.md` (NEW) | 06_dev | DEVELOPER | Complaint phrase list — developer-only |
| `loop_flood_protection.md` (NEW) | 06_dev | DEVELOPER | Anti-spam thresholds — developer-only |
| `llm_provider_chain.md` (NEW) | 06_dev | DEVELOPER | GitHub→Groq→Ollama chain |
| `payment_engine.md` [KB-04] | 06_dev | DEVELOPER | Payment module internals |
| `payroll_engine.md` [KB-05] | 06_dev | DEVELOPER | Payroll module internals |
| `escort_engine.md` [KB-03] | 06_dev | DEVELOPER | Escort module internals |
| `voice_processing.md` (NEW) | 06_dev | DEVELOPER | Voice module — developer-only |
| `ai_safety_rules.md` (NEW) | 06_dev | DEVELOPER | AI safety mode logic |
| All `07_archived/` content | 07_archived | ARCHIVED | Historical traceability only |
| `00_authority/management_decisions/` | 00_authority | DEVELOPER | Governance authority |
| `00_authority/conflict_resolution/` | 00_authority | DEVELOPER | Conflict history |
| `00_authority/policy/` | 00_authority | ADMIN | Published policy authority |

---

# DELIVERABLE 7 — Folder Impact Report

## Folder Operations Required

### CREATE (New Folders)

| Folder | Purpose | Contents |
|---|---|---|
| `00_authority/` | Root authority folder — highest trust layer | Subfolders below |
| `00_authority/management_decisions/` | Approved management decisions | Conflict resolutions, hidden rule approvals |
| `00_authority/conflict_resolution/` | Conflict history and resolution record | CON-01 to CON-04, DUP decisions |
| `00_authority/policy/` | Published official policy | Approved business rules and procedures |
| `00_authority/certification/` | PKVC certification state | Current quality score, pending items |

### KEEP (Existing Folders — No Change to Structure)

| Folder | Status |
|---|---|
| `01_employee_knowledge/` | KEEP — enrich existing articles |
| `02_admin_knowledge/` | KEEP — add 1 new Knowledge Book |
| `03_ai_identity/` | KEEP — enrich existing articles |
| `04_business_rules/` | KEEP — add new articles, update conflicts |
| `05_workflows/` | KEEP — add new articles |
| `06_developer_system/` | KEEP — add 10+ new articles/books |
| `07_archived/` | KEEP — move legacy folders here |

### ARCHIVE (Move to 07_archived/)

| Source | Destination | Reason |
|---|---|---|
| `02_admin_system/` (entire folder) | `07_archived/legacy_admin_system/` | Superseded by `02_admin_knowledge/` during KB reorganization |
| `03_developer_system/` (entire folder) | `07_archived/legacy_developer_system/` | Superseded by `06_developer_system/` |

### PRESERVE (Root-Level Reports)

These root-level files should be moved to 00_authority/ or archived:

| File | Action |
|---|---|
| `knowledge_inventory.md` | MOVE to `00_authority/` and update content |
| `conflict_resolution_record.md` | MOVE to `00_authority/conflict_resolution/` |
| `duplicate_report.md` | MOVE to `00_authority/conflict_resolution/` |
| `gap_report.md` | MOVE to `00_authority/certification/` |
| `missing_report.md` | MOVE to `00_authority/certification/` |
| `enrichment_report.md` | MOVE to `00_authority/certification/` |
| `ai_access_matrix.md` | MOVE to `00_authority/policy/` |
| `README.md` | KEEP at root, update to reflect new structure |
| `production_knowledge_report.md` | ARCHIVE to `07_archived/` |
| `PKM_KE_KBG_Program_v2_Analysis_*.md` (3 files) | ARCHIVE to `07_archived/pkm_reports/` |
| `PKVC_Program_v1_Analysis_2026-06-21.md` | ARCHIVE to `07_archived/pkvc_reports_20260621/` |
| `latestaudit21062026.txt` | KEEP as source reference (do not move) |
| `KBTI_v1_PreExecution_Deliverables_20260621.md` | KEEP at root until after approval |

---

# DELIVERABLE 8 — New File Proposal

All 37 new files to be created, grouped by priority:

## Priority 1 — Critical Safety and Authority (Create First)

| File | Path | Visibility | Source |
|---|---|---|---|
| `silent_skip_rules.md` | `06_developer_system/` | DEVELOPER | HK-01, HK-02, message_router._should_silent_skip |
| `draft_always_gate.md` | `06_developer_system/` | DEVELOPER | HK-09, bridge_poller._is_draft_always |
| `complaint_protection_rules.md` | `06_developer_system/` | DEVELOPER | HK-10, HK-11, bridge_poller._COMPLAINT_PHRASES |
| `loop_flood_protection.md` | `06_developer_system/` | DEVELOPER | HK-13, HK-14, bridge_poller loop constants |
| `message_router_engine.md` | `06_developer_system/` | DEVELOPER | REPORT 9 WF-01, REPORT 3 HK-01 to HK-16 |

## Priority 2 — Authority Records (Create Second)

| File | Path | Visibility | Source |
|---|---|---|---|
| `management_decisions_register.md` | `00_authority/management_decisions/` | DEVELOPER | 21_management_decisions + conflict_resolution_record |
| `conflict_resolution_CON01_04.md` | `00_authority/conflict_resolution/` | DEVELOPER | CON-01 to CON-04 resolutions |
| `duplicate_governance_DUP03_04_06.md` | `00_authority/conflict_resolution/` | DEVELOPER | DUP-03, DUP-04, DUP-06 with developer evidence and proposed decisions |
| `food_cost_policy.md` | `04_business_rules/` | SUPERVISOR | CON-04: 150/day + time exceptions |

## Priority 3 — Admin Operations Reference (Create Third)

| File | Path | Visibility | Source |
|---|---|---|---|
| `admin_command_engine.md` | `02_admin_knowledge/` | ADMIN | REPORT 13 — all 38 commands |
| `rbac_engine.md` | `06_developer_system/` | DEVELOPER | REPORT 14 BR-01, rbac/__init__.py |
| `scheduler_engine.md` | `06_developer_system/` | ADMIN | REPORT 20 — 14 jobs |
| `payroll_state_machine.md` | `05_workflows/` | ADMIN | REPORT 10 SM-01, payroll.ALLOWED_TRANSITIONS |

## Priority 4 — Business Logic Reference (Create Fourth)

| File | Path | Visibility | Source |
|---|---|---|---|
| `llm_provider_chain.md` | `06_developer_system/` | DEVELOPER | REPORT 17 — GitHub→Groq→Ollama |
| `advance_payment_rules.md` | `04_business_rules/` | ADMIN | HK-04, HK-43, payment_workflow.ADVANCE_KEYWORDS |
| `recruitment_engine.md` | `04_business_rules/` | SUPERVISOR | REPORT 9 WF-05, HK-33, HK-34, HK-35, HK-36 |
| `ai_safety_rules.md` | `06_developer_system/` | DEVELOPER | REPORT 17 AI safety mode section |

## Priority 5 — Engine Knowledge Books (Create Fifth)

| File | Path | Visibility | Source |
|---|---|---|---|
| `identity_engine.md` | `06_developer_system/` | DEVELOPER | REPORT 15 — full identity resolution |
| `bridge_engine.md` | `06_developer_system/` | DEVELOPER | REPORT 9 WF-01, HK-16 to HK-18, HK-44, HK-45, HK-46 |
| `rag_engine.md` | `06_developer_system/` | DEVELOPER | REPORT 21, HK-28 to HK-32 |
| `escort_engine.md` | `06_developer_system/` | DEVELOPER | REPORT 9 WF-02, WF-03, REPORT 11 parsers |
| `payment_engine.md` | `06_developer_system/` | DEVELOPER | REPORT 9 WF-03, CON-01 formula |
| `payroll_engine.md` | `06_developer_system/` | DEVELOPER | REPORT 7 WF-07, SM-01, CON-02 |
| `database_engine.md` | `06_developer_system/` | DEVELOPER | REPORT 16 — all 30+ tables |

## Priority 6 — Workflow and Operational Articles (Create Sixth)

| File | Path | Visibility | Source |
|---|---|---|---|
| `escort_release_payment_workflow.md` | `05_workflows/` | ADMIN | REPORT 9 WF-03, CON-01, CON-04 |
| `accountant_payment_ingest.md` | `05_workflows/` | ADMIN | REPORT 4 (payment_ingest module), MIS-20 |
| `social_engine.md` | `06_developer_system/` | DEVELOPER | REPORT 4 social_auto_reply section, DUP-07 |
| `attendance_engine.md` | `05_workflows/` | SUPERVISOR | REPORT 9 WF-04, DUP-05 |
| `voice_processing.md` | `06_developer_system/` | DEVELOPER | REPORT 4 voice_processor, MIS-22 |

---

# DELIVERABLE 9 — File Rename Proposal

Files to be renamed for clarity and consistency:

| Current Name | Proposed Name | Folder | Reason |
|---|---|---|---|
| `hybrid_search.md` | `rag_strategy.md` | `06_developer_system/` | Name is ambiguous; content is RAG/BM25 strategy |
| `event_pipeline.md` | `outbound_pipeline.md` | `06_developer_system/` | Rename reflects content (outbound message pipeline) |
| `ai_system_prompt.md` | `system_prompt_rules.md` | `03_developer_system/` → ARCHIVE | (Archive instead — superseded by `06_developer_system/system_prompt.md`) |

---

# DELIVERABLE 10 — Merge Proposal

Articles with overlapping content that must be merged:

## Merge 1: Identity Brain Duplication

| Source A | Source B | Target | Strategy |
|---|---|---|---|
| `03_developer_system/identity_brain.md` | `06_developer_system/identity_brain.md` | `06_developer_system/identity_brain.md` | Merge all content into `06_developer_system/`; archive `03_developer_system/` version |

## Merge 2: OCR Pipeline / Engine Duplication

| Source A | Source B | Target | Strategy |
|---|---|---|---|
| `03_developer_system/ocr_pipeline.md` | `06_developer_system/ocr_engine.md` | `06_developer_system/ocr_engine.md` | Merge into `06_developer_system/`; archive `03_developer_system/` version |

## Merge 3: Security Rules Duplication

| Source A | Source B | Target | Strategy |
|---|---|---|---|
| `03_developer_system/security_rules.md` | `06_developer_system/security_rules.md` | `06_developer_system/security_rules.md` | Merge into `06_developer_system/`; archive `03_developer_system/` version |

## Merge 4: RAG Documentation Duplication

| Source A | Source B | Target | Strategy |
|---|---|---|---|
| `03_developer_system/rag_rules.md` | `06_developer_system/rag_strategy.md` | New `06_developer_system/rag_engine.md` | Both are partial; consolidate into new Knowledge Book |

## Merge 5: Workflow Engine Duplication

| Source A | Source B | Target | Strategy |
|---|---|---|---|
| `03_developer_system/workflow_engine.md` | `06_developer_system/workflow_engine.md` | `06_developer_system/workflow_engine.md` + new books | Extract content into specific Knowledge Books (KB-01, KB-03); archive both originals |

## Merge 6: Parser Documentation Duplication

| Source A | Source B | Target | Strategy |
|---|---|---|---|
| `03_developer_system/parser_logic.md` | `06_developer_system/parser_engine.md` | `06_developer_system/parser_engine.md` | Merge into `06_developer_system/`; archive `03_developer_system/` version |

## Merge 7: Admin Payroll Documentation

| Source A | Source B | Target | Strategy |
|---|---|---|---|
| `02_admin_system/payroll_rules.md` | (partial content in `04_business_rules/`) | New `payroll_engine.md` [KB-05] + `payroll_state_machine.md` | Extract full content into new articles; archive `02_admin_system/` version |

---

# DELIVERABLE 11 — Archive Proposal

Files and folders to be archived to `07_archived/`, preserving history:

## Folder Archives

| Source | Archive Destination | Reason |
|---|---|---|
| `02_admin_system/` (entire folder, 5 files) | `07_archived/legacy_admin_system/` | Pre-reorganization folder; all content migrated to `02_admin_knowledge/` and `05_workflows/` |
| `03_developer_system/` (entire folder, 6 files) | `07_archived/legacy_developer_system/` | Pre-reorganization folder; all content migrated to `06_developer_system/` |

## Root-Level File Archives

| Source | Archive Destination | Reason |
|---|---|---|
| `production_knowledge_report.md` | `07_archived/pkm_reports/` | Superseded by `latestaudit21062026.txt` as authoritative PKM source |
| `PKM_KE_KBG_Program_v2_Analysis_2026-06-21.md` | `07_archived/pkm_reports/` | Source report; not a KB article |
| `PKM_KE_KBG_Program_v2_Analysis_2026-06-21_BOARD_READY.md` | `07_archived/pkm_reports/` | Source report; not a KB article |
| `PKM_KE_KBG_Program_v2_Analysis_2026-06-21_DETAILED.md` | `07_archived/pkm_reports/` | Source report; not a KB article |
| `PKVC_Program_v1_Analysis_2026-06-21.md` | `07_archived/pkvc_reports_20260621/` | Already in correct location family; move to archive subfolder |
| `enrichment_report.md` | `07_archived/program_reports/` | Process report, not KB content |
| `duplicate_report.md` | `07_archived/program_reports/` | Superseded by `00_authority/conflict_resolution/` |

## Root-Level Files to Move to Authority (NOT Archive)

| Source | Destination | Reason |
|---|---|---|
| `conflict_resolution_record.md` | `00_authority/conflict_resolution/` | Authority document |
| `ai_access_matrix.md` | `00_authority/policy/` | Policy document |
| `knowledge_inventory.md` | `00_authority/` | Master inventory |
| `gap_report.md` | `00_authority/certification/` | Certification tracking |
| `missing_report.md` | `00_authority/certification/` | Certification tracking |

---

# DELIVERABLE 12 — Traceability Matrix

Every new and updated article must trace to production, PKM report, and management decision.

## Critical and High Priority Articles

| Article | Production Module | Function/Constant | PKM Report | PKVC Report | Management Decision |
|---|---|---|---|---|---|
| `silent_skip_rules.md` | `modules/message_router` | `_should_silent_skip()` | REPORT 3 HK-01, HK-02 | PKVC-17 (missing) | HK-01 approved |
| `draft_always_gate.md` | `modules/bridge_poller` | `_is_draft_always()` | REPORT 3 HK-09 | PKVC-17 (missing) | HK-09 approved |
| `complaint_protection_rules.md` | `modules/bridge_poller` | `_COMPLAINT_PHRASES`, `_ADVANCE_REQUEST_PHRASES` | REPORT 3 HK-10, HK-11 | PKVC-17 (missing) | N/A (developer scope) |
| `loop_flood_protection.md` | `modules/bridge_poller` | `_LOOP_*`, `_KW_FLOOD_*` | REPORT 3 HK-13, HK-14 | PKVC-17 (missing) | HK-13 approved |
| `transport_allowance.md` (UPDATE) | `modules/escort_lifecycle` | `_TRANSPORT_RATES` | REPORT 3 HK-19 | PKVC-16 CON-03 | CON-03: Mongla=800 approved |
| `payment_business_rules.md` (UPDATE) | `modules/payment_workflow` | `create_escort_payment_draft()` | REPORT 3 HK-25; REPORT 14 BR-08 | PKVC-16 CON-01 | CON-01: 12000/30×days approved |
| `payroll_state_machine.md` (NEW) | `modules/payroll` | `ALLOWED_TRANSITIONS` | REPORT 10 SM-01 | PKVC-13 | CON-02 approved |
| `food_cost_policy.md` (NEW) | `modules/escort_lifecycle` | `_calc_duty_days()`, hardcoded ৳150 | REPORT 3 HK-20 | PKVC-16 CON-04 | CON-04: 150/day+exceptions approved |
| `admin_command_engine.md` [KB-06] | `modules/admin_commands` | All 38 commands | REPORT 13 | PKVC-12 | N/A |
| `rbac_engine.md` [KB-14] | `modules/rbac` | `COMMAND_ROLE`, `ensure_bootstrap_admins()` | REPORT 3 HK-40 to HK-42 | PKVC-09 | HK-41 bootstrap approved |
| `scheduler_engine.md` [KB-07] | `modules/scheduler` | All 14 APScheduler jobs | REPORT 20 | PKVC-11 | N/A |
| `message_router_engine.md` [KB-01] | `modules/message_router` | 15-step routing priority | REPORT 9 WF-01 | PKVC-04 | HK-01 to HK-09 approved |
| `identity_engine.md` [KB-02] | `modules/identity_brain` | `_ROLE_PRIORITY`, 6 evidence sources | REPORT 15 | PKVC-09 | N/A |
| `bridge_engine.md` [KB-10] | `modules/bridge_poller` | `_fetch_new_messages()`, cursor logic | REPORT 9 WF-01 | PKVC-04 | HK-44, HK-45, HK-46 approved |
| `rag_engine.md` [KB-08] | `modules/rag` | BM25 index, safety filters | REPORT 21 | PKVC-08 | N/A |
| `escort_engine.md` [KB-03] | `modules/escort`, `escort_lifecycle` | Multiple parsers, close_program() | REPORT 9 WF-02, WF-03 | PKVC-04 | CON-03, CON-04 |
| `payment_engine.md` [KB-04] | `modules/payment_workflow` | `create_escort_payment_draft()` | REPORT 9 WF-03 | PKVC-05 | CON-01 |
| `payroll_engine.md` [KB-05] | `modules/payroll` | `compute_run()` | REPORT 9 WF-07 | PKVC-05 | CON-02 |
| `database_engine.md` [KB-13] | All modules | All 30+ tables | REPORT 16 | PKVC-07 | N/A |
| `llm_provider_chain.md` (NEW) | `config.py`, all modules | LLM provider chain config | REPORT 17 | PKVC-08 | N/A |
| `recruitment_engine.md` [KB-09] | `modules/recruitment_flow` | `_compute_score()`, `SESSION_TTL`, `VALID_POSITIONS` | REPORT 9 WF-05 | PKVC-04 | HK-33, HK-34, HK-35, HK-36 approved |
| `social_engine.md` [KB-11] | `modules/social_auto_reply/` | 12 files; reply_rules.py | REPORT 4 | N/A | DUP-07: salary_structure.txt is source of truth |
| `duplicate_governance_DUP03_04_06.md` | `phone_normalizer`, `number_identity`, `recruitment_flow`, draft tables | Multiple | REPORT 6 DUP-03, DUP-04, DUP-06 | PKVC-15 | PENDING — requires formal approval |

---

# DELIVERABLE 13 — Knowledge Quality Checklist

Every article created under KBTI must pass all applicable items before being marked complete.

## Standard Article Quality Gates

| Gate | Check | Applies To |
|---|---|---|
| QC-01 | Article has Purpose, Scope, Visibility, Target Roles, Source of Authority sections | All articles |
| QC-02 | Article traces to at least one production module or management decision | All articles |
| QC-03 | Article references PKM report ID or PKVC report ID | All articles |
| QC-04 | Visibility level is set and matches classification table | All articles |
| QC-05 | No confidential developer information appears in EMPLOYEE/SUPERVISOR/ADMIN articles | All articles |
| QC-06 | No invented knowledge — all facts trace to Layer 0, 1, or 2 | All articles |
| QC-07 | Conflict-affected values use management-approved numbers (CON-01 to CON-04) | Articles containing financial/transport/food values |
| QC-08 | Business rules include Validation Rules section | Business Rule articles |
| QC-09 | Workflow articles include State Machine or Decision Tree | Workflow articles |
| QC-10 | All Related Articles links are resolvable (target file exists) | All articles |
| QC-11 | Article has Revision History entry with today's date and KB program source | All articles |
| QC-12 | Examples are present and not hypothetical — drawn from real production behavior | All articles |
| QC-13 | No internal system markers (outbound poison tokens, injection patterns) appear in EMPLOYEE/PUBLIC articles | Security-adjacent articles |
| QC-14 | Knowledge Book articles contain Architecture, Business Rules, Workflow, Examples, Exceptions sections | Knowledge Book articles |
| QC-15 | FAQs are present for employee-facing and supervisor-facing articles | PUBLIC/EMPLOYEE/SUPERVISOR articles |
| QC-16 | Management decision reference is cited where approved values are used | Any article using CON-01 to CON-04 values |
| QC-17 | Pending decisions are explicitly flagged as "PENDING MANAGEMENT DECISION — not yet authoritative" | Any article referencing DUP-03, DUP-04, DUP-06 pending items |

## Special Gates for Conflict-Resolution Articles

| Gate | Check |
|---|---|
| QC-18 | Old/incorrect values are NOT present anywhere in the article |
| QC-19 | Management decision document is cited by reference |
| QC-20 | Effective date of the approved value is recorded |

## Knowledge Book Additional Gates

| Gate | Check |
|---|---|
| QC-21 | Knowledge Book has chapter-level table of contents |
| QC-22 | Every chapter is traceable to at least one production function |
| QC-23 | Security chapter explicitly states what must NOT be exposed to non-developers |

---

# ─────────────────────────────────────
# ⏸ STOP — AWAITING MANAGEMENT APPROVAL
# ─────────────────────────────────────

## What Has Been Done

All 13 KBTI pre-execution deliverables have been generated above.

**Zero knowledge base files have been created, modified, moved, or deleted.**

## What Requires Approval Before Execution

### Decision 1 — DUP-03: Phone Normalization Governance
> **Proposed:** Mark as intentional layered design (phone_normalizer = canonical, number_identity = identity variants). No consolidation.
> **Management must:** APPROVE / OVERRIDE

### Decision 2 — DUP-04: Recruitment Keyword Authority
> **Proposed:** Confirm recruitment_flow as single authority; router is a delegator. Document this design.
> **Management must:** APPROVE / OVERRIDE

### Decision 3 — DUP-06: Draft Table Architecture
> **Proposed:** Confirm split-by-purpose: fazle_draft_replies for conversational drafts; fazle_payment_drafts for financial/ledger drafts.
> **Management must:** APPROVE / OVERRIDE

### Decision 4 — Folder Archive Authorization
> **Proposed:** Archive 02_admin_system/ → 07_archived/legacy_admin_system/ and 03_developer_system/ → 07_archived/legacy_developer_system/
> **Management must:** APPROVE / OVERRIDE

### Decision 5 — Execution Phase Authorization
> **Proposed:** Execute KBTI in 6 phases as defined in REPORT 28 of the audit, with Priority 1 (critical safety articles) first.
> **Management must:** APPROVE / OVERRIDE / REPRIORITIZE

## On Approval, the Following Will Be Executed

1. Create `00_authority/` folder tree and move authority documents
2. Create 5 critical safety articles (Priority 1)
3. Create 4 authority records (Priority 2)
4. Create admin operations Knowledge Books (Priority 3)
5. Continue through Priority 4, 5, 6
6. Archive legacy folders
7. Enrich all 11 existing partial articles
8. Update all 4 conflict-affected articles with management-approved values
9. Execute 7 merge operations
10. Update README.md to reflect new structure

**Estimated KB quality score after completion: 85–90 / 100 (up from current 42 / 100)**
