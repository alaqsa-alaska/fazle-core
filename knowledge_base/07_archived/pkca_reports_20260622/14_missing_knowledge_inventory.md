---
title: PKCA Report 14: Missing Knowledge Inventory
owner: Fazle Core Admin
status: archived
last_verified: 2026-06-24
runtime_index: false
---

# PKCA Report 14: Missing Knowledge Inventory

**Date:** 2026-06-22
**Mode:** Read-Only Analysis

---

## Inventory Method

This report lists every distinct production knowledge unit that has NO documentation in the KB (0%). Items with partial coverage (>0%) are in the gap matrix (Report 13).

---

## Category 1: Message Routing Pipeline (0%)

The 15-step message routing pipeline is entirely undocumented:

1. **Bridge poller cursor** — adaptive 1s→30s backoff per bridge, cursor stored in `bridge_poller_cursor` table
2. **Message deduplication** — `processed_bridge_messages` table prevents double-processing; SHA-based message hash
3. **Group/broadcast skip** — @g.us, newsletters, status@broadcast silently skipped at SQL level
4. **LID unresolved DMs** — stored as phone='unresolved:\<lid\>' not dropped
5. **OCR trigger conditions** — JPG/JPEG/PNG/WEBP, 1KB–8MB; `media_normalization` generates placeholder text
6. **15-step routing sequence** — from family fast-path (step 1) to polite holding (step 15); step-by-step decision tree
7. **Bridge1 vs Bridge2 purpose** — bridge1=8082 (HR channel), bridge2=8081 (OPS channel)
8. **Outbound circuit breaker** — BridgeClient opens after N failures, closes on recovery
9. **Automated suffix anchor** — prevents double-appending suffix on retry

---

## Category 2: Admin Command System (0%)

The following admin command knowledge is entirely missing:

1. **PAYROLL command group** — 7 payroll commands, their syntax, required roles, and actions
2. **BACKUP command group** — 3 backup commands
3. **SCHEDULE STATUS / RUN JOB** — scheduler admin commands
4. **USER REMOVE / USER LIST / USER APIKEY** — user management commands
5. **Bangla digit support in commands** — APPROVE ১৬৫ works
6. **Multi-ID APPROVE** — APPROVE 165 166 167
7. **Command deduplication** — SHA1(text+phone), 30s TTL, 256 entries
8. **Audit trail** — every command logged to fazle_admin_audit

---

## Category 3: Draft System (0%)

1. **Draft quality rejection criteria** — 4 criteria: empty, LLM_FALLBACK_EXACT string match, BAD_PATTERNS list (11 items: file://, /home/azim, Traceback, etc.), MAX_DRAFT_LEN=4000
2. **Draft state names** — pending, approved, rejected, rejected_quality, rejected_fallback, expired
3. **Attendance draft TTL** — expiry via draft_ttl_cleanup job every 30 min
4. **Reviewed reply memory match hierarchy** — intent_role_phone → intent_role → intent
5. **Reviewed reply memory exclusions** — attendance, payment, gap_action draft types excluded from learning

---

## Category 4: Scheduler System (0%)

All 15 scheduled jobs are completely undocumented:

1. daily_payroll_compute — 02:00 daily
2. dlq_alert — every 15 min
3. health_summary — every 6h
4. agent_incident_summary — every 6h
5. stale_escort_reminder — 09:00 daily
6. payment_reconciliation — hourly
7. backup_staleness_alert — 03:00 daily
8. combined_draft_cleanup — hourly
9. daily_memory_review — 09:00 daily
10. rag_rebuild — 18:00 daily
11. daily_admin_digest — 08:00 daily
12. daily_db_backup — 02:30 daily
13. lock_cleanup — every 5 min
14. draft_ttl_cleanup — every 30 min
15. bridge_watchdog — every 5 min

---

## Category 5: Outbound Queue System (0%)

1. **Queue states** — pending → sending → sent / failed → dlq
2. **DLQ behavior** — max_attempts exceeded → dlq status; alert every 15 min if DLQ > 0
3. **Exponential backoff** — retry delays double per attempt
4. **Idempotency key format** — prevents duplicate enqueue
5. **Multi-channel support** — bridge1, bridge2, meta, messenger, comment channels
6. **OUTBOUND_ENABLED env flag** — global kill switch
7. **Circuit breaker** — CLOSED→OPEN after failures; OPEN→CLOSED on recovery

---

## Category 6: AI System (0%)

1. **Groq rate limits** — 14,400 requests/day, 30 RPM (hard limits embedded in llm.py)
2. **Intent vs reply chain order difference** — Groq-first for intent, GitHub-first for reply
3. **Exact model names** — openai/gpt-4o-mini (GitHub), llama-3.1-8b-instant (Groq), qwen3:8b (Ollama)
4. **LLM fallback holding message** — exact Bangla text returned when all providers fail
5. **OLLAMA_REPLY_DISABLED** — Ollama never used for customer WhatsApp replies when set
6. **Recruitment AI safe fallback** — "এই বিষয়ে নিশ্চিত তথ্যের জন্য অফিসে যোগাযোগ করুন।"
7. **Recruitment AI reads from** — `resources/ops/recruitment_source_of_truth.txt`
8. **Reply template categories** — recruitment (normal/frustrated), greeting, salary, vendor, incident, emergency, followup
9. **Memory extractor fire-and-forget** — runs after every exchange, does not block reply

---

## Category 7: Escort Slip Extractor (0%)

1. **EscortSlipResult TypedDict** — all 18 fields including signatures{}, confidence, missing_fields[], extraction_id
2. **Document type detection** — printed_template_slip, handwritten_blank_slip, mixed_form, unknown_document
3. **Template detection keywords** — 16 keywords (al-aqsa, escort slip, master mobile, etc.)
4. **Label blacklist** — 35+ strings that can never be field values
5. **Signature detection** — lighter master signed, ghat supervisor signed, company signed
6. **REQUIRED_FIELDS** — mother_vessel, lighter_vessel, escort_name, escort_mobile, start_date, completion_date

---

## Category 8: Fazle Payroll Engine — FPE (0%)

The FPE is a completely separate background engine from the core payroll module:

1. **5 worker threads** — message_processor_worker, accounting_worker, historical_sync_loop, gap_scan_loop, bridge_health_loop
2. **FPE processing state machine** — pending→parsing→parsed→accounting→done/failed/skipped
3. **MessageType enum** — payment, balance_summary, cash_command, income_command, escort_payment, other
4. **TxnCategory enum** — transaction categories for FPE
5. **FPE database tables** — fpe_wa_messages, fpe_message_processing_state, fpe_cash_transactions, fpe_employees
6. **FPE API routes** — /api/fpe/* endpoints
7. **FPE vs core payroll distinction** — FPE uses fpe_cash_transactions (separate from wbom_cash_transactions)

---

## Category 9: Security Protections (0%)

1. **Prompt injection blocked patterns** — 18 patterns (exact list not published)
2. **Outbound poison filter** — 16 internal strings blocked from outbound
3. **Loop protection** — 3 replies/120s → pause 600s (Redis primary, in-memory fallback)
4. **Keyword flood protection** — same keyword >3 in 5 min → 15 min block
5. **Reply cooldown** — 60s between auto-replies per contact (Redis primary, in-memory fallback)
6. **Bootstrap admin creation** — ADMIN_NUMBERS from .env auto-created as superadmin

---

## Category 10: wa_chat_frontend Admin Dashboard (0%)

1. **25 REST endpoints** — full list undocumented
2. **SSE event stream** — /api/wa/stream with auth variants (?key= for SSE, X-Internal-Key for REST)
3. **Contact CRUD** — create/edit/delete/block/unblock contacts
4. **Draft management UI** — approve/edit/reject from web interface
5. **Group messaging endpoint** — send to multiple recipients
6. **Settings management** — view/update config settings via web
7. **Observability** — Prometheus /metrics endpoint at app_port/metrics

---

## Category 11: Phone Normalization (0%)

1. **Canonical format** — 8801XXXXXXXXXX (13 digits, no +)
2. **Phone lookup variants** — always try: 01XXXXXXXXX, 880XXXXXXXXX, +880XXXXXXXXX
3. **Best-name strategy** — longest non-empty name wins in contact sync
4. **contact_sync sources** — bridge1 + bridge2 + Meta; incremental and full sync modes

---

## Category 12: Database Behavior (0%)

1. **43 production tables** — none named in KB
2. **Advisory locks** — concurrent payment writes use Postgres advisory locks
3. **Soft-delete** — wbom_employees uses status='Inactive' never hard-delete
4. **Message hash dedup** — UNIQUE INDEX on message_hash in wbom_whatsapp_messages
5. **Attendance dedup** — UNIQUE(employee_id, attendance_date) with ON CONFLICT UPDATE
6. **Backup rotation** — 14 daily + 8 weekly; SHA-256 hash verification

---

## Missing Knowledge Count

| Category | Knowledge Units Missing |
|---|---|
| Message routing pipeline | 9 |
| Admin command system | 8 |
| Draft system | 5 |
| Scheduler (all 15 jobs) | 15 |
| Outbound queue | 7 |
| AI system | 9 |
| Escort slip extractor | 6 |
| Fazle Payroll Engine | 7 |
| Security protections | 6 |
| wa_chat_frontend | 7 |
| Phone normalization | 4 |
| Database behavior | 6 |
| **Total** | **89 missing knowledge units** |

**Total KB articles: 65**
**Total missing knowledge units: 89**
**Ratio: 1.4 missing units per existing article**
