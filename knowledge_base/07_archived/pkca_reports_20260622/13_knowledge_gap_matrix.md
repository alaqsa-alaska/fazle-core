---
title: PKCA Report 13: Knowledge Gap Matrix
owner: Fazle Core Admin
status: archived
last_verified: 2026-06-24
runtime_index: false
---

# PKCA Report 13: Knowledge Gap Matrix

**Date:** 2026-06-22
**Mode:** Read-Only Analysis

---

## Format

Each row = one gap unit of undocumented production knowledge.
- **Missing Knowledge** — what is not documented
- **Source Module** — where it comes from in production
- **Existing KB Article** — the best existing home for this knowledge
- **Proposed Destination** — file to enrich (no new file unless truly necessary)
- **Priority** — P1 (critical for ops), P2 (important for devs), P3 (nice to have)

---

## AI / LLM Behavior Gaps (GAP-AI-01 to GAP-AI-12)

| ID | Missing Knowledge | Source Module | Existing KB Article | Proposed Destination | Priority |
|---|---|---|---|---|---|
| GAP-AI-01 | Reply chain: GitHub Models→Groq→Ollama with exact models and rate limits | `app/llm.py` | `06_developer_system/automation_pipeline.md` | `automation_pipeline.md` § LLM Providers | P1 |
| GAP-AI-02 | Intent chain: Groq→GitHub→Ollama (DIFFERENT ORDER than reply) | `app/llm.py` | `06_developer_system/automation_pipeline.md` | `automation_pipeline.md` § Intent Classification | P1 |
| GAP-AI-03 | OLLAMA_REPLY_DISABLED flag — Ollama disabled for replies but still used for intent/RAG | `app/config.py` | `06_developer_system/developer_notes.md` | `developer_notes.md` § Config Flags | P2 |
| GAP-AI-04 | LLM fallback holding message text (Bangla) | `app/llm.py._FALLBACK_REPLY` | `04_business_rules/ai_response_rules.md` | `ai_response_rules.md` § Fallback | P1 |
| GAP-AI-05 | LLM learning memory — every reply saved to llm_learning_memory table | `app/llm.py` | `06_developer_system/automation_pipeline.md` | `automation_pipeline.md` § Learning | P2 |
| GAP-AI-06 | Memory extractor — fire-and-forget fact extraction per conversation | `modules/memory_extractor` | `06_developer_system/automation_pipeline.md` | `automation_pipeline.md` § Memory | P2 |
| GAP-AI-07 | Role classifier — Bangla system prompt injection per contact role | `modules/role_classifier` | `06_developer_system/identity_brain.md` | `identity_brain.md` § Role Prompts | P2 |
| GAP-AI-08 | Recruitment AI brain — deterministic fast-replies without LLM for fee/contact/age/office | `modules/recruitment_ai` | `04_business_rules/recruitment_business_rules.md` | `recruitment_business_rules.md` § AI Behavior | P1 |
| GAP-AI-09 | Reply templates rotation — _rotate() per-sender counter, template categories | `modules/reply_templates` | `06_developer_system/automation_pipeline.md` | `automation_pipeline.md` § Templates | P2 |
| GAP-AI-10 | Reviewed reply memory — admin-approved replies learned for future matching | `modules/reviewed_reply_memory` | `06_developer_system/workflow_engine.md` | `workflow_engine.md` § Reply Learning | P2 |
| GAP-AI-11 | AI safety gate — AI_SAFE_MODE, AUTO_REPLY_ENABLED, RECRUITMENT_AUTOREPLY_ENABLED flags | `app/config.py` | `06_developer_system/developer_notes.md` | `developer_notes.md` § Safety Gates | P1 |
| GAP-AI-12 | Automated reply suffix — full Bangla text appended to all auto-replies | `app/bridge.py._AUTOMATED_SUFFIX` | `04_business_rules/ai_response_rules.md` | `ai_response_rules.md` § Suffix | P1 |

---

## Silent-Skip and Draft Gate Gaps (GAP-GATE-01 to GAP-GATE-10)

| ID | Missing Knowledge | Source Module | Existing KB Article | Proposed Destination | Priority |
|---|---|---|---|---|---|
| GAP-GATE-01 | 11 silent-skip display-name tokens (al-aqsa, escort, client, etc.) | `message_router._should_silent_skip` | `04_business_rules/ai_response_rules.md` | `ai_response_rules.md` § Silent Skip | P1 |
| GAP-GATE-02 | role='blocked' hard silent-skip (no reply, no draft, no log) | `message_router` | `04_business_rules/ai_response_rules.md` | `ai_response_rules.md` § Silent Skip | P1 |
| GAP-GATE-03 | 9 safe auto-send intents (complete list) | `message_router._SAFE_AUTOSEND_INTENTS` | `04_business_rules/ai_response_rules.md` | `ai_response_rules.md` § Auto-Send | P1 |
| GAP-GATE-04 | advance_request excluded from auto-send despite looking like a safe intent | `message_router` comment | `04_business_rules/ai_response_rules.md` | `ai_response_rules.md` § Auto-Send | P1 |
| GAP-GATE-05 | Draft-always roles — accountant, client_escort_buyer, vip_client, repeat_client | `bridge_poller._is_draft_always` | `04_business_rules/ai_response_rules.md` | `ai_response_rules.md` § Draft Gate | P1 |
| GAP-GATE-06 | Complaint phrases that force draft (11 phrases) | `bridge_poller._COMPLAINT_PHRASES` | `04_business_rules/ai_response_rules.md` | `ai_response_rules.md` § Force Draft | P1 |
| GAP-GATE-07 | Advance request phrases that force draft (5 phrases) | `bridge_poller._ADVANCE_REQUEST_PHRASES` | `04_business_rules/payment_business_rules.md` | `payment_business_rules.md` § Advance | P1 |
| GAP-GATE-08 | Draft quality gate — 4 rejection criteria (empty, LLM fallback exact, BAD_PATTERNS, >4000 chars) | `modules/draft_quality` | `06_developer_system/automation_pipeline.md` | `automation_pipeline.md` § Quality Gate | P2 |
| GAP-GATE-09 | Outbound poison filter — 16 strings blocked from outbound | `bridge_poller._OUTBOUND_POISON` | `06_developer_system/security_rules.md` | `security_rules.md` § Poison Filter | P1 |
| GAP-GATE-10 | office_location bypass — skips reviewed-reply memory AND AI, goes KB-only | `message_router` step 12 | `04_business_rules/ai_response_rules.md` | `ai_response_rules.md` § Fast Paths | P2 |

---

## Loop and Security Gaps (GAP-SEC-01 to GAP-SEC-06)

| ID | Missing Knowledge | Source Module | Existing KB Article | Proposed Destination | Priority |
|---|---|---|---|---|---|
| GAP-SEC-01 | Loop detection: 3 replies/120s → pause 600s | `bridge_poller` | `06_developer_system/security_rules.md` | `security_rules.md` § Loop | P1 |
| GAP-SEC-02 | Keyword flood: same keyword >3 in 5 min → 15 min block | `bridge_poller` | `06_developer_system/security_rules.md` | `security_rules.md` § Flood | P1 |
| GAP-SEC-03 | Prompt injection: 18 patterns blocked + logged | `bridge_poller._PROMPT_INJECTION_PATTERNS` | `06_developer_system/security_rules.md` | `security_rules.md` § Prompt Injection | P1 |
| GAP-SEC-04 | Admin command dedup: SHA1(text+phone), 30s TTL, 256 entries | `admin_commands._dedup_seen` | `06_developer_system/security_rules.md` | `security_rules.md` § Command Dedup | P2 |
| GAP-SEC-05 | API keys stored as SHA-256 hash | `modules/rbac.hash_api_key` | `06_developer_system/security_rules.md` | `security_rules.md` § Auth | P2 |
| GAP-SEC-06 | Group/broadcast messages silently skipped at SQL level | `bridge_poller._fetch_new_messages` | `06_developer_system/security_rules.md` | `security_rules.md` § Filtering | P2 |

---

## Escort / Release Slip Gaps (GAP-ESC-01 to GAP-ESC-07)

| ID | Missing Knowledge | Source Module | Existing KB Article | Proposed Destination | Priority |
|---|---|---|---|---|---|
| GAP-ESC-01 | Transport rate table with exact BDT values per destination | `escort_lifecycle._TRANSPORT_RATES` | `04_business_rules/escort_business_rules.md` | `escort_business_rules.md` § Transport | P1 |
| GAP-ESC-02 | Food = 150 BDT/day; time exceptions (before 10AM no food, after 3PM no food) | `escort_lifecycle._calc_duty_days` | `04_business_rules/escort_business_rules.md` | `escort_business_rules.md` § Food | P1 |
| GAP-ESC-03 | Duty days >90 → SUSPICIOUS warning | `escort_lifecycle.build_release_draft` | `04_business_rules/escort_business_rules.md` | `escort_business_rules.md` § Validation | P2 |
| GAP-ESC-04 | Release date validation: future dates or >1 year old rejected | `escort_lifecycle._validate_release_date` | `05_workflows/release_slip_workflow.md` | `release_slip_workflow.md` § Validation | P2 |
| GAP-ESC-05 | EscortSlipResult TypedDict — all fields, REQUIRED_FIELDS list | `escort_slip_extractor` | `06_developer_system/ocr_engine.md` | `ocr_engine.md` § EscortSlipResult | P2 |
| GAP-ESC-06 | Document type detection: printed_template_slip vs handwritten_blank_slip | `escort_slip_extractor.detect_document_type` | `06_developer_system/ocr_engine.md` | `ocr_engine.md` § Document Types | P2 |
| GAP-ESC-07 | Release confirmation parser field list (6 extracted fields) | `escort_lifecycle.parse_release_confirmation` | `05_workflows/release_slip_workflow.md` | `release_slip_workflow.md` § Parser | P2 |

---

## Payroll Gaps (GAP-PAY-01 to GAP-PAY-05)

| ID | Missing Knowledge | Source Module | Existing KB Article | Proposed Destination | Priority |
|---|---|---|---|---|---|
| GAP-PAY-01 | Payroll state machine: 6 states, 5 transitions, ALLOWED_TRANSITIONS table | `modules/payroll.ALLOWED_TRANSITIONS` | `05_workflows/salary_workflow.md` | `salary_workflow.md` § State Machine | P1 |
| GAP-PAY-02 | Payroll idempotency: UNIQUE(employee_id, period_year, period_month) WHERE status != 'cancelled' | `modules/payroll.compute_run` | `06_developer_system/database_rules.md` | `database_rules.md` § Idempotency | P2 |
| GAP-PAY-03 | PAYROLL COMPUTE/SUBMIT/APPROVE/LOCK/PAID/CANCEL commands + required roles | `modules/admin_commands` | `02_admin_knowledge/admin_operations_overview.md` | `admin_operations_overview.md` § Payroll Cmds | P1 |
| GAP-PAY-04 | FPE workers: 5 background workers, their names, responsibilities | `modules/fazle_payroll_engine/workers.py` | None | NEW: `06_developer_system/fpe_overview.md` | P2 |
| GAP-PAY-05 | FPE processing state machine: pending→parsing→parsed→accounting→done/failed/skipped | `modules/fazle_payroll_engine/models.py` | None | NEW: `06_developer_system/fpe_overview.md` | P2 |

---

## Recruitment Gaps (GAP-REC-01 to GAP-REC-05)

| ID | Missing Knowledge | Source Module | Existing KB Article | Proposed Destination | Priority |
|---|---|---|---|---|---|
| GAP-REC-01 | **CONFLICT BR-25**: Age range — production says 18–55, KB says 18–45 | `modules/recruitment_flow._parse_age` | `04_business_rules/recruitment_business_rules.md` | Requires management decision before update | P1-CONFLICT |
| GAP-REC-02 | 9 valid recruitment positions (full list) | `modules/recruitment_flow.VALID_POSITIONS` | `04_business_rules/recruitment_business_rules.md` | `recruitment_business_rules.md` § Positions | P1 |
| GAP-REC-03 | Recruitment scoring algorithm (experience pts + position pts + completeness pts) | `modules/recruitment_flow._compute_score` | `04_business_rules/recruitment_business_rules.md` | `recruitment_business_rules.md` § Scoring | P1 |
| GAP-REC-04 | Session TTL: 24 hours | `modules/recruitment_flow.SESSION_TTL` | `04_business_rules/recruitment_business_rules.md` | `recruitment_business_rules.md` § Session | P1 |
| GAP-REC-05 | Deterministic fee/contact/office/age fast-reply behavior (no LLM used) | `modules/recruitment_ai` | `04_business_rules/recruitment_business_rules.md` | `recruitment_business_rules.md` § AI Brain | P1 |

---

## Database and Infrastructure Gaps (GAP-DB-01 to GAP-DB-08)

| ID | Missing Knowledge | Source Module | Existing KB Article | Proposed Destination | Priority |
|---|---|---|---|---|---|
| GAP-DB-01 | 43 production table names and their purposes | Multiple modules | `06_developer_system/database_rules.md` | `database_rules.md` § Table Inventory | P2 |
| GAP-DB-02 | Phone multi-variant lookup: 01X, 880X, +880X | `modules/phone_normalizer` | `06_developer_system/database_rules.md` | `database_rules.md` § Phone Lookup | P2 |
| GAP-DB-03 | Idempotency key patterns for payments, messages, outbound | Multiple | `06_developer_system/database_rules.md` | `database_rules.md` § Idempotency | P2 |
| GAP-DB-04 | Outbound queue state machine: pending→sending→sent/failed→dlq | `modules/outbound` | `06_developer_system/automation_pipeline.md` | `automation_pipeline.md` § Outbound Queue | P1 |
| GAP-DB-05 | DLQ behavior: max_attempts exceeded → dlq status; DLQ_ALERT every 15 min | `modules/outbound` | `06_developer_system/automation_pipeline.md` | `automation_pipeline.md` § DLQ | P1 |
| GAP-DB-06 | Circuit breaker: bridge goes OPEN after N failures, CLOSED on recovery | `app/bridge.py.CircuitBreaker` | `06_developer_system/automation_pipeline.md` | `automation_pipeline.md` § Circuit Breaker | P2 |
| GAP-DB-07 | Backup: 14 daily + 8 weekly rotation; SHA-256 hash verification | `modules/backup` | `06_developer_system/developer_notes.md` | `developer_notes.md` § Backup | P2 |
| GAP-DB-08 | Contact sync: canonical 8801XXXXXXXXXX; best-name=longest; ON CONFLICT DO UPDATE | `modules/contact_sync` | `06_developer_system/database_rules.md` | `database_rules.md` § Contact Sync | P2 |

---

## Scheduler Gaps (GAP-SCHED-01 to GAP-SCHED-03)

| ID | Missing Knowledge | Source Module | Existing KB Article | Proposed Destination | Priority |
|---|---|---|---|---|---|
| GAP-SCHED-01 | Complete 15-job schedule (all job names, cron times, purposes) | `modules/scheduler` | `06_developer_system/automation_pipeline.md` | `automation_pipeline.md` § Scheduler | P1 |
| GAP-SCHED-02 | Scheduler timezone: Asia/Dhaka; env override: SCHEDULER_TIMEZONE | `modules/scheduler` | `06_developer_system/automation_pipeline.md` | `automation_pipeline.md` § Scheduler | P2 |
| GAP-SCHED-03 | SCHEDULE STATUS and RUN JOB commands | `modules/admin_commands` | `02_admin_knowledge/admin_operations_overview.md` | `admin_operations_overview.md` § Scheduler Cmds | P2 |

---

## RAG Gaps (GAP-RAG-01 to GAP-RAG-06)

| ID | Missing Knowledge | Source Module | Existing KB Article | Proposed Destination | Priority |
|---|---|---|---|---|---|
| GAP-RAG-01 | BM25 params: k1=1.5, b=0.75 | `modules/rag` | `06_developer_system/rag_strategy.md` | `rag_strategy.md` § Algorithm | P2 |
| GAP-RAG-02 | Chunk size: 320 chars, overlap: 60 chars, min token: 2 chars | `modules/rag` | `06_developer_system/rag_strategy.md` | `rag_strategy.md` § Chunking | P2 |
| GAP-RAG-03 | Bilingual tokenizer regex: [A-Za-z0-9ঀ-৿]+ | `modules/rag._TOKEN_RE` | `06_developer_system/rag_strategy.md` | `rag_strategy.md` § Tokenizer | P2 |
| GAP-RAG-04 | 11 excluded directories, 11 excluded filename patterns | `modules/rag._EXCLUDED_*` | `06_developer_system/rag_strategy.md` | `rag_strategy.md` § Safety | P2 |
| GAP-RAG-05 | 30+ chunk-level safety patterns purged from index | `modules/rag._CHUNK_UNSAFE_PATTERNS` | `06_developer_system/security_rules.md` | `security_rules.md` § RAG Safety | P2 |
| GAP-RAG-06 | Index rebuilt daily at 18:00 by scheduler; sources: resources/*.txt + fazle_knowledge_base | `modules/rag` | `06_developer_system/rag_strategy.md` | `rag_strategy.md` § Index Rebuild | P2 |

---

## Admin Frontend Gaps (GAP-FE-01 to GAP-FE-03)

| ID | Missing Knowledge | Source Module | Existing KB Article | Proposed Destination | Priority |
|---|---|---|---|---|---|
| GAP-FE-01 | wa_chat_frontend: 25 REST endpoints + SSE stream | `modules/wa_chat_frontend` | None | `06_developer_system/developer_notes.md` § Frontend | P2 |
| GAP-FE-02 | SSE stream: /api/wa/stream; X-Internal-Key + ?key= auth | `modules/wa_chat_frontend` | None | `06_developer_system/developer_notes.md` § Frontend | P2 |
| GAP-FE-03 | Frontend capabilities: contact CRUD, block, draft approve/edit/reject, group messaging | `modules/wa_chat_frontend` | None | `06_developer_system/developer_notes.md` § Frontend | P2 |

---

## Gap Summary by Priority

| Priority | Gap Count | Domain |
|---|---|---|
| P1-CONFLICT | 1 | BR-25 age range conflict |
| P1 Critical | 29 | AI, gates, escort, payroll, recruitment, scheduler, outbound |
| P2 Important | 28 | DB, RAG, security, frontend, FPE |
| P3 Nice | 0 | N/A |

**Total documented gaps: 58**
**Percentage requiring new articles: 3 of 58 (FPE overview is the only truly new content)**
