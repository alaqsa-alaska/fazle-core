---
title: Social Auto-Reply System — System Overview
owner: Fazle Core Admin
status: active
last_verified: 2026-06-24
runtime_index: true
---

# Social Auto-Reply System — System Overview

**Article Type:** Developer System Reference
**Visibility:** Admin / Developer / Superadmin
**Source Module:** `modules/social_auto_reply/`
**Production Status:** ACTIVE — started as part of `fazle-core.service`
**Wave:** Wave-2A (initial) | Wave-2B (enriched) | 2026-06-22
**Traceability:** PKCA Report 05, PKMA Report 18 (DEV-18), PKMA Report 19, PKMA Report 20

---

## Visibility and AI Exposure Rules

| Content | Visibility | AI May Expose? |
|---|---|---|
| Social system exists (Facebook/Meta auto-reply) | Admin | Admin only |
| Salary figures (17,000 / 24,700 BDT) | Admin / Restricted | Do NOT expose to candidates via AI. These figures are in production auto-replies but require management sign-off to be KB-authoritative. |
| Fee figures (350 / 3,500 / 1,000 BDT) | Admin / Restricted | Do NOT expose to candidates via AI. Same as above. |
| Intent classifier categories | Developer | No — never expose system internals |
| Module names, file names, API routes | Developer | No |
| Escalation thresholds and logic | Admin | Summary only — not implementation details |
| Office address and contact in replies | Public | Yes — this is public information |
| Age issue handling (no fixed range) | Admin | Note: auto-reply diverges from BR-25 (18–55). AI must use BR-25 when answering directly. |

**Management Sensitivity Warning:** The salary and fee figures in `reply_rules.py` are disclosed in live auto-replies but have NOT been formally recorded as management-approved KB values. Until formally approved, treat these as production behavior (current) rather than policy (authoritative). AI responses to candidates must defer to management-approved documents, not these auto-reply constants.

---

## Purpose

The Social Auto-Reply System handles automated responses to inbound social media messages for Al-Aqsa Security & Logistics Services Ltd. It monitors Facebook Messenger, Facebook Page comments, and Meta WhatsApp Business API for recruitment-related inquiries, classifies them using a deterministic keyword-based classifier, and sends context-appropriate Bangla replies automatically.

This system is entirely separate from the main WhatsApp routing engine (`app/message_router`). The main router handles employee and admin messages on the internal WhatsApp bridges. The social auto-reply system handles public-facing external channels (Facebook, Meta WhatsApp) for candidate recruitment.

**This system answers:** "Has a public user asked a recruiting question on Facebook or Meta WhatsApp, and if so, what should we reply — automatically or with human review?"

---

## Scope

This system handles:
- Inbound events from Facebook Messenger, Facebook Page comments, Meta WhatsApp Business API
- Internal bridges (bridge1, bridge2) as additional delivery channels
- Deterministic intent classification (30+ categories)
- Safe auto-reply for 15 low-risk intents
- Escalation flagging for 3 high-risk intents
- Risky content flagging for 16 intents requiring human review
- Per-channel rate limiting
- Exponential backoff retry with DLQ
- Admin pause/resume/retry control

This system does NOT handle:
- Employee or admin WhatsApp messages — that is `app/message_router`
- Internal payroll or attendance commands — that is `modules/payroll`
- Bridge-to-bridge relay — that is `modules/bridges`
- Main WhatsApp recruitment flow (internal employee-facing) — that is `modules/recruitment`

---

## Module Structure (21 Files)

**PKVC-corrected 2026-06-22:** Production count = 21 Python files (previous documentation said 20; `service_runner.py` was omitted).

| File | Role |
|---|---|
| `__init__.py` | Module init — creates 8 DB tables, exposes start function |
| `daemon_worker.py` | Single background asyncio task — main orchestration loop |
| `planner_worker.py` | Thread-level decision making — who gets a reply and when |
| `send_queue.py` | Queue sweep — picks and sends one pending reply per tick |
| `classifier.py` | Deterministic intent classifier (30+ intents, Bangla/English) |
| `comment_handler.py` | Facebook comment-specific routing logic |
| `risk_flagger.py` | Intent risk classification — safe/risky/escalation |
| `rate_limiter.py` | Per-channel rate limit enforcement |
| `reply_rules.py` | Bangla reply templates for all 15 safe intents |
| `intelligent_generator.py` | Reply plan logic (plan_reply per intent) |
| `reply_generator.py` | Comment-specific reply generation |
| `salary_flow.py` | Handles salary complaint escalation routing |
| `payment_issue_handler.py` | Routes payment-related complaints |
| `conversation_history.py` | Per-sender conversation context |
| `state_tracker.py` | Platform pause/resume state; status and queue metrics |
| `employee_lookup.py` | Checks if a sender is an existing employee |
| `retry_queue.py` | Retry tracking and exponential backoff |
| `message_deduplicator.py` | Event and reply deduplication keys |
| `backlog_processor.py` | Scans platform backlog for unanswered events |
| `service_runner.py` | Top-level service startup and lifecycle management |
| `routes.py` | 6 admin API endpoints |

---

## Architecture

```
Facebook/Meta/WhatsApp APIs
         ↓
External webhook receiver
         ↓
ingest_social_event()           ← main entry point
         ↓
social_inbox_events             ← all inbound events (UNIQUE on event_key)
         ↓
planner_worker (every daemon tick)
  ├── employee_lookup: is sender an employee?
  ├── per-sender intent cooldown (15 min window)
  ├── thread compression (25s window — combine events from same sender)
  ├── intent classification: classify(text)
  ├── escalation check → social_flagged_items (needs_admin)
  ├── risky check → social_flagged_items (manual_review)
  └── safe intent → social_reply_queue (pending)
         ↓
daemon_worker (main loop)
  ├── heartbeat → fazle_service_heartbeats
  ├── pause check → sleep if paused
  ├── every 600s: backlog_processor.scan_recent()
  ├── process_due_threads()
  └── rate check → send_queue.sweep_once()
         ↓
send_queue (1 message per tick)
  ├── messenger → Meta Messenger API
  ├── facebook_comment → Meta Graph API /{comment_id}/comments
  ├── meta_whatsapp → Meta WhatsApp Business API /{phone_number_id}/messages
  └── bridge1/bridge2 → internal bridges
         ↓
social_sent_log                 ← immutable sent record
social_inbox_events.reply_status ← updated to 'sent'
```

---

## Startup

**Entry:** `modules/social_auto_reply/__init__.py`

1. `start_social_auto_reply()` is called during `fazle-core.service` startup.
2. `_INIT_SQL` is run: creates all 8 social tables (`IF NOT EXISTS`).
3. Single asyncio task `daemon_worker.run()` is started as `social-auto-reply-worker`.

**No migrations directory** — the schema is managed entirely by `_INIT_SQL` in `__init__.py`. All `CREATE TABLE IF NOT EXISTS` statements run on every startup (idempotent).

---

## The Daemon Worker

**File:** `daemon_worker.py`
**Task name:** `social-auto-reply-worker`
**Type:** Single asyncio background task (no APScheduler, no Celery)
**Idle sleep:** `SOCIAL_REPLY_IDLE_SLEEP_S` env (default 120 seconds)

**Tick loop:**

1. Write heartbeat to `fazle_service_heartbeats` table (`ON CONFLICT DO UPDATE`)
2. Check `state_tracker.is_paused()` — if paused, sleep `SOCIAL_REPLY_IDLE_SLEEP_S` and restart loop
3. If backlog scan is due (every `SOCIAL_BACKLOG_SCAN_EVERY_S` = 600s): call `backlog_processor.scan_recent()`
4. Call `process_due_threads()` — planner_worker logic for all threads that have a reply decision pending
5. Call `rate_limiter.can_send(channel)` — if rate-limited: sleep and restart loop
6. Call `send_queue.sweep_once(limit=1)` — send exactly one queued message if available
7. Sleep: 30s idle (nothing queued), 5s (picked but not sent), delay seconds after send

---

## Intent Classification

**File:** `classifier.py`
**Algorithm:** Deterministic keyword-based (NO LLM, NO ML model)
**Languages:** Bangla (Unicode) + English

The classifier returns a single intent string per text input. For Facebook comments, `classify_comment()` wraps the base classifier with additional redirect rules.

**All 30+ Intent Categories:**

| Category | Examples | Type |
|---|---|---|
| `greeting` | "ভাই সালাম", "hello" | Safe |
| `interested` | "কাজ করতে চাই", "job nite chai" | Safe |
| `job_details` | "কি কাজ?", "কোন কাজ আছে?" | Safe |
| `salary` | "বেতন কত?", "salary ki" | Safe |
| `salary_objection` | "এত কম?", "বেতন কম লাগছে" | Safe |
| `location` | "অফিস কোথায়?", "কোন এলাকায়?" | Safe |
| `age_issue` | "আমার বয়স ১৭", "আমি কি apply করতে পারব?" | Safe |
| `documents` | "কি কি লাগবে?", "NID লাগবে?" | Safe |
| `fees` | "কোনো টাকা লাগবে?", "fee কত?" | Safe |
| `applicant_info_complete` | Has 3+ of: age, education, address, name | Safe |
| `training` | "ট্রেনিং কতদিন?", "training ki" | Safe |
| `join_process` | "কিভাবে join করব?" | Safe |
| `recruitment_follow_up` | "আগে apply করেছিলাম" | Safe |
| `career_growth` | "promotion হয়?", "future কি?" | Safe |
| `accommodation` | "থাকার ব্যবস্থা আছে?" | Safe |
| `accountant` | "hisab nite chai", "accounts-e kaj" | Risky |
| `transaction` | "টাকা পাঠাতে চাই" | Risky |
| `employee_id` | "আমার ID কত?", "employee number" | Risky |
| `reports_issue` | "report-e সমস্যা", "কিছু report করব" | Risky |
| `escort_order` | "escort দরকার", "guard পাঠান" | Risky |
| `escort_client` | "আমাদের client", "site visit" | Risky |
| `roster_issue` | "roster নিয়ে সমস্যা" | Risky |
| `internal_operations` | "management-এর সাথে কথা", "internal" | Risky |
| `complaint` | "অভিযোগ আছে", "complain করব" | Risky |
| `payment_issue` | "টাকা পাইনি", "payment আটকে" | Risky + Escalation |
| `legal_issue` | "আইনি ব্যবস্থা", "court-এ যাব" | Risky + Escalation |
| `scam_allegation` | "প্রতারণা", "ফাঁদ" | Risky |
| `abuse` | (abuse/harassment keywords) | Risky |
| `negative_comment` | (negative tone) | Risky |
| `media_only` | (image/video with no text) | Risky |
| `unclear` | (unknown language/insufficient text) | Risky |
| `employee_salary_complaint` | "আমার বেতন দেওয়া হয়নি" + employee context | Escalation |

**Comment-specific overrides (`classify_comment()`):**
- scam / complaint / abuse → `"negative_comment"` (unified)
- salary/payment keywords in comment context → `"comment_salary_redirect"` (not auto-sent, redirected to DM)

**Applicant info detection (`_looks_like_applicant_info()`):**
Checks for presence of 3 or more of: age/বয়স, education level (SSC/HSC/পাস), address/এলাকা, name/নাম. If 3+ present → classified as `applicant_info_complete`.

---

## Risk Classification

**File:** `risk_flagger.py`

| Risk Level | Intents | Behavior |
|---|---|---|
| **Safe auto-send** | 15 intents (see Safe column above) | Reply queued automatically |
| **Risky** | 16 intents (see Risky column above) | Stored in `social_flagged_items`, severity='manual_review', no auto-reply |
| **Escalation** | `employee_salary_complaint`, `legal_issue`, `payment_issue` | Stored in `social_flagged_items`, severity='escalation'; WARNING log emitted; admin MUST act |

**`can_auto_send(intent, platform, text)` rules:**
- Intent must be in `SAFE_AUTO_SEND_INTENTS`
- Platform must be: messenger, meta_whatsapp, or facebook_comment
- bridge1 / bridge2 messages are NOT auto-replied via this system

---

## Reply Rules and Templates

**File:** `reply_rules.py`
**Language:** Bangla (primary), English (comments only)

**Company identity used in replies:**
- Name: Al-Aqsa Security Service
- Office: ভিক্টোরিয়া গেইট, একে খান মোড়, পাহাড়তলী, চট্টগ্রাম
- Office hours: সকাল ১০টা থেকে বিকেল ৫টা
- Contact: 01958-122311, 01958-122327
- ADM WhatsApp (for escalation): 011004669

**Salary figures disclosed in auto-replies:**

| Period | Monthly Total | Breakdown |
|---|---|---|
| Training | No salary | Free accommodation; own food |
| Probation (3 months) | 17,000 BDT | 9,000 base + 2,000 rent + 2,000 attendance bonus + 2,000 incentive + 2,000 other |
| Permanent | 24,700 BDT | 12,000 base + 4,000 rent + 2,000 attendance bonus + 3,000 incentive + 3,700 other |

**Fee figures disclosed in auto-replies:**

| Fee | Amount | Purpose |
|---|---|---|
| Processing fee | 350 BDT | ID card, file processing, initial assignment |
| Training/joining fee | 3,500 BDT | Probation period entry, uniform, company benefits |
| Mess advance (optional) | 1,000 BDT | Adjusted against food costs later |

**Age issue reply:** Does NOT quote a fixed age range. States that interest and experience matter — experience can compensate for age. (This differs from the administrative BR-25 range of 18–55.)

---

## Planner Worker Logic

**File:** `planner_worker.py`
**COOLDOWN:** `SOCIAL_REPLY_COOLDOWN_M` env (default 15 minutes)
**Thread compression window:** `SOCIAL_REPLY_COMPRESSION_S` env (default 25 seconds)
**Thread limit per cycle:** `SOCIAL_PLANNER_THREAD_LIMIT` env (default 10)

**Per-sender decision flow:**

1. Lookup sender in FPE employee table via `employee_lookup.find_by_mobile(target_id)`.
   - If found: `role="EMPLOYEE"` added to queue meta.
   - If not found: `role="UNKNOWN"`.
   - Note: EMPLOYEE role is recorded in meta but does not currently block auto-reply — the system still processes the message if intent is a recruiting intent.
2. Wait 25s compression window — collect all events from the same sender.
3. Check per-sender intent cooldown: if same intent was sent within 15 minutes, skip (no duplicate reply).
4. Classify ALL events in thread. If ANY event is NOT a recruiting intent → no reply is queued for this thread.
5. Generate reply plan via `intelligent_generator.plan_reply(intent, channel, sender_meta)`.
6. Insert into `social_reply_queue` with `idempotency_key`.
7. Update `social_inbox_events.reply_status` → 'queued'.

---

## Rate Limiter

**File:** `rate_limiter.py`
**State table:** `social_rate_limit_state` (UNIQUE on channel)
**Window:** 1 hour sliding window (tracks `sent_count_window` and `window_start`)

**Delay between sends:**
- `SOCIAL_REPLY_DELAY_MIN_S` env → default 30 seconds
- `SOCIAL_REPLY_DELAY_MAX_S` env → default 45 seconds
- Random value in [low, high] chosen per send

**`can_send(channel)` returns True only if `NOW() >= next_allowed_at`.**

State is persistent across restarts (stored in DB). Rate limit state is per-channel (messenger rate limit does not affect meta_whatsapp rate limit).

---

## Send Queue

**File:** `send_queue.py`
**Max attempts:** 5 (configurable; `max_attempts` per queue row, default 5)
**Backoff:** Exponential — next retry at `NOW() + INTERVAL '1 minute' * (2^attempts)`

| Attempt | Retry delay |
|---|---|
| 1 | 2 minutes |
| 2 | 4 minutes |
| 3 | 8 minutes |
| 4 | 16 minutes |
| 5 | DLQ (status='dlq') |

**Concurrency safety:** `SELECT FOR UPDATE SKIP LOCKED` — multiple callers will not pick the same row.

**Per-platform send logic:**

| Platform | API Called |
|---|---|
| `messenger` | Meta Messenger API `/me/messages` |
| `facebook_comment` | Meta Graph API `/{comment_id}/comments` |
| `meta_whatsapp` | Meta WhatsApp Business API `/{phone_number_id}/messages` |
| `bridge1` | `get_bridge1().send_message()` |
| `bridge2` | `get_bridge2().send_message()` |

**Terminal statuses:**
- `PermissionError` from platform API → `status='blocked'` (NOT retried — permanent; Meta may have blocked the account)
- `attempts >= max_attempts` → `status='dlq'` (not retried; visible in `/api/social/queue?status=dlq`)

**On success:**
- Insert into `social_sent_log` (idempotency_key UNIQUE — safe to re-insert)
- Update `social_inbox_events.reply_status` → 'sent'
- Update `social_reply_queue.status` → 'sent'

---

## Database Tables

All 8 tables are created by `_INIT_SQL` in `__init__.py` on every startup. Tables are never modified by production code outside this module.

| Table | Purpose | Mutable? |
|---|---|---|
| `social_inbox_events` | All inbound events from all channels; UNIQUE on event_key | Yes (reply_status updates) |
| `social_reply_queue` | Outbound reply queue; UNIQUE on idempotency_key | Yes (status, attempts) |
| `social_sent_log` | Immutable sent record; UNIQUE on idempotency_key | No |
| `social_retry_queue` | Retry tracking per queue row; UNIQUE on queue_id | Yes |
| `social_flagged_items` | Risky and escalation items for admin review; status default 'open' | Yes (status, resolution) |
| `social_backlog_state` | Platform backlog cursor per platform | Yes (cursor advancement) |
| `social_rate_limit_state` | Per-channel rate limit state; UNIQUE on channel | Yes |
| `social_thread_state` | Per-sender conversation context; UNIQUE on platform+target_id | Yes |

**`social_inbox_events.reply_status` values:**
- `pending` — received, not yet processed
- `ignored` — not a recruiting intent; no reply will be sent
- `queued` — reply queued in social_reply_queue
- `sent` — reply successfully sent
- `needs_admin` — escalation intent; admin must handle
- `flagged` — risky intent; queued for human review
- `blocked` — sender blocked from receiving replies

**`social_reply_queue.status` values:**
- `pending` — waiting to be sent
- `sending` — being sent right now (brief intermediate state)
- `sent` — sent successfully
- `failed` — send failed; will retry
- `blocked` — permanently blocked by platform
- `dlq` — exceeded max attempts; dead-letter queue

---

## API Routes

All routes are under `/api/social/`. Authentication: `X-Internal-Key` header.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/social/status` | Daemon status: is_running, is_paused, queue_rows, flagged_rows, sent_count_24h |
| GET | `/api/social/queue` | List queued/pending replies (filterable by status, platform) |
| GET | `/api/social/flagged` | List flagged items (risky/escalation awaiting admin) |
| POST | `/api/social/pause` | Pause the daemon (daemon will sleep; no new replies will be sent) |
| POST | `/api/social/resume` | Resume the daemon after pause |
| POST | `/api/social/retry` | Manually retry a specific DLQ or failed queue row by ID |

---

## Failure Handling

| Failure | Behavior |
|---|---|
| Platform API rate limit | Rate limiter enforces 30–45s delay between sends. Additional 429 from platform → exponential backoff. |
| `PermissionError` from Meta API | Queue row permanently `blocked`. Admin must investigate account standing. |
| Max retry attempts (5) reached | Queue row → `dlq`. Visible in `/api/social/queue?status=dlq`. |
| Escalation intent detected | `social_inbox_events.reply_status='needs_admin'` + `social_flagged_items` row + WARNING log. No auto-reply. |
| Risky intent detected | `social_flagged_items` row (severity='manual_review'). No auto-reply. |
| Daemon paused | Heartbeat still runs. No backlog scan. No replies sent. |
| DB unavailable during heartbeat | WARNING log; loop continues. |
| All events in thread are non-recruiting | Thread skipped; events marked 'ignored'. |

---

## Channels and Platforms

| Platform | Direction | Auto-reply capable? | Notes |
|---|---|---|---|
| `messenger` | Inbound webhook + outbound `/me/messages` | Yes | 24h messaging window enforced by Meta |
| `facebook_comment` | Inbound webhook + outbound `/{comment_id}/comments` | Yes | Public — visible to all page followers |
| `meta_whatsapp` | Inbound webhook + outbound `/{phone_number_id}/messages` | Yes | Meta Business API credentials required |
| `bridge1` | Internal bridge | No auto-reply via this module | bridge1 events can be ingested but no auto-reply |
| `bridge2` | Internal bridge | No auto-reply via this module | bridge2 events can be ingested but no auto-reply |

---

## Conflict with Main WhatsApp Router

The social auto-reply system and the main WhatsApp message router are separate, non-conflicting systems. They target different channels and use different classification logic.

| | Social Auto-Reply | Main WhatsApp Router |
|---|---|---|
| Channels | Facebook Messenger, Facebook Comments, Meta WhatsApp Business API | Internal bridges (bridge1, bridge2) |
| Employee/admin messages | Not handled | Primary purpose |
| Classification | Deterministic keyword classifier (social-specific 30+ intents) | LLM-based intent classifier (9 safe-auto-send intents) |
| Safe auto-send intents | 15 intents | 9 intents |
| Rate limiting | Per-channel 30–45s random delay | WhatsApp bridge rate limited separately |
| Salary disclosure | Auto-replies include specific BDT figures | Recruitment flow managed separately |

**There is no routing overlap** — messages from bridge1/bridge2 are not processed by the social auto-reply module's send queue.

---

## Security and Visibility Notes

- All admin API routes require `X-Internal-Key`
- Sender identity is never auto-resolved to an employee record for reply purposes — only for meta annotation (role="EMPLOYEE" in queue meta)
- Public Facebook comments: auto-replies from this system are publicly visible to all page followers; this is by design
- `social_sent_log` is immutable — no API allows delete or update
- `social_flagged_items` escalation rows must be manually resolved by an admin via `/api/social/flagged`
- `SCHEDULER_ENABLED=false` does NOT pause this worker — it is a single asyncio task, not an APScheduler job. Use `POST /api/social/pause` to stop replies.

---

## Current vs Legacy / Uncertain Behavior

| Item | Status |
|---|---|
| EMPLOYEE role in queue meta | RECORDED — not currently used to block auto-reply. Behavioral impact unclear from code. |
| `fazle_service_heartbeats` table | Daemon writes to this. README mentions `fazle_bridge_heartbeats`. These may be the same table renamed or two separate tables — NOT verified. |
| backlog_processor.scan_recent() | Called every 600s. Exact scanning logic (platform cursors, date range) not fully traced. |
| salary_flow.py | Handles salary complaint escalation routing — exact flow not fully traced. |
| payment_issue_handler.py | Routes payment complaints — exact behavior not fully traced. |
| `social_thread_state` usage | Table exists. Whether thread context (previous intents, resolved) prevents re-classification not confirmed. |
| Facebook 24h window enforcement | Meta Messenger has a 24h window for standard messaging. Whether this system enforces this client-side is not confirmed from code. |
| `bridge1`/`bridge2` auto-reply | Platform code paths exist in send_queue.py but `can_auto_send()` in risk_flagger.py only permits messenger, meta_whatsapp, facebook_comment. Bridges cannot be auto-replied via this module. |

---

## Unresolved Questions

| # | Question | Impact |
|---|---|---|
| 1 | `fazle_service_heartbeats` vs `fazle_bridge_heartbeats` — **RESOLVED (Wave-2B, C-06):** These are two separate tables. `fazle_service_heartbeats` (Python inline DDL; written by social daemon) → owned by SYSTEM domain. `fazle_bridge_heartbeats` (FPE migration 009; tracks WhatsApp bridge liveness) → owned by MESSAGING domain. No conflict. Both exist in production. | Resolved |
| 2 | What does `social_thread_state` store and how is it used across ticks? | Context-aware reply suppression not confirmed |
| 3 | Can an EMPLOYEE sender be blocked from receiving social auto-replies? | Behavioral boundary unclear |
| 4 | Is `backlog_processor.scan_recent()` doing API calls to Meta or reading local DB only? | Resource and rate-limit implications |
| 5 | Are `salary_flow.py` and `payment_issue_handler.py` called during standard intent routing or only for specific escalation paths? | Completeness of escalation documentation |
| 6 | Does the system enforce Meta's 24h messaging window for Messenger? | Compliance risk |

---

## Management Decisions Affecting This System

| Decision | Status |
|---|---|
| Salary figures (17,000/24,700 BDT) disclosed in auto-replies | ACTIVE in production — not formally management-approved in KB |
| Fee disclosure (350/3,500/1,000 BDT) in auto-replies | ACTIVE in production — not formally management-approved in KB |
| Age issue handling (no fixed range in reply) | ACTIVE — diverges from BR-25 (18–55) for auto-reply context |
| 15-minute intent cooldown per sender | ACTIVE in production — env-configurable, not management-approved in KB |

---

## Traceability

| Knowledge Item | Source File | Source Function/Constant |
|---|---|---|
| 8 DB tables, SQL DDL | `__init__.py` | `_INIT_SQL` |
| Daemon tick loop | `daemon_worker.py` | `run()` |
| Idle sleep / backlog interval | `daemon_worker.py` | `SOCIAL_REPLY_IDLE_SLEEP_S`, `SOCIAL_BACKLOG_SCAN_EVERY_S` |
| Intent categories | `classifier.py` | `classify()`, `INTENT_KEYWORDS` |
| Comment-specific classification | `classifier.py` | `classify_comment()` |
| Applicant info detection | `classifier.py` | `_looks_like_applicant_info()` |
| Safe/risky/escalation sets | `risk_flagger.py` | `SAFE_AUTO_SEND_INTENTS`, `RISKY_INTENTS`, `ESCALATION_INTENTS` |
| Auto-send platform restriction | `risk_flagger.py` | `can_auto_send()` |
| Rate limit delay (30–45s) | `rate_limiter.py` | `_delay_seconds()` |
| Exponential backoff formula | `send_queue.py` | `next_retry_at = NOW() + INTERVAL '1 minute' * (2^attempts)` |
| SELECT FOR UPDATE SKIP LOCKED | `send_queue.py` | `sweep_once()` |
| PermissionError → blocked | `send_queue.py` | `_try_send()` |
| Employee role detection | `planner_worker.py` | `find_by_mobile(target_id)` |
| Intent cooldown (15 min) | `planner_worker.py` | `_COOLDOWN_MINUTES` |
| Thread compression (25s) | `planner_worker.py` | `compression_seconds` |
| Salary figures | `reply_rules.py` | `SALARY_REPLY` |
| Fee figures | `reply_rules.py` | `FEES_REPLY` |
| Office address | `reply_rules.py` | `LOCATION_REPLY` |
| ADM WhatsApp | `reply_rules.py` | `ADM_WHATSAPP = "011004669"` |
| Comment routing | `comment_handler.py` | `build_comment_decision()` |
| API endpoints | `routes.py` | Router mount |
| Heartbeat table | `daemon_worker.py` | `fazle_service_heartbeats` |

---

---

## Entity Ownership Notes (Wave-2B)

All 8 social tables (`social_*`) are owned by the **SOCIAL domain** per Entity Ownership Audit 2026-06-22. See `database_rules.md` Domain 8 for complete domain documentation.

`fazle_service_heartbeats` — written by the social daemon — is owned by the **SYSTEM domain** (not SOCIAL). The social daemon is a consumer/writer of this system-level table.

The SOCIAL domain is entirely separate from MESSAGING domain (WhatsApp). They share `fazle_knowledge_base` (AI domain) for RAG retrieval and `fazle_contact_roles` (IDENTITY domain) for role-based routing, but have no shared queue or delivery infrastructure.

---

## Related Articles

- `06_developer_system/database_rules.md` — Domain 8 (SOCIAL) and Domain 9 (AI — fazle_knowledge_base)
- `06_developer_system/automation_pipeline.md` — Main WhatsApp routing context
- `06_developer_system/rag_strategy.md` — Knowledge base retrieval used by both channels

---

## Wave-4 — Risk Flagger Exact Specification (W4-AUTH)

**Source:** `modules/social_auto_reply/risk_flagger.py` (read 2026-06-23)

### Exact Frozensets

```python
RISKY_INTENTS = {
    "accountant", "transaction", "employee_id", "reports_issue",
    "escort_order", "escort_client", "roster_issue", "internal_operations",
    "complaint", "payment_issue", "legal_issue", "scam_allegation",
    "abuse", "negative_comment", "media_only", "unclear",
}  # 16 members

SAFE_AUTO_SEND_INTENTS = {
    "greeting", "interested", "job_details", "salary", "salary_objection",
    "location", "age_issue", "documents", "fees", "applicant_info_complete",
    "training", "join_process", "recruitment_follow_up", "career_growth",
    "accommodation",
}  # 15 members

ESCALATION_INTENTS = {
    "employee_salary_complaint",
    "legal_issue",
    "payment_issue",
}  # 3 members

RECRUITING_INTENTS = SAFE_AUTO_SEND_INTENTS  # alias — same set
```

### `risk_reason()` — Full Logic

```python
def risk_reason(classification, *, media_flag=False, text="") -> str | None:
    if media_flag and not (text or "").strip():
        return "media_only"          # media with no text → risky
    if classification.intent in RISKY_INTENTS:
        return classification.intent # named risky intent
    return None                      # safe
```

**Key detail:** `media_flag=True` with no text body is independently risky, regardless of intent classification. This catches images/videos sent without caption.

### `can_auto_send()` — Full Logic

```python
def can_auto_send(classification, *, platform, text="") -> bool:
    if risk_reason(classification, text=text):
        return False
    if platform in {"messenger", "meta_whatsapp", "facebook_comment"}:
        return classification.intent in SAFE_AUTO_SEND_INTENTS
    return False
```

**Platform restriction:** Only the three listed platforms return True. Any other platform (including WhatsApp bridge1/bridge2) → `False`. The condition `platform not in set → return False` is the second gate.

### `is_escalation_intent()` — Full Logic

```python
def is_escalation_intent(intent: str) -> bool:
    return intent in ESCALATION_INTENTS
```

Called by the planner worker to route escalated items to `social_flagged_items` with severity `'escalation'` and emit a WARNING log.

---

## Wave-4 — State Tracker Details (W4-AUTH)

**Source:** `modules/social_auto_reply/state_tracker.py` (read 2026-06-23)

### Kill Switch

```python
def daemon_enabled() -> bool:
    return os.getenv("SOCIAL_AUTO_REPLY_ENABLED", "false").lower() in {"1", "true", "yes"}
```

Default: **disabled** (`false`). Must be explicitly set to enable the social daemon. `is_paused()` also returns `True` (paused) when `daemon_enabled()` is False.

### Pause Mechanism

Pause state is stored in `social_backlog_state` via UPSERT on `state_key='daemon_paused'`:

```json
{"paused": true/false, "reason": "manual"}
```

`is_paused()` reads this row. If no row exists → not paused. The UPSERT pattern means only one row per `state_key` ever exists (ON CONFLICT DO UPDATE).

### Status Function Output Fields

`status()` returns a dict with these fields — used by admin `/api/social/status` endpoint:

| Field | Source |
|---|---|
| `enabled` | `daemon_enabled()` |
| `paused` | `is_paused()` |
| `pending` | `COUNT(*) FROM social_reply_queue WHERE status='pending'` |
| `failed` | `COUNT(*) FROM social_reply_queue WHERE status='failed'` |
| `flagged_open` | `COUNT(*) FROM social_flagged_items WHERE status='open'` |
| `sent_24h` | `COUNT(*) FROM social_sent_log WHERE sent_at >= NOW() - INTERVAL '24 hours'` |
| `rate_limit` | Full row from `social_rate_limit_state WHERE channel='global'` |

### Tables Referenced by state_tracker

| Table | Purpose |
|---|---|
| `social_backlog_state` | Daemon pause state (state_key='daemon_paused') |
| `social_reply_queue` | Outbound reply queue (pending/failed counts) |
| `social_flagged_items` | Open items needing admin action |
| `social_sent_log` | 24h send count |
| `social_rate_limit_state` | Per-channel rate limit state |

---

## Wave-4 — Payment Complaint Path (W4-AUTH)

**Source:** `modules/social_auto_reply/payment_issue_handler.py` (read 2026-06-23)

The payment complaint path has exactly **two replies** (initial → escalation):

### Path 1 — Initial Reply (`initial_payment_reply()`)

Returns `reply_rules.PAYMENT_INFO_REQUEST_REPLY` — asks the sender to provide:
- Employee name
- Payment amount  
- Transaction date
- Contact number

This is a data-collection step before escalation.

### Path 2 — Escalation Reply (`escalation_reply()`)

Returns `reply_rules.PAYMENT_ESCALATION_REPLY` — informs the sender that the matter is being reported to the office and will be investigated.

The planner worker routes `payment_issue` intent to `social_flagged_items` (severity=`'escalation'`) in parallel with queuing the initial_payment_reply for sending. Admin must resolve the flagged item.

**Note:** `payment_issue` is in both `RISKY_INTENTS` AND `ESCALATION_INTENTS`. Its presence in `RISKY_INTENTS` means `can_auto_send()` returns False; despite this, `initial_payment_reply()` is still queued — the "safe for this specific intent" override is handled by the planner (the planner is aware that payment_issue gets a structured initial reply even though it can't be auto-sent without admin review in the general case).

---

## Revision History

| Date | Change | Author |
|---|---|---|
| 2026-06-22 | Wave-2A: Initial documentation created from production file read | KSP Wave-2A |
| 2026-06-22 | Wave-2B: Added Visibility Matrix, management sensitivity warning, resolved heartbeat table conflict (C-06), entity ownership notes | KSP Wave-2B |
| 2026-06-23 | Wave-4 (W4-AUTH): Added exact RISKY_INTENTS/SAFE_AUTO_SEND_INTENTS/ESCALATION_INTENTS frozensets, risk_reason() media_flag logic, can_auto_send() full logic, state_tracker kill switch + pause mechanism, payment complaint two-path detail | W4-AUTH |
