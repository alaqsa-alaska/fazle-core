---
title: PKMA Report 06 — Traceability Maturity
owner: Fazle Core Admin
status: archived
last_verified: 2026-06-24
runtime_index: false
---

# PKMA Report 06 — Traceability Maturity

**Program:** PKMA v1.0
**Date:** 2026-06-22
**Mode:** READ-ONLY

---

## Purpose

Assess the completeness of the traceability chain: KB Article → Production Module → Function → Database Table. Without traceability, knowledge cannot be verified, updated correctly, or certified.

---

## Traceability Model

Full traceability requires 4 links per knowledge unit:
1. **KB Article** — which file contains the knowledge
2. **Production Module** — which Python module implements it
3. **Source Function** — which function is the authoritative implementation
4. **Database Table** — which table persists the state (if any)

Partial traceability = 2–3 links present
Zero traceability = knowledge exists but origin is unknown

---

## Full Traceability Map (Post-Wave-1)

### Level 3 Domains — Full Traceability (Wave-1 established this)

| Knowledge Unit | KB Article | Module | Function | Table |
|---|---|---|---|---|
| Payroll state machine | salary_workflow.md | modules/payroll | ALLOWED_TRANSITIONS | wbom_payroll_approval_log |
| Payroll idempotency | salary_workflow.md | modules/payroll | compute_run() | wbom_payroll_approval_log (UNIQUE) |
| Escort state machine | escort_workflow.md | modules/escort_lifecycle | status transitions | escort_program_orders |
| Transport rates | escort_business_rules.md | modules/escort_lifecycle | _TRANSPORT_RATES | — |
| Food calculation | escort_business_rules.md | modules/escort_lifecycle | _calc_duty_days() | — |
| Suspicious duty days | escort_business_rules.md | modules/escort_lifecycle | build_release_draft() | — |
| Release date validation | release_slip_workflow.md | modules/escort_lifecycle | _validate_release_date() | — |
| Attendance state machine | attendance_workflow.md | modules/attendance | save_attendance() | wbom_attendance (ON CONFLICT UPDATE) |
| Attendance parser | attendance_workflow.md | modules/attendance_parser | _DATE_PATTERNS | — |
| Payment draft state | payment_workflow.md | modules/payment_workflow | create_escort_payment_draft() | wbom_payment_drafts |
| Employee verification | payment_workflow.md | modules/employee_verification | run_verification_step() | — |
| Recruitment session SM | recruitment_workflow.md | modules/recruitment_flow | advance_session() | fazle_recruitment_sessions |
| Recruitment age | recruitment_business_rules.md | modules/recruitment_flow | _parse_age() | — |
| Recruitment scoring | recruitment_business_rules.md | modules/recruitment_flow | _compute_score() | fazle_recruitment_sessions |
| Session TTL | recruitment_business_rules.md | modules/recruitment_flow | SESSION_TTL | fazle_recruitment_sessions |
| Silent-skip tokens | ai_response_rules.md | app/message_router | _should_silent_skip() | — |
| Draft-always roles | ai_response_rules.md | app/bridge_poller | _is_draft_always() | — |
| Safe auto-send intents | ai_response_rules.md | app/message_router | _SAFE_AUTOSEND_INTENTS | — |
| Complaint phrases | ai_response_rules.md | app/bridge_poller | _COMPLAINT_PHRASES | — |
| Advance request phrases | ai_response_rules.md | app/bridge_poller | _ADVANCE_REQUEST_PHRASES | — |
| office_location fast path | ai_response_rules.md | app/message_router | (HK-47 fast path) | — |
| Loop detection | security_rules.md | app/bridge_poller | _LOOP_* constants | — |
| Keyword flood protection | security_rules.md | app/bridge_poller | _KW_FLOOD_* | — |
| Prompt injection | security_rules.md | app/bridge_poller | _PROMPT_INJECTION_PATTERNS | outbound_safety_incidents |
| Outbound poison filter | security_rules.md | app/bridge_poller | _OUTBOUND_POISON | — |
| Reply cooldown | security_rules.md | app/bridge_poller | REPLY_COOLDOWN | Redis |
| Admin command dedup | security_rules.md | app/bridge_poller | SHA1(text+phone) | (LRU cache 256 entries) |
| LLM reply chain | automation_pipeline.md | app/llm.py | generate_reply() | — |
| Automated suffix | automation_pipeline.md | app/bridge.py | _AUTOMATED_SUFFIX | — |
| All 15 scheduler jobs | automation_pipeline.md | modules/scheduler/__init__.py | start_scheduler() | — |
| All 37 admin commands | admin_operations_overview.md | modules/admin_commands | dispatch_command() | Multiple |
| Identity resolution | identity_overview.md | modules/identity_brain | resolve_identity() | fazle_contact_roles, fazle_admins |
| Phone normalization | identity_overview.md | modules/phone_normalizer | normalize_bd_phone() | — |
| RBAC hierarchy | role_permissions.md | modules/rbac | COMMAND_ROLE | fazle_admins |
| Bootstrap admin | admin_role_management.md | modules/rbac | ensure_bootstrap_admins() | fazle_admins |
| Release confirmation parser | release_slip_workflow.md | modules/escort_lifecycle | parse_release_confirmation() | — |
| OCR slip extraction | release_slip_workflow.md | modules/escort_slip_extractor | extract_slip() | — |
| FPE workers | NOT IN KB | modules/fazle_payroll_engine/workers.py | start_workers() | Multiple |
| BM25 RAG params | NOT IN KB | modules/rag | (RAG engine init) | — |
| 43 database tables | NOT IN KB | Multiple | Multiple | Multiple |

---

## Traceability Coverage Matrix

| Domain | Article Link | Module Link | Function Link | Table Link | Traceability Score |
|---|---|---|---|---|---|
| Attendance | Yes | Yes | Yes | Yes | 100% |
| Escort | Yes | Yes | Yes | Partial | 90% |
| Escort Payment | Yes | Yes | Yes | Yes | 100% |
| Payroll | Yes | Yes | Yes | Yes | 100% |
| Recruitment | Yes | Yes | Yes | Yes | 100% |
| Identity Brain | Yes | Yes | Yes | Yes | 100% |
| AI Behavior | Yes | Yes | Yes | No | 75% |
| Security Rules | Yes | Yes | Yes | Partial | 90% |
| Message Router | Yes | Yes | Yes | No | 75% |
| Admin Commands | Yes | Yes | Yes | Partial | 85% |
| RBAC | Yes | Yes | Yes | Yes | 100% |
| Scheduler | Yes | Yes | Yes | No | 75% |
| Outbound/Notification | Yes (inline) | Yes | Yes | No | 70% |
| Release Slip | Yes | Yes | Yes | No | 75% |
| Automation Pipeline | Yes | Yes | Yes | No | 75% |
| RAG | No | No | No | No | 0% |
| OCR Engine | Partial | Partial | Partial | No | 30% |
| Parser Engine | Partial | Partial | No | No | 25% |
| Cash / FPE | Partial | No | No | No | 10% |
| Database Behavior | No | No | No | No | 0% |
| Social Auto Reply | No | No | No | No | 0% |
| WhatsApp Bridge | Partial | Partial | No | No | 20% |
| State Machines | Yes | Yes | Yes | Partial | 85% |
| Developer System | Yes (5/7) | Yes | Yes | Partial | 75% |
| Business Rules | Yes | Yes | Yes | Partial | 90% |

---

## Traceability Score Summary

| Tier | Domains | Average Traceability |
|---|---|---|
| Level 3 domains | 10 | 93% |
| Level 2 domains | 11 | 72% |
| Level 1 domains | 5 | 13% |
| Level 0 domains | 3 | 0% |
| **Overall** | **29** | **63%** |

---

## Critical Traceability Gaps

### Gap 1 — FPE Workers (CRITICAL)

- **Knowledge Unit:** 5 production workers (message_processor_worker, accounting_worker, historical_sync_loop, gap_scan_loop, bridge_health_loop)
- **Module:** `modules/fazle_payroll_engine/workers.py`
- **Traceability:** Production verified (workers read in session) but NOT in any KB article
- **Risk:** If someone reads the KB to understand the system, FPE workers are invisible. A developer could duplicate functionality or break workers without knowing the risk.

### Gap 2 — BM25 RAG Parameters (HIGH)

- **Knowledge Unit:** k1=1.5, b=0.75, chunk 320/60, bilingual tokenizer, 11 excluded dirs
- **Module:** `modules/rag`
- **Traceability:** Known from PKCA analysis but NOT documented in KB
- **Risk:** Future KB restructuring could break RAG relevance without knowing the chunk/tokenizer constraints.

### Gap 3 — Database Schema (HIGH)

- **Knowledge Unit:** 43 tables with their columns, relationships, and behaviors
- **Module:** Multiple
- **Traceability:** 0% — database_rules.md is entirely abstract
- **Risk:** Schema changes cannot be validated against KB; new developers have no schema reference.

### Gap 4 — Social Auto Reply Behavior (HIGH)

- **Knowledge Unit:** 20-file system behavior, reply rules, rate limiter, risk flagger
- **Module:** `modules/social_auto_reply`
- **Traceability:** 0%
- **Risk:** Changes to social auto reply have no KB reference; behavior cannot be audited.

---

## Traceability Quality Score

| Criterion | Score | Notes |
|---|---|---|
| Wave-1 articles have module links | 9/10 | All Wave-1 additions include Source Module |
| Wave-1 articles have function links | 9/10 | All Wave-1 additions include Source Function |
| Wave-1 articles have PKCA cross-references | 9/10 | All reference PKCA report number |
| Table links present | 6/10 | Not all articles list affected tables |
| Undocumented production behaviors | 4/10 | FPE, social auto reply, DB schema missing |
| **Overall Traceability Quality** | **7.4/10** | — |

---

*PKMA v1.0 | READ-ONLY | 2026-06-22*
