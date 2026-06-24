---
title: PKCA Report 01: Production Knowledge Coverage Matrix
owner: Fazle Core Admin
status: archived
last_verified: 2026-06-24
runtime_index: false
---

# PKCA Report 01: Production Knowledge Coverage Matrix

**Program:** Production Knowledge Coverage Audit (PKCA) v1.0
**Date:** 2026-06-22
**Mode:** Read-Only Analysis — No files modified
**Auditor:** Enterprise Knowledge Auditor

---

## Coverage Legend

| Symbol | Meaning |
|---|---|
| ✅ | Fully documented (≥80%) |
| 🟡 | Partially documented (30–79%) |
| 🔴 | Minimally documented (5–29%) |
| ⛔ | Not documented (0–4%) |

---

## Production Module Coverage Matrix

| # | Production Module | Lines | KB Article(s) | Coverage % | Missing Topics | Confidence |
|---|---|---|---|---|---|---|
| 1 | `app/bridge.py` — BridgeClient, CircuitBreaker | ~150 | None | 0% | Bridge client architecture, circuit breaker states, automated suffix appended to outbound messages | HIGH |
| 2 | `app/config.py` — 50+ settings | ~120 | Scattered (app port only) | 5% | All 50+ env vars, provider selection, bridge paths, Meta webhook config, ollama_reply_disabled flag | HIGH |
| 3 | `app/critical_numbers.py` — Critical phone management | ~80 | None | 0% | Critical phone detection, critical log append, phone variant generation | HIGH |
| 4 | `app/database.py` — asyncpg pool | ~60 | database_rules.md (abstract) | 5% | Connection pooling, fetch_all/fetch_one/fetch_val/execute helpers, transaction pattern | MEDIUM |
| 5 | `app/github_models.py` — GitHub Models API | ~100 | None | 0% | Primary AI provider API, token rotation, request format, model selection | HIGH |
| 6 | `app/groq_provider.py` — Groq client | ~80 | None | 0% | Groq API client, 14400 req/day limit, 30 RPM limit, fallback usage | HIGH |
| 7 | `app/llm.py` — Unified LLM interface | ~150 | None | 5% | Reply chain (GitHub→Groq→Ollama), intent chain (Groq→GitHub→Ollama), fallback reply, LLM learning memory save | HIGH |
| 8 | `app/main.py` — FastAPI app | ~120 | None | 0% | Lifespan startup/shutdown, router registration, middleware, health endpoint | MEDIUM |
| 9 | `app/ollama.py` — Ollama client | ~80 | None | 0% | Ollama API wrapper, model name, reply-disabled flag behavior | HIGH |
| 10 | `modules/accountant_summary` — Accounting summary detector | ~130 | None | 0% | Bengali accounting summary format, company-level cash flow recognition, intentional no-write design | HIGH |
| 11 | `modules/admin_commands` — 38 WhatsApp commands (1297L) | 1297 | admin_operations_overview.md (5%) | 5% | All 38 command syntaxes, RBAC guards per command, Bangla digit support, 30s SHA1 dedup, command categories | HIGH |
| 12 | `modules/admin_employees` — Employee CRUD API (390L) | 390 | None | 0% | POST/PATCH/DELETE /api/admin/employees, soft-delete policy, FPE pre-seed, X-Internal-Key auth | HIGH |
| 13 | `modules/admin_transactions` — Transaction CRUD API (550L) | 550 | None | 0% | POST/PATCH/DELETE /api/admin/transactions, smart employee matching (4 rules A-D), fuzzy name match >0.95 | HIGH |
| 14 | `modules/attendance` — Guard attendance handler (246L) | 246 | attendance_workflow.md (40%) | 40% | Guard-only vs escort-style distinction, draft creation, save flow | MEDIUM |
| 15 | `modules/attendance_parser` — Escort-style attendance (280L) | 280 | attendance_workflow.md (20%) | 20% | Date patterns, shift extraction, mobile extraction, name label heuristic, structured parse output | HIGH |
| 16 | `modules/backup` — pg_dump backup with rotation (288L) | 288 | None (scheduler mention only) | 5% | docker exec pg_dump, rotation policy (14 daily + 8 weekly), SHA256 hash, fazle_db_backups table | HIGH |
| 17 | `modules/bridge_poller` — Core message ingestion (1467L) | 1467 | None | 5% | SQLite ingestion, LID resolution, dedup, cursors, adaptive poll 1–30s, OCR/voice branching, loop detection (3/120s→600s), flood protection (3/5m→15m), 18 prompt injection patterns, 16 outbound poison markers, silent-skip logic, draft-always gate, complaint/advance phrase gates, reply cooldown 60s | HIGH |
| 18 | `modules/contact_roles` — Contact role seeding | ~20 | None | 0% | Contact role seeding (minimal module) | LOW |
| 19 | `modules/contact_sync` — Unified contact sync (356L) | 356 | None | 0% | 3-source merge (bridge1, bridge2, Meta), canonical phone normalization, best-name algorithm, ON CONFLICT DO UPDATE, full+incremental sync | HIGH |
| 20 | `modules/conversation_layer` — Shadow-only (NOT production) | ~50 | N/A | N/A | Shadow/dev-only — intentionally excluded from router | N/A |
| 21 | `modules/draft_quality` — Draft quality gate (~100L) | ~100 | None | 0% | Draft quality checks, LLM fallback exact match, bad patterns, MAX_DRAFT_LEN=4000, DRAFT_QUALITY_GATE env | HIGH |
| 22 | `modules/employee_verification` — Multi-step verification (385L) | 385 | faq_employee.md (10%) | 10% | 5-step verification flow (selfie→slip→method), step names (STEP_SELFIE/SLIP/METHOD/DONE), session reuse, identity mismatch handling | HIGH |
| 23 | `modules/escort` — Escort order parser (1047L) | 1047 | escort_workflow.md (30%) | 30% | 4 parser formats (labeled/inline/MV-block/numbered), field extraction patterns, lighter block parser, completed draft detector | HIGH |
| 24 | `modules/escort_lifecycle` — Release/payment lifecycle (716L) | 716 | release_slip_workflow.md (25%) | 25% | Transport rates (approved CON-03 values), food policy (CON-04), duty day calculation, OCR confidence <40% warning, release date validation, suspicious >90 days, attendance backfill, payment draft formula CON-01 | HIGH |
| 25 | `modules/escort_roster` — Roster sync | ~80 | None | 5% | sync_program_to_roster, sync_all_programs, recalculate_entry, get_roster_summary | MEDIUM |
| 26 | `modules/escort_slip_extractor` — Advanced OCR (947L) | 947 | ocr_engine.md (5%) | 5% | Document type detection (printed/handwritten/mixed), label blacklist, signature detection, EscortSlipResult TypedDict, REQUIRED_FIELDS, FULL_FIELDS, template vs handwritten signals, extraction_id DB save | HIGH |
| 27 | `modules/fazle_payroll_engine` — FPE (15+ files, ~1500L) | ~1500 | None | 0% | Background workers (msg_processor, accounting, hsync, gap_scan, bridge_health), API routes /api/fpe/*, ParsedPayment model, MessageType enum, TxnCategory enum, ProcessingStatus enum, employee match (4-rule A-D), ledger management, historical sync, gap scan, reversals | HIGH |
| 28 | `modules/identity_brain` — 11 roles, 8 sources (393L) | 393 | identity_overview.md + identity_brain.md (35%) | 35% | Full role priority table (11 roles, priorities 200→0), 8 evidence source chain, confidence scoring per source, secondary evidence (cash/attendance/escort/contact) | HIGH |
| 29 | `modules/image_hash` — Image deduplication | ~60 | None | 0% | Image hash algorithm, dedup table | LOW |
| 30 | `modules/intent` — Intent classifier | ~80 | None | 10% | Deterministic keyword matching, intent list, LLM fallback trigger | HIGH |
| 31 | `modules/kb_upload` — KB upload API | ~40 | None | 0% | KB upload endpoint | LOW |
| 32 | `modules/knowledge_base` — DB + hardcoded lookup (309L) | 309 | rag_strategy.md (10%) | 10% | Hardcoded fallback templates (3 entries), DB fazle_knowledge_base lookup, priority (DB first, hardcoded fallback) | MEDIUM |
| 33 | `modules/media_normalization` — Meta media handler (~120L) | ~120 | None | 0% | normalize_meta_media_message, OCR candidate detection, voice processing, placeholder text, MIME type handling | HIGH |
| 34 | `modules/memory_extractor` — Continuous learning (~150L) | ~150 | None | 0% | LLM-powered fact extraction, user_profiles table, user_memory table, KB promotion flag, JSON parse with fence stripping | HIGH |
| 35 | `modules/message_archive` — Message archive (~120L) | ~120 | None | 0% | save_message with canonical_phone, phone_last10, message_hash, critical_contact flag, critical_log_path, UNIQUE INDEX on message_hash | HIGH |
| 36 | `modules/message_router` — 15-step routing (580L) | 580 | workflow_engine.md (10%) | 10% | 15-step routing priority, silent-skip (_should_silent_skip), safe-intents list, escort content detection, reviewed-reply memory path, office_location fast path, role-based branching | HIGH |
| 37 | `modules/number_identity` — Phone identity utilities | ~150 | None | 5% | normalize_phone→list of variants, canonical_phone, is_critical_phone, build_message_hash, append_critical_log, phone_last10 | HIGH |
| 38 | `modules/observability` — Prometheus metrics (~150L) | ~150 | None | 0% | inc/gauge/observe API, counter/histogram/gauge stores, Prometheus text-format /metrics endpoint, HTTP middleware integration | HIGH |
| 39 | `modules/ocr_processor` — OCR processing (556L) | 556 | ocr_engine.md (20%) | 20% | process_document, process_image, OCR criteria (JPG/JPEG/PNG/WEBP, 1KB-8MB), confidence scoring | HIGH |
| 40 | `modules/outbound` — Persistent queue with DLQ (255L) | 255 | None | 0% | enqueue with idempotency_key, sweep_once with exponential backoff, DLQ at max_attempts, circuit breaker integration, multi-channel send (bridge1/bridge2/meta/messenger/comment) | HIGH |
| 41 | `modules/payment` — Thin re-export | ~20 | payment_workflow.md (via re-export) | 25% | Re-export only; actual logic in payment_workflow | LOW |
| 42 | `modules/payment_correction` — DORMANT (300L) | 300 | None | 0% | DORMANT: 0 callers. reverse_payment, adjust_payment, list_corrections — implemented but never invoked | LOW |
| 43 | `modules/payment_ingest` — Payment SMS parser (433L) | 433 | cash_workflow.md (20%) | 20% | looks_like_payment_sms, is_admin_cash_shorthand, SMS parser patterns, staging table, reconciliation | HIGH |
| 44 | `modules/payment_workflow` — Escort payment + advance (396L) | 396 | payment_workflow.md (25%) | 25% | create_escort_payment_draft (CON-01 formula), ADVANCE_KEYWORDS (18 phrases), DEFAULT_DAILY_RATE, finalize_payment with idempotency_key | HIGH |
| 45 | `modules/payroll` — Monthly payroll (338L) | 338 | salary_workflow.md (20%) | 20% | compute_run (idempotent), ALLOWED_TRANSITIONS (6-state machine), DEFAULT_PER_PROGRAM_RATE, approval log | HIGH |
| 46 | `modules/payroll_logic` — Employee salary Q&A (~200L) | ~200 | None | 0% | get_payroll_summary, PayrollSummary TypedDict, under_review flag, duty_count_30d, active_duties | HIGH |
| 47 | `modules/phone_normalizer` — BD phone canonicalization | ~80 | None | 0% | normalize_phone→single canonical 8801XXXXXXXXX or None, operator prefix validation (11-19) | MEDIUM |
| 48 | `modules/rag` — BM25 RAG (484L) | 484 | rag_strategy.md (15%) | 15% | BM25 (k1=1.5, b=0.75), chunk 320 chars/60 overlap, 3-layer safety filter, 80+ stop words, ring buffer (50 entries), excluded dirs/filenames, chunk unsafe patterns (30+) | HIGH |
| 49 | `modules/rbac` — RBAC (341L) | 341 | permission_matrix.md (20%) | 20% | 5 roles (viewer/operator/accountant/admin/superadmin), COMMAND_ROLE mapping (38 commands), ensure_bootstrap_admins, SHA-256 API key hash, RBAC denial audit trail | HIGH |
| 50 | `modules/recruitment_ai` — Recruitment AI brain (~230L) | ~230 | None | 0% | Deterministic fact replies, fee question detection, contact question detection, looks_like_recruitment_followup, safe fallback | HIGH |
| 51 | `modules/recruitment_flow` — 6-step funnel (365L) | 365 | recruitment_workflow.md (30%) | 30% | 6-step funnel, VALID_POSITIONS (9 roles), SESSION_TTL=24h, _compute_score (experience+position+completeness), INTAKE_KEYWORDS | HIGH |
| 52 | `modules/reply_templates` — Rotating templates (~200L) | ~200 | None | 0% | _rotate() per-sender counter, 10+ intent templates, frustration variants, vendor/incident/emergency/followup templates | HIGH |
| 53 | `modules/reports` — Report builders (460L) | 460 | None (command mentions only) | 10% | _b_daily_summary, _b_payroll, _b_cash, _b_recon, _b_escort, _b_report_list, 10-min cache, fazle_report_cache table, fazle_report_runs table | HIGH |
| 54 | `modules/reviewed_reply_memory` — Admin-curated memory (365L) | 365 | None dedicated | 5% | normalize_lookup_context, _match_scope hierarchy (intent_role_phone→intent_role→intent), _eligible_draft_type, unsafe content guard, FEATURE_ENABLED flag | HIGH |
| 55 | `modules/role_classifier` — Per-contact role context (~100L) | ~100 | None | 0% | get_user_context, ROLE_PRIORITY dict, per-role _ROLE_PROMPTS (Bangla), user_profiles + user_memory tables | HIGH |
| 56 | `modules/scheduler` — 14 APScheduler jobs (690L) | 690 | None | 0% | All 14 jobs with schedules, timezone Asia/Dhaka, env overrides, heartbeat, APScheduler integration | HIGH |
| 57 | `modules/social_auto_reply` — 20 files, ~2000L | ~2000 | None | 0% | Facebook comments handler, Messenger handler, Meta WhatsApp webhook, AI-powered reply generator, risk flagger, rate limiter, send queue with retry, backlog processor, state tracker, conversation history, employee lookup, payment issue handler, salary flow, deduplication | HIGH |
| 58 | `modules/user_role` — User role detection (247L) | 247 | identity_overview.md (30%) | 30% | detect by phone lookup, normalize to 11-digit, UserRole TypedDict, admin/accountant multi-source check | MEDIUM |
| 59 | `modules/voice_processor` — Voice transcription | ~100 | None | 0% | Audio transcription (ptt/audio), media processor URL, voice to text output | HIGH |
| 60 | `modules/wa_chat_frontend` — WhatsApp Web UI (820L) | 820 | None | 0% | 25 REST endpoints, SSE real-time stream, contact management, draft approval UI, group messaging, auto-reply settings, X-Internal-Key auth, role-based settings | HIGH |
| 61 | `resources/message_processor.py` — Resource processor | ~60 | None | 0% | Resource-level message processing | LOW |
| 62 | `resources/recruitment.py` — Recruitment resource | ~40 | None | 0% | Recruitment resource helpers | LOW |

---

## Summary by Coverage Band

| Band | Count | % of Total Modules |
|---|---|---|
| ✅ Fully documented (≥80%) | 0 | 0% |
| 🟡 Partially documented (30–79%) | 6 | 10% |
| 🔴 Minimally documented (5–29%) | 17 | 28% |
| ⛔ Not documented (0–4%) | 38 | 62% |
| N/A (non-production / shadow) | 1 | — |

**Overall Production Knowledge Coverage: 14%**
