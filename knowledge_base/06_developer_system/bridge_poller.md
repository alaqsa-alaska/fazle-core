# Bridge Poller
**KB Article ID:** DEV-06-BRIDGE-POLLER
**Source:** `modules/bridge_poller/__init__.py` (read 2026-06-23)
**Visibility:** Developer only
**Certified:** 2026-06-23 (Wave-4, W4-AUTH)

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
message_router.process_message()
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
