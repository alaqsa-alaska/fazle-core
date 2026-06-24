---
title: Wave-1 Knowledge Synchronization — Change Record
owner: Fazle Core Admin
status: archived
last_verified: 2026-06-24
runtime_index: false
---

# Wave-1 Knowledge Synchronization — Change Record

**Program:** Knowledge Synchronization Program (KSP) Wave-1
**Date:** 2026-06-22
**Mode:** Documentation only — No production files modified

---

## Files Updated (21 total)

### 06_developer_system/ (5 files)

| File | Change Type | Sections Added |
|---|---|---|
| `automation_pipeline.md` | Enriched | LLM Reply Chain, LLM Intent Chain (different order), Automated Reply Suffix, Outbound Queue State Machine, DLQ Behavior, Complete 15-Job Scheduler Table, Job Audit, Idempotency, Scheduler Admin Commands |
| `security_rules.md` | Enriched | Loop Detection, Keyword Flood Protection, Prompt Injection Protection, Outbound Poison Filter, Reply Cooldown (Redis + fallback), Group/Broadcast Skip, RAG Chunk Safety Filter, API Key SHA-256 Storage, Admin Command Deduplication |
| `identity_brain.md` | Enriched | 11-Step Resolution Algorithm (full table), Blocked Role Behavior, Confidence Scoring, 8 Evidence Sources Table |
| `developer_notes.md` | Enriched | AI Runtime Configuration Flags (9 flags), Bridge Port Configuration, Backup System (rotation + SHA-256 + table) |
| `role_permissions.md` | Enriched | RBAC 5-Level Hierarchy, Bootstrap Admin Creation, Role Gate Behaviors Matrix (all 12 roles) |

### 04_business_rules/ (4 files)

| File | Change Type | Sections Added |
|---|---|---|
| `ai_response_rules.md` | Enriched | Silent-Skip Rules (3 conditions + 11 tokens), Auto-Send Intent Gate (9 intents), Draft-Always Gate (4 roles), Force-Draft: Complaint Phrases (11), Force-Draft: Advance Phrases (5), office_location Fast Path, LLM Fallback Holding Message, Automated Reply Suffix |
| `escort_business_rules.md` | Enriched | Transport Rate Table (6 destinations + BDT values), Food Cost Rules (150/day + time exceptions), Duty Day Validation (suspicious >90), Release Date Validation, Payment Formula (CON-01/CON-02 confirmed) |
| `recruitment_business_rules.md` | Updated + Enriched | BR-25 age updated (18-45 → 18-55), 9 Valid Positions Table, Recruitment Scoring Algorithm (100-point scale), Session TTL (24h), Recruitment AI Deterministic Fast-Replies |
| `payment_business_rules.md` | Enriched | 18 Advance Trigger Keywords, 5 Force-Draft Short-Form Phrases, Payment Draft Lifecycle, Payment Draft State Machine |

### 05_workflows/ (6 files)

| File | Change Type | Sections Added |
|---|---|---|
| `salary_workflow.md` | Enriched | Payroll State Machine (6 states, 5 transitions, ALLOWED_TRANSITIONS), PAYROLL Admin Commands (7 commands), Daily Auto-Compute behavior |
| `escort_workflow.md` | Enriched | Escort Program State Machine (6 states), ESCORTCONFIRM Command Syntax, Escort Order Parser Formats (4 formats), Stale Program Alert (30-day rule) |
| `payment_workflow.md` | Enriched | Payment Draft State Machine, Employee Payment Verification (5-step), PAID/ADVANCE Commands, Payment Reconciliation (hourly job) |
| `attendance_workflow.md` | Enriched | Attendance Draft State Machine, Attendance Parser (date formats + shift + name heuristic), Duplicate Detection (ON CONFLICT UPDATE), APPROVE Command (multi-ID + Bangla digits) |
| `recruitment_workflow.md` | Enriched | Recruitment Session State Machine (7-step + scored/expired), Role-Based Recruitment Gate, Recruiting-Blocked Roles Table |
| `release_slip_workflow.md` | Enriched | Release Confirmation Parser (6 extracted fields), Release Date Validation Rules, Duty Day Calculation, OCR Slip Extraction (image requirements + 4 document types + 6 required fields) |

### 02_admin_knowledge/ (2 files)

| File | Change Type | Sections Added |
|---|---|---|
| `admin_operations_overview.md` | Enriched | Complete 37-Command Reference (8 groups: draft, payment, escort, payroll, reports, backup, user/RBAC, scheduler) |
| `admin_role_management.md` | Enriched | 5 USER Commands with syntax, Bootstrap Admin Creation (ADMIN_NUMBERS env), RBAC Role Level Summary |

### 03_ai_identity/ (2 files)

| File | Change Type | Sections Added |
|---|---|---|
| `identity_overview.md` | Enriched | 11-Step Resolution Algorithm (with table + evidence sources), Identity Confidence Scoring, Phone Normalization (3 variants + canonical format) |
| `permission_matrix.md` | Enriched | Production Role Gate Behaviors Table (all 12 roles, 4 gate behaviors), Safe Auto-Send Intent Gate (9 intents) |

### 01_employee_knowledge/ (1 file — BR-25 management decision)

| File | Change Type | Sections Updated |
|---|---|---|
| `recruitment_policy.md` | BR-25 fix | Age range: 18–45 → **18–55** per management decision |

---

## Evidence Used

| Knowledge Area | Source Module | Source Function | PKCA Report |
|---|---|---|---|
| LLM provider chains | `app/llm.py` | `generate_reply()` | 08 |
| Automated suffix | `app/bridge.py` | `_AUTOMATED_SUFFIX` | 08 |
| Outbound queue | `modules/outbound` | `enqueue()`, `sweep_once()` | 09 |
| All 15 scheduler jobs | `modules/scheduler/__init__.py` | `start_scheduler()` | 07 |
| Loop detection | `app/bridge_poller` | `_LOOP_*` constants | 10 (HK-13) |
| Keyword flood | `app/bridge_poller` | `_KW_FLOOD_*` | 10 (HK-14) |
| Prompt injection | `app/bridge_poller` | `_PROMPT_INJECTION_PATTERNS` | 10 (HK-15) |
| Outbound poison filter | `app/bridge_poller` | `_OUTBOUND_POISON` | 10 (HK-12) |
| Silent-skip tokens | `app/message_router` | `_should_silent_skip()` | 10 (HK-01) |
| Safe auto-send intents | `app/message_router` | `_SAFE_AUTOSEND_INTENTS` | 10 (HK-03) |
| Draft-always roles | `app/bridge_poller` | `_is_draft_always()` | 10 (HK-09) |
| Complaint phrases | `app/bridge_poller` | `_COMPLAINT_PHRASES` | 10 (HK-10) |
| Advance phrases | `app/bridge_poller` | `_ADVANCE_REQUEST_PHRASES` | 10 (HK-11) |
| Identity algorithm | `modules/identity_brain` | `resolve_identity()` | 11 |
| Blocked role | `app/message_router` | `_should_silent_skip()` | 10 (HK-02) |
| RBAC hierarchy | `modules/rbac` | `COMMAND_ROLE` | 12 (HK-40) |
| Bootstrap admin | `modules/rbac` | `ensure_bootstrap_admins()` | 10 (HK-41) |
| Transport rates | `modules/escort_lifecycle` | `_TRANSPORT_RATES` | 10 (HK-19) |
| Food calculation | `modules/escort_lifecycle` | `_calc_duty_days()` | 10 (HK-20) |
| Suspicious duty days | `modules/escort_lifecycle` | `build_release_draft()` | 10 (HK-23) |
| Release date validation | `modules/escort_lifecycle` | `_validate_release_date()` | 09 (SM-03) |
| Payroll state machine | `modules/payroll` | `ALLOWED_TRANSITIONS` | 09 (SM-01) |
| Payroll idempotency | `modules/payroll` | `compute_run()` | 10 (HK-27) |
| PAYROLL commands | `modules/admin_commands` | `_cmd_payroll_*()` | 12 |
| All 37 admin commands | `modules/admin_commands` | `dispatch_command()` | 12 |
| Recruitment age | `modules/recruitment_flow` | `_parse_age()` | 04 (BR-25) |
| Valid positions | `modules/recruitment_flow` | `VALID_POSITIONS` | 10 (HK-36) |
| Recruitment scoring | `modules/recruitment_flow` | `_compute_score()` | 10 (HK-34) |
| Session TTL | `modules/recruitment_flow` | `SESSION_TTL` | 10 (HK-33) |
| Recruitment AI | `modules/recruitment_ai` | `_looks_like_fee_question()` | 08 |
| Advance keywords | `modules/payment_workflow` | `ADVANCE_KEYWORDS` | 10 (HK-43) |
| Payment draft states | `modules/payment_workflow` | `create_escort_payment_draft()` | 09 (SM-03) |
| Employee verification | `modules/employee_verification` | `run_verification_step()` | 09 (SM-06) |
| Attendance state machine | `modules/attendance` | `save_attendance()` | 09 (SM-05) |
| Attendance parser | `modules/attendance_parser` | `_DATE_PATTERNS` | 05 |
| Escort state machine | `modules/escort_lifecycle` | Status transitions | 09 (SM-02) |
| ESCORTCONFIRM | `modules/admin_commands` | `_cmd_escort_confirm()` | 12 |
| OCR requirements | `modules/escort_slip_extractor` | `extract_slip()` | 05 |
| Document type detection | `modules/escort_slip_extractor` | `detect_document_type()` | 05 |
| Release parser fields | `modules/escort_lifecycle` | `parse_release_confirmation()` | 05 |
| Recruitment session SM | `modules/recruitment_flow` | `advance_session()` | 09 (SM-04) |
| Phone normalization | `modules/phone_normalizer` | `normalize_bd_phone()` | 11 |
| Reply cooldown | `app/bridge_poller` | `REPLY_COOLDOWN` | 10 (HK-44) |

---

## Management Decisions Applied

| Decision | Code | Applied To | Effect |
|---|---|---|---|
| Age range 18–55 (RESOLVED) | BR-25 | `recruitment_policy.md`, `recruitment_business_rules.md` | Changed 18–45 to 18–55 in all articles |
| Office address source of truth | DUP-01 | N/A (already applied) | Not re-applied in Wave-1 |
| Mongla transport rate ৳800 | HK-19 / CON-03 | `escort_business_rules.md` | Transport table documented with Mongla=800 |
| Food rate 150/day + time exceptions | CON-04 | `escort_business_rules.md` | Food rules documented |
| Payment formula 12000/30×days | CON-01/CON-02 | `escort_business_rules.md` | Formula documented |
| Silent-skip token list approved | HK-01 | `ai_response_rules.md` | Token list documented |
| Safe autosend intents approved | HK-03/04 | `ai_response_rules.md`, `permission_matrix.md` | Intent gate documented |
| Draft-always roles approved | HK-09 | `ai_response_rules.md`, `role_permissions.md` | Gate documented |
| Recruitment scoring approved | HK-34 | `recruitment_business_rules.md` | Scoring algorithm documented |
| Session TTL 24h approved | HK-33 | `recruitment_business_rules.md` | TTL documented |
| Reply cooldown 60s approved | HK-44 | `security_rules.md` | Cooldown documented |
| Loop protection approved | HK-13 | `security_rules.md` | Loop rules documented |

---

## Coverage Before Wave-1

| Dimension | Before |
|---|---|
| Overall production coverage | 14% |
| Workflow coverage | 21% |
| Business rule coverage | 27% |
| State machine coverage | 3.5% |
| Hidden rule coverage | 4% |
| AI behavior coverage | 1.7% |
| Scheduler coverage | 0% |
| Admin command coverage | 8% |
| Identity coverage | 8% |
| OCR coverage | 15% |
| Database coverage | <1% |

---

## Coverage After Wave-1 (Re-measured)

| Dimension | Before | After | Change |
|---|---|---|---|
| Overall production coverage | 14% | ~40% | +26 pp |
| Workflow coverage | 21% | ~65% | +44 pp |
| Business rule coverage | 27% | ~72% | +45 pp |
| State machine coverage | 3.5% | ~55% | +52 pp |
| Hidden rule coverage (HK-01–47) | 4% | ~52% | +48 pp |
| AI behavior coverage | 1.7% | ~60% | +58 pp |
| Scheduler coverage | 0% | ~90% | +90 pp |
| Admin command coverage | 8% | ~75% | +67 pp |
| Identity coverage | 8% | ~70% | +62 pp |
| OCR/Release slip coverage | 15% | ~55% | +40 pp |
| Database coverage | <1% | ~3% | +2 pp (not targeted in P1) |
| Recruitment coverage | 44% | ~85% | +41 pp |
| Attendance coverage | 40% | ~75% | +35 pp |
| Payment coverage | 25% | ~65% | +40 pp |
| Escort coverage | 21% | ~65% | +44 pp |

---

## Remaining P1 Gaps After Wave-1

The following P1 gaps were NOT closed in Wave-1:

| Gap | Module | Reason Not Closed |
|---|---|---|
| Fazle Payroll Engine (FPE) — 5 workers | `modules/fazle_payroll_engine` | Requires new article `fpe_overview.md` — Wave-2 scope |
| Social auto-reply system (20 files) | `modules/social_auto_reply` | Requires new article `social_auto_reply_system.md` — Wave-2 scope |
| wa_chat_frontend 25 endpoints | `modules/wa_chat_frontend` | P2 per enrichment plan; developer_notes has brief mention only |
| Contact sync behavior | `modules/contact_sync` | P2 — not targeted in Wave-1 |
| Memory extractor fire-and-forget | `modules/memory_extractor` | P2 — not targeted in Wave-1 |
| Database table inventory (43 tables) | Multiple | P2 — database_rules.md enrichment not in Wave-1 scope |
| Role classifier / Bangla prompt injection | `modules/role_classifier` | P2 — not targeted in Wave-1 |
| Reviewed reply memory exclusions | `modules/reviewed_reply_memory` | P2 — not targeted in Wave-1 |
| RAG technical parameters | `modules/rag` | P2 — rag_strategy.md enrichment not in Wave-1 scope |
| Payment correction (DORMANT) | `modules/payment_correction` | DORMANT — zero callers; low priority |

---

## Recommended Wave-2 Scope

### New Articles (create these)

| New Article | Priority | Content |
|---|---|---|
| `06_developer_system/fpe_overview.md` | P2 | FPE 5 workers, processing state machine, 4 tables, API routes, FPE vs core payroll distinction |
| `06_developer_system/social_auto_reply_system.md` | P2 | 20-file architecture, Facebook/Messenger/Meta WhatsApp, reply rules, rate limiter, risk flagger |

### Existing Articles to Enrich (P2 items)

| Article | What to Add |
|---|---|
| `06_developer_system/database_rules.md` | 43 table inventory, phone lookup variants, idempotency patterns, advisory locks, soft-delete |
| `06_developer_system/rag_strategy.md` | BM25 params (k1=1.5, b=0.75), chunk size (320/60), bilingual tokenizer, 11 excluded dirs, 11 excluded filename patterns |
| `06_developer_system/automation_pipeline.md` | Draft quality gate (4 criteria), reply templates rotation, memory extractor details, circuit breaker |
| `06_developer_system/identity_brain.md` | Bangla system prompt injection per role (role_classifier) |
| `06_developer_system/developer_notes.md` | wa_chat_frontend 25 endpoints + SSE stream, observability /metrics, contact sync canonical format |
| `06_developer_system/ocr_engine.md` | Full EscortSlipResult TypedDict (18 fields), label blacklist (35+ strings), signature detection |
| `06_developer_system/security_rules.md` | Exact prompt injection patterns list |

---

## Verification

**No production files modified.** Verified by checking that all edits are in `/home/azim/core/knowledge_base/` only.

**No KB articles deleted or renamed.** Only enrichments added to existing content.

**All enrichments traceable to production source.** Every new section includes Source Module, Source Function, and PKCA Report reference.

**Management decisions applied correctly:**
- BR-25: Age updated to 18-55 in both employee-facing (recruitment_policy.md) and admin/developer-facing (recruitment_business_rules.md) articles.
- All HK decisions approved in PKVC Management Decisions have been documented.
