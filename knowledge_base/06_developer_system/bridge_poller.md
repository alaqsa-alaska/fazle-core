---
title: Bridge Poller
owner: Fazle Core Admin
status: active
last_verified: 2026-06-24
runtime_index: true
---

# Bridge Poller
**KB Article ID:** DEV-06-BRIDGE-POLLER
**Source:** `modules/bridge_poller/__init__.py` (read 2026-06-23; ESX-WIRE update 2026-06-23 Session 10; routing update 2026-06-23 Session 11)
**Visibility:** Developer only
**Certified:** 2026-06-23 (Wave-4, W4-AUTH); updated Session 10/11

---

## Purpose

The bridge poller is the primary ingest mechanism for WhatsApp messages arriving at the two WhatsApp bridge instances. It polls SQLite message stores every 1–30 seconds (adaptive), persists every real DM to `wbom_whatsapp_messages`, and feeds the message processing pipeline.

---

## Ingest Policy v1.0.2 (Locked)

| Message Type | Treatment |
|---|---|
| DMs (`@s.whatsapp.net`) | **ALWAYS persisted** to `wbom_whatsapp_messages` before any router/draft logic. No early return drops a real DM silently. |
| Groups (`@g.us`) | SKIPPED ENTIRELY at SQL level — not persisted, no draft, no reply |
| Newsletters (`@newsletter`) | SKIPPED ENTIRELY at SQL level |
| `status@broadcast` | SKIPPED ENTIRELY at SQL level |
| LID-unresolved DMs | Persisted with `phone='unresolved:<lid>'` — no real inbound is lost; tracked in observability |
| Media type: reaction/receipt/revoke/deleted/protocol | SKIPPED |

**Ownership directive:** Group chats are out of scope by owner directive (2026-04-27). This is permanent policy, not a temporary limitation.

---

## Two Bridge Configurations

| Key | Bridge 1 | Bridge 2 |
|---|---|---|
| `name` | `"bridge1"` | `"bridge2"` |
| `messages_db` | `settings.bridge1_db_path` | `settings.bridge2_db_path` |
| `whatsapp_db` | `settings.bridge1_whatsapp_db_path` | `settings.bridge2_whatsapp_db_path` |
| `get_bridge` | `get_bridge1()` | `get_bridge2()` |

Bridge DB paths come from `.env` — can be overridden without code changes.

---

## Adaptive Poll Interval

| Constant | Value | Condition |
|---|---|---|
| `BRIDGE_POLL_MIN_S` | 1.0 s | Messages arriving |
| `BRIDGE_POLL_MAX_S` | 30.0 s | Sustained idle |
| `BRIDGE_POLL_BACKOFF` | 1.5× | Multiply sleep each consecutive idle iteration |
| `REPLY_COOLDOWN` | 60 s | Minimum seconds between replies to same number |
| `_SEND_GATE_CHECK_INTERVAL` | 300 s | Re-verify send-control every 5 min (survives bridge restart) |

---

## SQLite Query — DM Filter

Reads from the `messages` table in each bridge's SQLite DB:

```sql
SELECT id, chat_jid, sender, content, timestamp, media_type,
       processed_text, filename, url
FROM messages
WHERE is_from_me = 0
  AND datetime(timestamp) > datetime(:since)
  AND chat_jid NOT LIKE '%@newsletter'
  AND chat_jid != 'status@broadcast'
  AND chat_jid NOT LIKE '%@g.us'
  AND (media_type IS NULL
       OR media_type NOT IN ('reaction', 'receipt', 'revoke', 'deleted', 'protocol'))
  AND (content IS NOT NULL OR processed_text IS NOT NULL
       OR ((media_type IS NOT NULL) AND (filename IS NOT NULL OR url IS NOT NULL)))
ORDER BY datetime(timestamp) ASC
LIMIT 50
```

**Read-only access:** `file:{db}?mode=ro` — bridge SQLite DBs are never modified.

---

## Cursor Management

**Tables:**
- `bridge_poller_cursor` — stores `(bridge, last_ts)` per bridge
- `bridge_poller_cursor` with bridge key `"{bridge}:outgoing"` — stores last processed outgoing timestamp

**Inbound cursor:** Updated after each successful message batch. On fresh start (no cursor): starts from `NOW() - 5 minutes` to catch very recent messages without replaying all history.

**Outgoing cursor:** Same table, different key. Used for scanning outbound admin messages containing `[RELEASE CONFIRMED]`.

---

## LID Resolution

WhatsApp business accounts use LID (Linked ID) instead of phone numbers. The bridge poller reads the `whatsmeow_lid_map` table from the bridge's `whatsapp.db` file to resolve `lid → phone`.

**Fallback:** If sender LID has no mapping, `chat_jid` is checked (if it's a `@s.whatsapp.net` JID, the phone is extracted directly). If still unresolvable: stored as `phone = "unresolved:{lid}"`.

---

## Deduplication Tables

| Table | PK | Purpose |
|---|---|---|
| `processed_bridge_messages` | `(message_id, bridge)` | Dedup for inbound messages (safety net, handles cursor edge cases) |
| `processed_outgoing_escort_messages` | `(message_id, bridge)` | Dedup for outgoing RELEASE CONFIRMED scans |

---

## Image and OCR Eligibility

Files are eligible for OCR if:

| Parameter | Constraint |
|---|---|
| Extensions | `.jpg`, `.jpeg`, `.png`, `.webp` |
| Min size | 1,024 bytes |
| Max size | 8 MB (8 × 1024 × 1024 bytes) |

Images outside these bounds are skipped with a logged reason (`unsupported_ext`, `file_missing`, `too_small`, `too_large`).

---

## Image OCR Pipeline (ESX-WIRE — Session 10)

**Authorization:** Session 10, ESX-WIRE (management_decisions.md)

Eligible images enter a two-step pipeline:

```
Eligible image file
    ↓
STEP 1 — classify_from_context(text, media_type)
    Lightweight: checks surrounding text for slip signals.
    Result: "probable_slip" | other
    ↓ (probable_slip only)
STEP 2 — escort_slip_extractor.extract_escort_slip(file_path, source_label)
    3-pass OCR merge (media-processor + tesseract CLI + ImageMagick-preprocessed)
    Returns: EscortSlipResult (TypedDict)
    ↓
completion_date is not None?
    ├── YES → TWO-DATE RULE: supervisor stamped second date → Release Slip
    │       Translate EscortSlipResult → compat dict
    │       → escort_lifecycle.handle_ocr_release_slip(compat, source, phone)
    │       → Admin notified via bridge2 admin number
    │       → escort_reply sent back to sender
    │
    ├── NO (doc_type==unknown OR conf_pct < 10) → Unknown document
    │       If sender has active escort program → notify admin, ask for clearer photo
    │       If no active program → skip (continue)
    │
    └── NO (assignment slip, not release) → use raw_ocr_text for normal routing
```

### EscortSlipResult → compat dict translation

`handle_ocr_release_slip()` expects a specific dict shape. The translation layer:

| EscortSlipResult field | compat dict key | Notes |
|---|---|---|
| `escort_name` | `employee_name` | |
| `lighter_vessel` or `mother_vessel` | `vessel` | lighter preferred |
| `completion_date` | `date` | the release date (second date) |
| `release_place` | `location` | |
| `confidence` × 100 | `confidence_score` | 0–100 int |
| `raw_ocr_text` | `raw_text` | |
| `master_mobile` | `master_mobile` | extra field |
| `start_date` | `start_date` | extra field |
| hardcoded `"release_slip"` | `slip_type` | **required** — function returns None if missing |

### TWO-DATE RULE (critical)

The physical escort slip is **identical** for assignment and release. Only the presence of a second (completion) date distinguishes them:
- ONE date (`completion_date is None`) → assignment slip → ongoing duty
- TWO dates (`completion_date is not None`) → supervisor stamped release → duty completed

This is a structural detector — no keyword matching needed.

### Escort reply text (bangla) on successful release

- High confidence, no missing fields: `"✅ রিলিজ স্লিপ পাওয়া গেছে। অ্যাডমিন যাচাই করে পেমেন্ট অনুমোদন করবেন।"`
- Missing fields or conf < 60%: `"✅ আপনার রিলিজ স্লিপ পাওয়া গেছে। কিছু তথ্য স্পষ্ট নয়। অ্যাডমিন যাচাই করছেন…"`

### Save behavior

`extract_escort_slip()` saves to `escort_slip_extractions` table automatically (default: `save_to_db=True`).

---

## Social Daemon Routing (SOCIAL_AUTO_REPLY_SINGLE_ENGINE)

**Controlled by:** `os.getenv("SOCIAL_AUTO_REPLY_SINGLE_ENGINE", "true")` — read per-call, not cached.

**Current production setting (Session 11):** `true`

When `true` and social daemon heartbeat is fresh (< 300 s):

```
Inbound DM
    ↓
social_auto_reply.ingest_social_event(platform=bridge_name, ...)
    ↓ saved to social_inbox_events
intent classification (classify_message)
    ├── escalation_intent → status='needs_admin'
    ├── NOT recruiting_intent → status='ignored' (NO reply sent)
    └── recruiting_intent → proceeds to social daemon reply pipeline
    ↓
continue   ← legacy router SKIPPED for this message
```

When `true` but social daemon heartbeat is STALE (> 300 s):
- Logs error: `"[social] daemon heartbeat stale >300s — falling through to legacy router"`
- Falls through to `process_bridge_inbound()` (legacy path, full intent routing)

When `false`:
- Skips social daemon entirely
- Goes directly to `process_bridge_inbound()` for all messages

---

## Outgoing Message Scan — `[RELEASE CONFIRMED]`

A second scan runs on outgoing messages (`is_from_me = 1`) to detect admin `[RELEASE CONFIRMED]` messages. When found, `escort_lifecycle.handle_admin_release_confirmation()` is called to close the escort program and create attendance + settlement records.

Managed by a separate cursor key (`"{bridge}:outgoing"`) and dedup table (`processed_outgoing_escort_messages`).

---

## Outbound Safety Incidents

Messages blocked by the outbound send-gate are recorded in `outbound_safety_incidents`:

| Column | Notes |
|---|---|
| `ts` | Timestamp |
| `recipient` | Intended recipient phone |
| `bridge` | Bridge that would have sent |
| `blocked_reason` | String explanation |
| `message_preview` | First N chars of message |
| `queue_id` | Queue row ID if applicable |
| `source_module` | `'bridge_poller'` |

---

## Key PostgreSQL Tables

| Table | Purpose |
|---|---|
| `bridge_poller_cursor` | Persistent read cursor (inbound + outgoing) per bridge |
| `processed_bridge_messages` | Inbound dedup safety net |
| `processed_outgoing_escort_messages` | Outgoing dedup for RELEASE CONFIRMED scan |
| `outbound_safety_incidents` | Blocked send audit log |
| `wbom_whatsapp_messages` | All persisted DMs (destination of ingest) |

---

## Processing Pipeline (per inbound DM)

```
SQLite poll
    ↓
LID → phone resolution
    ↓
processed_bridge_messages dedup check → skip if already seen
    ↓
wbom_whatsapp_messages INSERT (ALWAYS for DMs)
    ↓
contact_sync.upsert_contact_from_message()
    ↓
Is message an image? (eligible OCR file)
    ├── YES → ESX-WIRE image path (see "Image OCR Pipeline" section above)
    │          classify_from_context() → extract_escort_slip() → handle_ocr_release_slip()
    │          result text set; falls through to routing below
    │
    └── NO → text message, proceed as-is
    ↓
SOCIAL_AUTO_REPLY_SINGLE_ENGINE enabled AND social daemon healthy?
    ├── YES → ingest_social_event() → social daemon handles reply → SKIP legacy path
    │
    └── NO → process_bridge_inbound() (legacy message_router path)
                ↓
            result → draft creation or auto-send via outbound queue
```

---

## Observability Counters

| Metric | Labels |
|---|---|
| `messages_skipped_total` | `reason=group\|newsletter\|status` |
| `bridge_poll_total` | per bridge |
| `bridge_poll_errors_total` | per bridge |

---

## Cross-References

- `escort_workflow.md` — `[RELEASE CONFIRMED]` message processed from outgoing scan
- `contact_sync.md` — `upsert_contact_from_message()` called per inbound DM
- `workflow_engine.md` — `process_message()` is Step 1 of routing
- `automation_pipeline.md` — outbound queue consumed by `outbound.sweep_once()`
- `distributed_architecture.md` — bridge orchestrator tracks bridge health; bridge poller feeds from same SQLite
