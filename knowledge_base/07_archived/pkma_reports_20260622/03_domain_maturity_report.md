---
title: PKMA Report 03 — Domain Maturity Report
owner: Fazle Core Admin
status: archived
last_verified: 2026-06-24
runtime_index: false
---

# PKMA Report 03 — Domain Maturity Report

**Program:** PKMA v1.0
**Date:** 2026-06-22
**Mode:** READ-ONLY

---

## Purpose

Per-domain maturity assessment with detailed evidence, rationale, and advancement path for all 30 domains.

---

## Domain 01 — Attendance

**Maturity Level: 3 (Management Approved)**

| Dimension | Status |
|---|---|
| KB Article | `05_workflows/attendance_workflow.md` |
| Production Source | `modules/attendance`, `modules/attendance_parser` |
| State Machine | 4-state: pending → approved / rejected / expired |
| Parser | Date formats (DD-MM-YYYY, YYYY-MM-DD), shift D/N, mobile, name heuristic |
| Admin Commands | APPROVE (multi-ID, Bangla digits, dedup HK-38/39) |
| Management Decision | DUP-05 approved (attendance vs attendance_parser separation) |
| Unresolved Conflicts | None |
| Missing | Duplicate detection edge cases; ON CONFLICT UPDATE fine print |

**Evidence:** Wave-1 read `modules/attendance` before documenting. State machine, parser, APPROVE command all verified active.
**Advancement Path:** Wave-2 enrichment of edge cases → PKVC certification → Level 4.

---

## Domain 02 — Escort

**Maturity Level: 3 (Management Approved)**

| Dimension | Status |
|---|---|
| KB Article | `05_workflows/escort_workflow.md`, `04_business_rules/escort_business_rules.md` |
| Production Source | `modules/escort_lifecycle` |
| State Machine | 6-state: draft → confirmed → Assigned → Running → Completed / Cancelled |
| Parser | 4 formats: labeled block, inline compact, MV-block, numbered |
| Admin Commands | ESCORTCONFIRM with syntax verified |
| Stale Alert | 30-day rule, ESCORT_STALE_DAYS, scheduler reminder |
| Management Decision | CON-01 (formula), CON-02 (formula), CON-03 (Mongla 800), CON-04 (food 150/day + time exceptions) |
| Unresolved Conflicts | None |
| Missing | Escort order parser regex patterns not in KB |

**Advancement Path:** Document parser regex patterns in Wave-2 → PKVC certification → Level 4.

---

## Domain 03 — Escort Payment

**Maturity Level: 3 (Management Approved)**

| Dimension | Status |
|---|---|
| KB Articles | `05_workflows/payment_workflow.md`, `04_business_rules/escort_business_rules.md` |
| Payment Formula | 12000/30 × duty_days = daily salary component |
| Transport Table | 6 destinations; Mongla=800 BDT (CON-03 confirmed) |
| Food Calculation | 150/day with time exceptions (before 10AM no food on release; after 3PM no food on boarding) |
| Payment Draft | 4-state: pending → sent / rejected / expired (24h TTL) |
| Management Decision | CON-01, CON-02, CON-03, CON-04 |
| Unresolved Conflicts | None |
| Missing | Correction module (DORMANT — zero callers) not documented |

**Advancement Path:** Verify payment_correction module is truly dormant → PKVC certification → Level 4.

---

## Domain 04 — Release Slip / OCR

**Maturity Level: 2 (Production Verified)**

| Dimension | Status |
|---|---|
| KB Article | `05_workflows/release_slip_workflow.md` |
| Parser Fields | 6 regex fields: _RC_DATE_RE, _RC_SHIFT_RE, _RC_POINT_RE, _RC_DAYS_RE, _RC_CONV_RE, _RC_FOOD_RE |
| OCR Requirements | Image types JPG/JPEG/PNG/WEBP; size 1KB–8MB; 4 document types |
| EscortSlipResult | 6 required fields documented; 12 additional fields NOT in KB |
| Validation | Release date (no future, no >1yr past); duty days >90 = SUSPICIOUS |
| Management Decision | None for release slip as separate domain (covered by escort decisions) |
| Missing | Full 18-field EscortSlipResult TypedDict; label blacklist (35+ strings); signature detection |

**Gap to Level 3:** No management decision specifically for OCR format or slip validation rules.
**Advancement Path:** Document full TypedDict in ocr_engine.md → management confirmation → PKVC cert → Level 4.

---

## Domain 05 — Payroll

**Maturity Level: 3 (Management Approved)**

| Dimension | Status |
|---|---|
| KB Article | `05_workflows/salary_workflow.md` |
| State Machine | 6-state: draft → reviewed → approved → locked → paid; cancelled from any non-paid |
| ALLOWED_TRANSITIONS | All 5 transitions documented with function names |
| Audit | wbom_payroll_approval_log table; UNIQUE constraint |
| Admin Commands | 7 PAYROLL commands: START, REVIEW, APPROVE, LOCK, MARK-PAID, CANCEL, STATUS |
| Auto-Compute | Daily job at 02:00, daily_payroll_compute in scheduler |
| Management Decision | CON-01 (formula 12000/30×days) |
| Missing | Bulk payroll scenarios; override for partial month; correction after lock |

**Advancement Path:** Document edge cases → PKVC certification → Level 4.

---

## Domain 06 — Cash / Fazle Payroll Engine (FPE)

**Maturity Level: 1 (Documented)**

| Dimension | Status |
|---|---|
| KB Article | payment_business_rules.md (partial); **no fpe_overview.md exists** |
| FPE Workers | 5 workers verified ACTIVE in production (`modules/fazle_payroll_engine/workers.py`) |
| Worker Names | message_processor_worker, accounting_worker, historical_sync_loop, gap_scan_loop, bridge_health_loop |
| Worker Config | POLL_INTERVAL=3s, BATCH_SIZE=20, MAX_ATTEMPTS=5 (verified but NOT in KB) |
| Cash Transactions | `wbom_cash_transactions` — payment history; used by identity engine |
| KB Coverage | FPE worker behavior: 0% documented in KB |
| Management Decision | None for FPE specifically |

**Gap to Level 2:** Production verified (workers read and confirmed ACTIVE) but NO KB article covering FPE.
**Advancement Path:** Create `fpe_overview.md` (Wave-2 scope) → Level 2 immediately; management approval → Level 3.

---

## Domain 07 — Recruitment

**Maturity Level: 3 (Management Approved)**

| Dimension | Status |
|---|---|
| KB Articles | `05_workflows/recruitment_workflow.md`, `04_business_rules/recruitment_business_rules.md`, `01_employee_knowledge/recruitment_policy.md` |
| Session State Machine | 7-step: new → step:name → step:age → step:area → step:position → step:experience → step:phone → scored / expired |
| Age Range | 18–55 (BR-25 RESOLVED; production code: `modules/recruitment_flow._parse_age()`) |
| Positions | 9 valid positions documented |
| Scoring | Experience (60/40/20/0 pts), position (20 pts), completeness (20 pts), max 100 |
| TTL | SESSION_TTL = 24h (HK-33) |
| AI Brain | 4 deterministic categories; fallback Bangla message; reads from resources/ops/recruitment_source_of_truth.txt |
| Management Decision | BR-25 RESOLVED, HK-33, HK-34, HK-36 |
| Unresolved Conflicts | None |

**Advancement Path:** PKVC post-Wave-1 run → no new conflicts expected → Level 4 achievable.

---

## Domain 08 — Identity Brain

**Maturity Level: 2 (Production Verified)**

| Dimension | Status |
|---|---|
| KB Articles | `03_ai_identity/identity_overview.md`, `03_ai_identity/permission_matrix.md` |
| Algorithm | 11-step resolution; step 0 = blocked pre-check; steps 1–11 with table, evidence, database |
| Confidence Scoring | 1.0 / 0.95 / 0.7 / 0.5 / 0.0 with criteria |
| Phone Normalization | 3 variants → canonical 8801XXXXXXXXXX |
| Evidence Sources | 8 sources across 7 tables documented |
| Management Decision | None formal for identity algorithm itself |
| Missing | Bangla role-specific system prompt injection (role_classifier) |

**Gap to Level 3:** Identity algorithm is a production engineering choice — no management decision exists for the 11-step priority order.
**Advancement Path:** Request management ratification of priority order → management decision → Level 3.

---

## Domain 09 — AI Behavior

**Maturity Level: 2 (Production Verified)**

| Dimension | Status |
|---|---|
| KB Articles | `06_developer_system/automation_pipeline.md`, `04_business_rules/ai_response_rules.md` |
| Reply Chain | GitHub Models → Groq → Ollama (with model names, Groq rate limits 14400/day 30RPM) |
| Intent Chain | Groq → GitHub → Ollama (different order — documented with rationale) |
| Fallback | Bangla holding message verified; automated reply suffix verified |
| Config Flags | 9 AI flags documented (OLLAMA_REPLY_DISABLED, AI_SAFE_MODE, etc.) |
| Management Decision | None for LLM provider selection or fallback strategy |
| Missing | Draft quality gate (4 criteria); memory extractor fire-and-forget; reply template rotation |

**Gap to Level 3:** LLM provider ordering is an engineering decision — no management approval exists.
**Advancement Path:** Wave-2 enrichment of quality gate + memory extractor → management ratification → Level 3.

---

## Domain 10 — RAG

**Maturity Level: 1 (Documented)**

| Dimension | Status |
|---|---|
| KB Article | `06_developer_system/rag_strategy.md` (abstract; not enriched in Wave-1) |
| BM25 Params | k1=1.5, b=0.75 — known from PKCA but NOT in KB |
| Chunk Config | 320 chars / 60 overlap — known from PKCA but NOT in KB |
| Tokenizer | `[A-Za-z0-9ঀ-৿]+` bilingual — known from PKCA but NOT in KB |
| Excluded Dirs | 11 excluded directories — NOT in KB |
| Rebuild Schedule | rag_rebuild job at 18:00 — documented in automation_pipeline.md only |
| Management Decision | None |

**Gap to Level 2:** Technical RAG parameters identified in PKCA but not written to KB.
**Advancement Path:** Enrich rag_strategy.md (Wave-2 scope) → Level 2 immediately.

---

## Domain 11 — OCR Engine

**Maturity Level: 1 (Documented)**

| Dimension | Status |
|---|---|
| KB Article | `ocr_engine.md` (stub; not enriched), `release_slip_workflow.md` (partial) |
| Partial Documentation | 6 of 18 EscortSlipResult fields documented; 4 document types documented |
| Full TypedDict | 18 fields — not in KB |
| Label Blacklist | 35+ strings — not in KB |
| Signature Detection | Not documented |
| Management Decision | None |

**Gap to Level 2:** Module read (partially) in Wave-1 for release_slip context; full TypedDict not extracted.
**Advancement Path:** Enrich ocr_engine.md with full TypedDict and blacklist (Wave-2) → Level 2.

---

## Domain 12 — Scheduler

**Maturity Level: 2 (Production Verified)**

| Dimension | Status |
|---|---|
| KB Article | `06_developer_system/automation_pipeline.md` (complete scheduler section) |
| Jobs | All 15 jobs documented with schedule, function, module, idempotency |
| Verification | All 15 jobs confirmed ACTIVE in `modules/scheduler/__init__.py` `start_scheduler()` |
| Env Overrides | Documented (SCHEDULER_ENABLED flag) |
| Admin Commands | SCHEDULE STATUS, RUN JOB documented |
| Management Decision | None required (operational configuration) |
| Missing | Detailed job failure behavior; APScheduler misfire_grace_time values |

**Advancement Path:** Document misfire_grace and failure modes in Wave-2 → management ratification → Level 3.

---

## Domain 13 — Message Router

**Maturity Level: 3 (Management Approved)**

| Dimension | Status |
|---|---|
| KB Articles | `04_business_rules/ai_response_rules.md`, `06_developer_system/security_rules.md` |
| 15-Step Priority | Documented in automation_pipeline.md |
| Silent-Skip | 3 conditions: 11 tokens, accountant phone, blocked role (HK-01) |
| Draft-Always Gate | 4 roles: accountant, client_escort_buyer, vip_client, repeat_client (HK-09) |
| Safe Auto-Send | 9 intents (HK-03, HK-04) |
| Force-Draft Phrases | Complaint (11), advance (5) documented |
| office_location | Fast path bypass (KB-only, no LLM) documented (HK-47) |
| Management Decision | HK-01, HK-03, HK-04, HK-09, HK-13, HK-44 |
| Unresolved Conflicts | None |

**Advancement Path:** PKVC certification post-Wave-1 → Level 4 achievable quickly.

---

## Domain 14 — Notification / Outbound Queue

**Maturity Level: 2 (Production Verified)**

| Dimension | Status |
|---|---|
| KB Article | Inline in `automation_pipeline.md` — no dedicated article |
| Outbound Queue | State machine: queued → sending → sent / failed; retry logic documented |
| DLQ | Dead-letter queue behavior documented |
| Sweep | sweep_once() function verified |
| Cooldown | REPLY_COOLDOWN=60s (HK-44) documented |
| Management Decision | HK-44 (cooldown) — partial; no outbound-specific management decision |

**Gap to Level 3:** No dedicated article; outbound queue as a system has no formal management authority.
**Advancement Path:** Extract outbound section to dedicated article → management ratification → Level 3.

---

## Domain 15 — Security Rules

**Maturity Level: 3 (Management Approved)**

| Dimension | Status |
|---|---|
| KB Article | `06_developer_system/security_rules.md` |
| Loop Detection | 3 msg/120s → pause 600s (HK-13) — management approved |
| Keyword Flood | 3 keywords in 5min → 15min block (HK-14) |
| Prompt Injection | 18 patterns → outbound_safety_incidents (HK-15) |
| Poison Filter | 16 strings in outbound (HK-12) |
| Reply Cooldown | 60s Redis + fallback (HK-44) — management approved |
| Group/Broadcast Skip | HK-16 |
| RAG Safety Filter | 30+ patterns (HK-31) |
| API Key Storage | SHA-256 (HK-42) |
| Admin Dedup | SHA1(text+phone) 30s TTL 256 entries (HK-37) |
| Management Decision | HK-13 (loop), HK-44 (cooldown); HK-12/14/15 not explicitly in management decisions |

**Note:** HK-12, HK-14, HK-15 are documented production behaviors but not in the formal management decision log.
**Advancement Path:** Formal ratification of HK-12/14/15 → PKVC certification → Level 4.

---

## Domain 16 — Admin Commands

**Maturity Level: 2 (Production Verified)**

| Dimension | Status |
|---|---|
| KB Article | `02_admin_knowledge/admin_operations_overview.md` |
| Coverage | All 37 commands documented across 8 groups |
| Verification | `modules/admin_commands dispatch_command()` verified |
| RBAC Enforcement | COMMAND_ROLE mapping verified |
| Global Features | Bangla digits, multi-ID APPROVE, dedup, audit log |
| Management Decision | No formal management approval for command set as domain |

**Advancement Path:** Management ratification of command set → PKVC certification → Level 4.

---

## Domain 17 — RBAC

**Maturity Level: 2 (Production Verified)**

| Dimension | Status |
|---|---|
| KB Articles | `06_developer_system/role_permissions.md`, `02_admin_knowledge/admin_role_management.md` |
| Hierarchy | 5-level: viewer < operator < accountant < admin < superadmin |
| Bootstrap | ADMIN_NUMBERS env → auto-superadmin on first message (HK-41) |
| Role Gate Matrix | All 12 roles × 4 gate behaviors (draft-always, silent-skip, recruiting-blocked, auto-reply) |
| Management Decision | HK-41 (bootstrap only) |
| Missing | Formal ratification of 5-level hierarchy itself |

**Advancement Path:** Management ratification of RBAC hierarchy → Level 3.

---

## Domain 18 — Database Behavior

**Maturity Level: 0 (Unknown)**

| Dimension | Status |
|---|---|
| KB Article | `06_developer_system/database_rules.md` (abstract only — connection, retry, advisory lock concepts) |
| Table Inventory | 43 tables identified in PKCA; 0 tables documented in KB |
| Phone Lookup Variants | Not in KB |
| Idempotency Patterns | Mentioned in payroll article only; not consolidated |
| Soft-Delete Patterns | Not documented |
| ON CONFLICT | Only in attendance article as inline note |
| Management Decision | None |

**Note:** Database behavior is known to the development team but entirely absent from the Knowledge Base.
**Advancement Path:** Wave-2 enrichment of database_rules.md (43 tables + patterns) → Level 2.

---

## Domain 19 — Parser Engine

**Maturity Level: 1 (Documented)**

| Dimension | Status |
|---|---|
| KB Article | `parser_engine.md` (stub); parser details inline in workflow articles |
| Verified Parsers | attendance_parser, release_confirmation_parser, escort_order_parser documented in workflows |
| Unverified | 12 of 15 parsers not documented |
| Engine Architecture | Not documented |
| Management Decision | None |

**Advancement Path:** Enrich parser_engine.md with all 15 parsers → Level 2.

---

## Domain 20 — Social Auto Reply

**Maturity Level: 0 (Unknown)**

| Dimension | Status |
|---|---|
| KB Article | None |
| Production Reality | 20-file system: Facebook/Messenger/Meta WhatsApp comment reply |
| KB Coverage | ~4% (PKCA estimate) — effectively 0% useful documentation |
| New Article Justified | Yes (PKCA Report 17 justified new article `social_auto_reply_system.md`) |
| Management Decision | None |

**Advancement Path:** Create `social_auto_reply_system.md` (Wave-2 scope) → Level 1 immediately.

---

## Domain 21 — WhatsApp Channel

**Maturity Level: 2 (Production Verified)**

| Dimension | Status |
|---|---|
| KB Coverage | Bridge ports documented (developer_notes); watchdog job documented (automation_pipeline) |
| Bridge Config | bridge1=8082 (HR), bridge2=8081 (OPS), media=8090, app=8200 verified |
| Bridge Watchdog | bridge_watchdog job every 5min verified in scheduler |
| Management Decision | None |
| Missing | No dedicated WhatsApp channel article |

**Advancement Path:** Expand developer_notes or create dedicated article → management ratification → Level 3.

---

## Domain 22 — Messenger

**Maturity Level: 0 (Unknown)**

No KB documentation. Part of undocumented social_auto_reply system.
**Advancement Path:** Covered by `social_auto_reply_system.md` Wave-2 article.

---

## Domain 23 — Facebook

**Maturity Level: 0 (Unknown)**

No KB documentation. Part of undocumented social_auto_reply system.
**Advancement Path:** Covered by `social_auto_reply_system.md` Wave-2 article.

---

## Domain 24 — Voice

**Maturity Level: N/A (Not Implemented)**

Voice functionality is not present in the Fazle AI Platform. No assessment applicable.

---

## Domain 25 — Bridge

**Maturity Level: 2 (Production Verified)**

| Dimension | Status |
|---|---|
| KB Coverage | Bridge ports in developer_notes; bridge_health_loop in FPE workers (verified) |
| Circuit Breaker | Mentioned in developer_notes |
| Health Check | bridge_health_loop verified ACTIVE (POLL_INTERVAL=3s) |
| Management Decision | None |
| Missing | No dedicated bridge architecture article |

---

## Domain 26 — Automation Pipeline

**Maturity Level: 2 (Production Verified)**

| Dimension | Status |
|---|---|
| KB Article | `06_developer_system/automation_pipeline.md` (comprehensively enriched in Wave-1) |
| Coverage | LLM chains, scheduler (15 jobs), outbound queue, DLQ, suffix, holding message |
| Production Verified | All components verified in multiple modules |
| Management Decision | None for pipeline as domain |
| Missing | Draft quality gate (4 criteria); memory extractor; reply template rotation |

---

## Domain 27 — Developer System

**Maturity Level: 2 (Production Verified)**

| Dimension | Status |
|---|---|
| Articles | 5 of 7 enriched; database_rules.md and rag_strategy.md not enriched |
| Coverage | Config flags, bridge ports, backup, RBAC, security, identity, scheduler all verified |
| Management Decision | None for developer system as domain |
| Gap | database_rules.md and rag_strategy.md still at Level 1 or below |

---

## Domain 28 — Business Rules

**Maturity Level: 3 (Management Approved)**

| Dimension | Status |
|---|---|
| Articles | All 4 articles in 04_business_rules/ enriched in Wave-1 |
| Rules Covered | Transport, food, payment formula, advance keywords, draft-always, silent-skip, recruitment rules |
| Management Decision | CON-01–04, BR-25, HK-01, HK-03, HK-04, HK-09, HK-33, HK-34 |
| Unresolved Conflicts | None (BR-25 resolved) |

---

## Domain 29 — Workflow

**Maturity Level: 3 (Management Approved)**

| Dimension | Status |
|---|---|
| Articles | All 6 workflow articles enriched in Wave-1 |
| State Machines | All major state machines documented |
| Management Decision | Per-domain management decisions all applied |
| Unresolved Conflicts | None |

---

## Domain 30 — State Machines

**Maturity Level: 2 (Production Verified)**

| State Machine | Article | Production Module | Verified |
|---|---|---|---|
| Payroll | salary_workflow.md | modules/payroll | Yes |
| Escort | escort_workflow.md | modules/escort_lifecycle | Yes |
| Payment Draft | payment_workflow.md | modules/payment_workflow | Yes |
| Attendance Draft | attendance_workflow.md | modules/attendance | Yes |
| Recruitment Session | recruitment_workflow.md | modules/recruitment_flow | Yes |
| Employee Verification | payment_workflow.md | modules/employee_verification | Yes |
| Outbound Queue | automation_pipeline.md | modules/outbound | Yes |
| DLQ | automation_pipeline.md | modules/outbound | Yes |
| Release Date Validation | release_slip_workflow.md | modules/escort_lifecycle | Yes |
| Bridge Health | automation_pipeline.md | modules/fazle_payroll_engine | Yes |

All 10 state machines verified. No management approval exists for state machines as a domain.
**Advancement Path:** Management ratification → PKVC cert → Level 4.

---

*PKMA v1.0 | READ-ONLY | 2026-06-22*
