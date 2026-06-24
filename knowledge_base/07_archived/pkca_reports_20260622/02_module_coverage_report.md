---
title: PKCA Report 02: Module Coverage Report
owner: Fazle Core Admin
status: archived
last_verified: 2026-06-24
runtime_index: false
---

# PKCA Report 02: Module Coverage Report

**Date:** 2026-06-22
**Mode:** Read-Only Analysis

---

## Group A — Critical Infrastructure (0–10% Coverage)

### A1. bridge_poller (1467 lines) — 5% Covered

**Purpose:** Core message ingestion engine. Polls two WhatsApp bridge SQLite stores every 1–30s (adaptive backoff).

**Production Knowledge:**
- Adaptive poll interval: MIN=1s → MAX=30s (1.5× per idle cycle)
- LID→Phone resolution for unresolved contacts (stored as `unresolved:<lid>`)
- Dedup via `processed_bridge_messages` (ON CONFLICT DO NOTHING)
- OCR branch: JPG/JPEG/PNG/WEBP, 1KB–8MB only
- Voice branch: audio/ptt → voice_processor
- Loop detection: 3 replies / 120s → pause 600s
- Keyword flood: same keyword >3 / 5m → block 15m
- 18 prompt injection patterns (regex-blocked and logged)
- 16 outbound poison markers (block send; log to outbound_safety_incidents)
- Silent-skip: accountant phone + 11 display-name tokens + role='blocked'
- Draft-always gate: accountant, client_escort_buyer, vip_client, repeat_client
- Complaint phrases (Bangla): 10 phrases force draft
- Advance request phrases (Bangla): 5 phrases force draft
- Reply cooldown: 60s (Redis primary, in-memory fallback)
- On fresh start: begins from NOW()-5 minutes
- Groups (@g.us), newsletters, status@broadcast silently skipped at SQL level
- process_message() → calls message_router

**KB Coverage:** 5% — no dedicated KB article. workflow_engine.md mentions "message.received" event only.

**Enrichment Target:** `06_developer_system/bridge_engine.md` (new article)

---

### A2. message_router (580 lines) — 10% Covered

**Purpose:** 15-step priority routing engine. Routes every inbound message to the correct handler.

**Production Knowledge:**
- Step 1: family → personal reply
- Step 2: escort_roles → handle_escort_client_message()
- Step 3: admin → command/NL/help (never falls through to AI)
- Step 4: attendance detected → create_attendance_draft()
- Step 5: intent classification (LLM → fallback deterministic)
- Step 6: accountant → payment ingest / KB / AI
- Step 7: candidate/unknown → recruitment_eligibility()
- Step 8: escort order intent → handle_escort_client_message()
- Step 9: employee → verification/attendance/slip/release/complaint/advance
- Step 10: advance request (any) → start_advance_verification()
- Step 11: office_location → KB fast path (bypasses AI entirely)
- Step 12: KB lookup
- Step 13: Reviewed reply memory
- Step 14: AI fallback (GitHub→Groq→Ollama)
- Step 15: polite holding message
- _looks_like_escort_order() keyword detection (8 phrases)
- _SAFE_AUTOSEND_INTENTS list (9 intents)
- advance_request excluded from auto-send (intentional)

**KB Coverage:** 10% — workflow_engine.md lists state machine names but not the 15-step routing order.

**Enrichment Target:** `06_developer_system/workflow_engine.md` (enrich with routing priority)

---

### A3. admin_commands (1297 lines) — 5% Covered

**Purpose:** WhatsApp-based admin command processor. Handles all 38 operational commands.

**Production Knowledge:**
- 38 commands in 7 categories: Draft, Payment, Escort, Payroll, Report, System, NL
- RBAC guard: each command has minimum required role (viewer/operator/accountant/admin/superadmin)
- Dedup: SHA1(text+phone), 30s TTL, 256 entries
- Bangla digit support: APPROVE ১৬৫ works (automatic normalization)
- APPROVE supports multi-ID: APPROVE 165 166 167
- NL router: handles natural-language admin queries (last N chats, last contact)
- All commands logged to fazle_admin_audit

**KB Coverage:** 5% — admin_operations_overview.md lists 4 admin responsibilities but not a single command syntax.

**Enrichment Target:** `02_admin_knowledge/admin_operations_overview.md` (enrich); create `02_admin_knowledge/admin_command_reference.md` if needed.

---

### A4. scheduler (690 lines) — 0% Covered

**Purpose:** APScheduler-based cron job manager. Runs 14+1 scheduled jobs.

**Production Knowledge:**
- Timezone: Asia/Dhaka (configurable via SCHEDULER_TIMEZONE)
- 14 named jobs: daily_payroll_compute, dlq_alert, health_summary, agent_incident_summary, stale_escort_reminder, payment_reconciliation, backup_staleness_alert, combined_draft_cleanup, daily_memory_review, rag_rebuild, daily_admin_digest, daily_db_backup, lock_cleanup, draft_ttl_cleanup
- bridge_watchdog: every 5 minutes (stale check)
- Each job has env overrides (PAYROLL_AUTO_COMPUTE_HOUR, DLQ_ALERT_INTERVAL_MIN, etc.)
- Job run metadata stored in fazle_scheduled_jobs
- Service heartbeats stored in fazle_service_heartbeats

**KB Coverage:** 0% — no scheduler article anywhere in KB.

**Enrichment Target:** `06_developer_system/automation_pipeline.md` (enrich with job list)

---

### A5. social_auto_reply (~2000 lines, 20 files) — 0% Covered

**Purpose:** Facebook/Messenger/Meta WhatsApp automated reply system.

**Production Knowledge:**
- 20 source files covering distinct responsibilities
- Facebook comment handling, Messenger chat, Meta WhatsApp webhook
- AI-powered intelligent reply generator
- Risk flagger for sensitive content
- Rate limiter (per-platform, per-sender)
- Send queue with retry and dead letter
- State tracker (conversation state per sender)
- Conversation history management
- Employee lookup from Messenger context
- Payment issue handler
- Salary inquiry flow
- Backlog processor for delayed messages
- Deduplicator across platforms
- Social reply source of truth: reply_rules.py (see DUP-07 decision)
- Office address in social_auto_reply uses abbreviated version (not authoritative full address)

**KB Coverage:** 0% — no KB article covers Facebook/Messenger/Meta WhatsApp at all.

**Enrichment Target:** `06_developer_system/automation_pipeline.md` (enrich); dedicated article if needed.

---

### A6. wa_chat_frontend (820 lines) — 0% Covered

**Purpose:** WhatsApp Web-style chat UI backend (25 REST endpoints + SSE stream).

**Production Knowledge:**
- GET /api/wa/contacts — paginated contact list with unread counts
- PATCH /api/wa/contacts/{phone} — edit display_name
- POST /api/wa/contacts/{phone}/block — disable auto-reply for number
- GET /api/wa/messages/{phone} — cursor-paginated conversation history
- POST /api/wa/send — send message via bridge
- POST /api/wa/broadcast — broadcast to multiple contacts
- GET /api/wa/drafts — pending drafts with contact name + original msg
- POST /api/wa/drafts/{id}/approve — approve + enqueue draft
- POST /api/wa/drafts/{id}/edit — edit draft body
- POST /api/wa/drafts/{id}/reject — reject draft
- POST /api/wa/groups — create group; GET/PATCH/DELETE group; POST send to group
- GET/PATCH /api/wa/settings — role-based auto-reply toggle management
- GET /api/wa/stream — SSE real-time stream (new messages + new drafts)
- Auth: X-Internal-Key header or ?key= query param (SSE only)

**KB Coverage:** 0% — no mention of the web frontend anywhere in KB.

**Enrichment Target:** `02_admin_knowledge/admin_operations_overview.md` (enrich with frontend reference)

---

### A7. fazle_payroll_engine (~1500 lines, 15 files) — 0% Covered

**Purpose:** Separate background payroll processing engine (FPE). Distinct from `modules/payroll`.

**Production Knowledge:**
- 5 background workers: message_processor_worker, accounting_worker, historical_sync_loop, gap_scan_loop, bridge_health_loop
- API routes: /api/fpe/* (10+ endpoints)
- MessageType enum: payment, balance_summary, cash_command, income_command, escort_payment, other
- TxnCategory enum: salary, advance, bonus, deduction, correction, income
- ProcessingStatus: pending→parsing→parsed→accounting→done/failed/skipped
- Employee matching: 4 rules (A: id_phone exact, B: payout_phone, C: fuzzy name >0.95, D: auto-create)
- fpe_cash_transactions table (distinct from wbom_cash_transactions)
- fpe_wa_messages table, fpe_message_processing_state table
- Historical sync: syncs conversation history from bridge SQLite into FPE
- Gap scan: detects payment messages that were missed
- Reversal support via /api/fpe/transactions/{id}/reverse
- Diagnostic bridge health monitoring

**KB Coverage:** 0% — no KB article references FPE.

**Enrichment Target:** `06_developer_system/database_rules.md` (enrich with FPE tables); dedicated article if scope justifies.

---

## Group B — Partially Covered Modules (30–79%)

### B1. attendance (246L) — 40% Covered

**What's in KB:** Guard duty day definition (12h=1 day), shift start times, attendance confirmation windows, duplicate check mentioned.

**Missing:** Parser internals (`_DATE_PATTERNS`, `_SHIFT_RE`, `_MOBILE_RE`, `_NAME_LABEL_RE`), attendance draft structure, save behavior (`wbom_attendance ON CONFLICT UPDATE`), distinction from attendance_parser module.

**Enrichment Target:** `05_workflows/attendance_workflow.md` — add parser details and save behavior.

---

### B2. identity_brain (393L) — 35% Covered

**What's in KB:** 11-role priority list, role names, basic routing rules.

**Missing:** Full priority numbers (200 for admin → 0 for unknown), 8 evidence source chain and order, confidence scores per source (88 for db_employee, 86 for cash/attendance, 85 for escort roster, etc.), secondary evidence sources (cash, attendance, escort roster, contact DB), text_hint candidate keywords (10 Bangla/English terms).

**Enrichment Target:** `03_ai_identity/identity_overview.md` — add evidence sources + confidence table.

---

### B3. escort (1047L) — 30% Covered

**What's in KB:** Basic order flow (client → draft → admin → confirm → confirm), required fields listed.

**Missing:** 4 parser formats (labeled block, inline, MV-block, numbered), parser regex constants (_MV_LABEL_RE etc.), is_completed_escort_draft() detection, handle_admin_escort_completion() flow, client never receives direct reply (always admin draft), remarks JSON structure.

**Enrichment Target:** `05_workflows/escort_workflow.md` — add parser format examples.

---

### B4. recruitment_flow (365L) — 30% Covered

**What's in KB:** 6-step collection flow (name→age→area→position→experience→phone), auto-reply allowed, manual review triggers.

**Missing:** VALID_POSITIONS (9 exact positions), SESSION_TTL=24h, _compute_score formula (experience≥6yr=60pts, ≥3yr=40pts, ≥1yr=20pts; target position=20pts; completeness=20pts), scoring bands (Hot≥70, Warm≥40, Cold<40), INTAKE_KEYWORDS (10 terms), recruitment_eligibility() gate (OPERATIONAL_ROLES blocked).

**Enrichment Target:** `04_business_rules/recruitment_business_rules.md` — add scoring algorithm and positions list.

---

### B5. user_role (247L) — 30% Covered

**What's in KB:** Role types and priority order listed in identity_overview.md.

**Missing:** normalize_phone to 11-digit local format, UserRole TypedDict structure, multi-source admin phone check, accountant phone check, confidence float field.

**Enrichment Target:** `03_ai_identity/identity_overview.md` — this is already partially there.

---

### B6. payment_workflow (396L) — 25% Covered

**What's in KB:** Basic payment flow, accountant handoff trigger, identity verification requirement.

**Missing:** Approved payment formula (CON-01: 12000/30×days), DEFAULT_DAILY_RATE constant, ADVANCE_KEYWORDS (18 phrases including medical/emergency/family crisis), idempotency_key format (`payment-draft:{id}`), finalize_payment() deduction sequence.

**Enrichment Target:** `05_workflows/payment_workflow.md` + `04_business_rules/payment_business_rules.md` — add CON-01 formula, advance keywords.
