"""
Fazle Core — Bridge SQLite Poller (Phase 3B)

Polls both bridge SQLite message stores every 5 seconds.
Pipeline: inbound SQLite row → LID→phone resolve → dedup check
          → classify intent → Ollama reply → bridge send API → checkpoint

Design choices:
- Read-only SQLite access (uri=?mode=ro) — never writes to bridge DBs
- Timestamp cursor stored in PostgreSQL `bridge_poller_cursor` (persists across restarts)
- Dedup table `processed_bridge_messages` as safety net (handles cursor edge cases)
- On fresh start (no cursor): begins from NOW() — avoids replying to historical messages
- SQLite queries run in thread pool (sync) so asyncio loop is never blocked

Ingest policy (v1.0.2 — locked):
- DMs (@s.whatsapp.net): ALWAYS persisted to wbom_whatsapp_messages before
  any router/draft logic. No early return drops a real DM silently.
- Groups (@g.us), newsletters (@newsletter), status@broadcast: SKIPPED ENTIRELY
  at SQL level — not persisted, no draft, no reply. Group chats are out of
  scope for this engine by owner directive (2026-04-27).
- LID-unresolved DMs are persisted with phone='unresolved:<lid>' so no real
  inbound is lost; counted via observability for alerting.
- Auto-reply remains gated by AUTO_REPLY_ENABLED + DRAFT_QUALITY_GATE.
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import sqlite3
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

from app.config import get_settings
from app.database import execute, fetch_one, fetch_all, fetch_val
from shared.draft_reply import create_draft_reply
from shared.sqlite_compat import has_column
from shared.media_text import has_meaningful_text, is_media_placeholder
from app.bridge import get_bridge1, get_bridge2, get_bridge3
from modules.message_router import (
    process_message,
    get_primary_admin,
    _is_safe_autosend_intent,
    SYSTEM_NOTIFICATION_TITLE_PREFIXES,
)
from modules.intent import classify as classify_intent
from modules.recruitment_flow import recruitment_eligibility

_settings = get_settings()

log = logging.getLogger("fazle.bridge_poller")

POLL_INTERVAL = 5  # seconds — used only for send-gate period calculation
REPLY_COOLDOWN = 60  # minimum seconds between replies to same number
_SEND_GATE_CHECK_INTERVAL = 300  # re-verify send-control every 5 min (survives bridge restart)

BRIDGE_POLL_MIN_S   = 1.0   # poll interval when messages are arriving
BRIDGE_POLL_MAX_S   = 30.0  # poll interval during sustained idle
BRIDGE_POLL_BACKOFF = 1.5   # multiply sleep by this each consecutive idle iteration

# Per-bridge SQLite paths — resolved from settings so .env overrides work.
BRIDGE_CONFIGS = [
    {
        "name": "bridge1",
        "messages_db": _settings.bridge1_db_path,
        "whatsapp_db": _settings.bridge1_whatsapp_db_path,
        "get_bridge": get_bridge1,
    },
    {
        "name": "bridge2",
        "messages_db": _settings.bridge2_db_path,
        "whatsapp_db": _settings.bridge2_whatsapp_db_path,
        "get_bridge": get_bridge2,
    },
    {
        "name": "bridge3",
        "messages_db": _settings.bridge3_db_path,
        "whatsapp_db": _settings.bridge3_whatsapp_db_path,
        "get_bridge": get_bridge3,
    },
]

# ── Schema ─────────────────────────────────────────────────────────────────────

_INIT_SQL = """
CREATE TABLE IF NOT EXISTS bridge_poller_cursor (
    bridge      TEXT PRIMARY KEY,
    last_ts     TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS processed_bridge_messages (
    message_id  TEXT    NOT NULL,
    bridge      TEXT    NOT NULL,
    phone       TEXT,
    processed_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (message_id, bridge)
);

CREATE INDEX IF NOT EXISTS idx_pbm_bridge_ts
    ON processed_bridge_messages (bridge, processed_at DESC);

CREATE TABLE IF NOT EXISTS processed_outgoing_escort_messages (
    message_id   TEXT    NOT NULL,
    bridge       TEXT    NOT NULL,
    processed_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (message_id, bridge)
);

CREATE TABLE IF NOT EXISTS outbound_safety_incidents (
    id              BIGSERIAL   PRIMARY KEY,
    ts              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    recipient       TEXT        NOT NULL,
    bridge          TEXT        NOT NULL,
    blocked_reason  TEXT        NOT NULL,
    message_preview TEXT,
    queue_id        TEXT,
    source_module   TEXT        NOT NULL DEFAULT 'bridge_poller'
);
CREATE INDEX IF NOT EXISTS idx_osi_ts
    ON outbound_safety_incidents (ts DESC);
CREATE INDEX IF NOT EXISTS idx_osi_recipient
    ON outbound_safety_incidents (recipient, ts DESC);
"""


async def init_tables():
    for stmt in _INIT_SQL.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            await execute(stmt)
    log.info("Bridge poller tables ready")


# ── Cursor helpers ─────────────────────────────────────────────────────────────

async def _get_cursor(bridge: str) -> datetime:
    """Return the last processed timestamp for this bridge.
    Defaults to NOW() on first run — avoids blasting old messages."""
    row = await fetch_one(
        "SELECT last_ts FROM bridge_poller_cursor WHERE bridge = $1", bridge
    )
    if row and row["last_ts"]:
        ts = row["last_ts"]
        # asyncpg returns timezone-aware datetime already
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts
    # First run — start from 5 minutes ago to catch very recent messages
    now = datetime.now(timezone.utc) - timedelta(minutes=5)
    await _set_cursor(bridge, now)
    return now


async def _set_cursor(bridge: str, ts: datetime):
    await execute(
        """
        INSERT INTO bridge_poller_cursor (bridge, last_ts) VALUES ($1, $2)
        ON CONFLICT (bridge) DO UPDATE SET last_ts = EXCLUDED.last_ts
        """,
        bridge, ts,
    )


async def _get_outgoing_cursor(bridge: str) -> datetime:
    """Return the last processed outgoing timestamp for this bridge.
    Defaults to NOW()-5min on first run to pick up recent admin completions."""
    row = await fetch_one(
        "SELECT last_ts FROM bridge_poller_cursor WHERE bridge = $1",
        f"{bridge}:outgoing",
    )
    if row and row["last_ts"]:
        ts = row["last_ts"]
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts
    now = datetime.now(timezone.utc) - timedelta(minutes=5)
    await _set_outgoing_cursor(bridge, now)
    return now


async def _set_outgoing_cursor(bridge: str, ts: datetime):
    await execute(
        """
        INSERT INTO bridge_poller_cursor (bridge, last_ts) VALUES ($1, $2)
        ON CONFLICT (bridge) DO UPDATE SET last_ts = EXCLUDED.last_ts
        """,
        f"{bridge}:outgoing", ts,
    )


def _is_system_generated_notification(text: str) -> bool:
    """Escort-pipeline audit, Root Cause 7 (2026-07-19): reject any
    outgoing message this app generated itself before treating it as a
    candidate admin confirmation.

    2026-07-27 (Owner directive): the previous origin marker -- an
    invisible zero-width-space appended to every outbound message body by
    app.bridge.BridgeClient.send()/send_strict() -- has been removed
    outright. No footer, marker, or hidden character may be appended to
    any outbound message body, full stop. This function now relies solely
    on known system-notification title prefixes
    (SYSTEM_NOTIFICATION_TITLE_PREFIXES): every template this app uses to
    notify the admin (draft-review messages, release-slip review, OCR
    alerts, safety-filter blocks, the pending-drafts list, Route B
    Ollama-failure alerts, etc.) starts with a fixed, recognizable prefix
    a human would never type verbatim. A message this app auto-sends is
    recognized by its known shape, not by an invisible marker planted in
    the body. Any new admin-notification template MUST add its own prefix
    to that list in the same change, or it becomes invisible to this
    guard -- see the maintenance note above the list definition."""
    if not text:
        return False
    stripped = text.lstrip()
    return any(stripped.startswith(prefix) for prefix in SYSTEM_NOTIFICATION_TITLE_PREFIXES)


async def _is_outgoing_processed(msg_id: str, bridge: str) -> bool:
    val = await fetch_val(
        "SELECT 1 FROM processed_outgoing_escort_messages WHERE message_id=$1 AND bridge=$2",
        msg_id, bridge,
    )
    return val is not None


async def _mark_outgoing_processed(msg_id: str, bridge: str):
    await execute(
        """
        INSERT INTO processed_outgoing_escort_messages (message_id, bridge)
        VALUES ($1, $2)
        ON CONFLICT DO NOTHING
        """,
        msg_id, bridge,
    )


# ── Dedup helpers ──────────────────────────────────────────────────────────────

async def _is_processed(msg_id: str, bridge: str) -> bool:
    val = await fetch_val(
        "SELECT 1 FROM processed_bridge_messages WHERE message_id=$1 AND bridge=$2",
        msg_id, bridge,
    )
    return val is not None


async def _mark_processed(msg_id: str, bridge: str, phone: str):
    await execute(
        """
        INSERT INTO processed_bridge_messages (message_id, bridge, phone)
        VALUES ($1, $2, $3)
        ON CONFLICT DO NOTHING
        """,
        msg_id, bridge, phone,
    )


_IMAGE_OCR_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
_IMAGE_OCR_MIN_BYTES = 1024
_IMAGE_OCR_MAX_BYTES = 8 * 1024 * 1024


def _is_lightweight_image_eligible_for_ocr(file_path: str, filename: str) -> tuple[bool, str]:
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in _IMAGE_OCR_EXTENSIONS:
        return False, f"unsupported_ext:{ext or 'none'}"
    if not os.path.isfile(file_path):
        return False, "file_missing"
    size = os.path.getsize(file_path)
    if size < _IMAGE_OCR_MIN_BYTES:
        return False, f"too_small:{size}"
    if size > _IMAGE_OCR_MAX_BYTES:
        return False, f"too_large:{size}"
    return True, f"eligible:size={size}:ext={ext}"


# ── SQLite fetch (sync — runs in thread pool) ─────────────────────────────────

def _load_lid_map(whatsapp_db: str) -> dict[str, str]:
    """Load lid→phone mapping from whatsapp.db."""
    lid_map: dict[str, str] = {}
    try:
        con = sqlite3.connect(f"file:{whatsapp_db}?mode=ro", uri=True, check_same_thread=False, timeout=5.0)
        rows = con.execute("SELECT lid, pn FROM whatsmeow_lid_map").fetchall()
        con.close()
        for lid, pn in rows:
            lid_map[str(lid)] = str(pn)
    except Exception as e:
        log.warning(f"LID map load error ({whatsapp_db}): {e}")
    return lid_map


def _fetch_new_messages(
    messages_db: str,
    whatsapp_db: str,
    since_ts: datetime,
    approved_group_jids: tuple = (),
) -> tuple[list[dict], datetime]:
    """
    Fetch inbound personal messages newer than since_ts from SQLite.
    Returns (messages, max_ts_seen).
    Runs synchronously — call via run_in_executor.

    approved_group_jids (Stage E3, 2026-08-28): the blanket "@g.us
    excluded" rule (Owner directive 2026-04-27, see this module's own
    docstring) still applies to every group EXCEPT the explicit JIDs in
    this tuple -- sourced only from modules.group_identity.
    list_approved_group_jids() (business_label IS NOT NULL), never a
    literal hand-typed here. Empty tuple (the default, and every caller
    except _poll_bridge's own live call site) reproduces the exact prior
    behavior byte-for-byte -- no group ever gets through.
    """
    messages: list[dict] = []
    max_ts = since_ts
    bridge_tag = "bridge1" if "/whatsapp1/" in messages_db else ("bridge2" if "/whatsapp2/" in messages_db else "unknown")
    skipped_no_text = 0
    unresolved_lid = 0

    try:
        # Use read-only URI mode — never modify bridge DBs
        con = sqlite3.connect(f"file:{messages_db}?mode=ro", uri=True, check_same_thread=False, timeout=5.0)
        con.row_factory = sqlite3.Row

        # `processed_text` is a legacy in-house OCR/STT column that is NOT part
        # of the stock bridge schema. A store created by the current bridge
        # binary (e.g. bridge1 after a re-pair) lacks it, while older
        # bridge2/bridge3 stores still have it. Detect it per-DB and degrade to
        # NULL rather than throwing `no such column` on every poll. Read-only.
        _has_processed_text = has_column(con, "messages", "processed_text")
        _pt_select = "processed_text" if _has_processed_text else "NULL AS processed_text"
        _pt_where = "OR processed_text IS NOT NULL" if _has_processed_text else ""

        # Normalize cursor boundary to UTC so bridge-local timezone offsets
        # cannot cause false "older/newer" comparisons in SQLite filtering.
        ts_utc = since_ts.astimezone(timezone.utc).replace(microsecond=0)
        ts_iso = ts_utc.isoformat(sep=" ")

        _query_params: dict = {"since": ts_iso}
        _group_exception_sql = "0"  # SQLite: always-false placeholder when no group is approved
        if approved_group_jids:
            _placeholders = []
            for i, jid in enumerate(approved_group_jids):
                key = f"approved_jid_{i}"
                _query_params[key] = jid
                _placeholders.append(f":{key}")
            _group_exception_sql = f"chat_jid IN ({', '.join(_placeholders)})"

        rows = con.execute(
            f"""
            SELECT id, chat_jid, sender, content, timestamp, media_type, {_pt_select},
                   filename, url
            FROM messages
            WHERE is_from_me = 0
              AND datetime(timestamp) > datetime(:since)
              AND chat_jid NOT LIKE '%@newsletter'
              AND chat_jid != 'status@broadcast'
              AND (chat_jid NOT LIKE '%@g.us' OR {_group_exception_sql})
              AND (media_type IS NULL
                   OR media_type NOT IN ('reaction', 'receipt', 'revoke', 'deleted', 'protocol'))
              AND (
                content IS NOT NULL
                {_pt_where}
                OR ((media_type IS NOT NULL) AND (filename IS NOT NULL OR url IS NOT NULL))
              )
            ORDER BY datetime(timestamp) ASC
            LIMIT 50
            """,
            _query_params,
        ).fetchall()

        # v1.0.2: visibility on what we filter out (groups/newsletters/status)
        # Stage E3: an approved group is genuinely persisted, not skipped
        # -- excluded from the "groups" skip count so this metric stays
        # accurate (same _group_exception_sql/_query_params as the main
        # query above).
        try:
            skipped_row = con.execute(
                f"""
                SELECT
                  SUM(CASE WHEN chat_jid LIKE '%@g.us' AND NOT ({_group_exception_sql}) THEN 1 ELSE 0 END) AS groups,
                  SUM(CASE WHEN chat_jid LIKE '%@newsletter' THEN 1 ELSE 0 END) AS newsletters,
                  SUM(CASE WHEN chat_jid = 'status@broadcast' THEN 1 ELSE 0 END) AS status
                FROM messages
                WHERE is_from_me = 0
                  AND datetime(timestamp) > datetime(:since)
                """,
                _query_params,
            ).fetchone()
            if skipped_row:
                from modules import observability as _obs
                bn = messages_db  # bridge identifier (full path is unique)
                if skipped_row["groups"]:
                    _obs.inc("messages_skipped_total", value=float(skipped_row["groups"]),
                             labels={"reason": "group", "db": bn})
                if skipped_row["newsletters"]:
                    _obs.inc("messages_skipped_total", value=float(skipped_row["newsletters"]),
                             labels={"reason": "newsletter", "db": bn})
                if skipped_row["status"]:
                    _obs.inc("messages_skipped_total", value=float(skipped_row["status"]),
                             labels={"reason": "status", "db": bn})
        except Exception as _sk_err:
            log.debug(f"skipped-count query failed: {_sk_err}")
        con.close()

        lid_map = _load_lid_map(whatsapp_db)

        for row in rows:
            msg = dict(row)

            # Resolve sender LID → phone
            sender_lid = str(msg.get("sender", "")).split(":")[0].split("@")[0]
            phone = lid_map.get(sender_lid, "")

            if not phone:
                # Fallback: try chat_jid if it's a s.whatsapp.net JID
                chat_jid = msg.get("chat_jid", "")
                if chat_jid.endswith("@s.whatsapp.net"):
                    phone = chat_jid.replace("@s.whatsapp.net", "")
                else:
                    # v1.0.2: do NOT silently drop. Tag and let downstream persist.
                    phone = f"unresolved:{sender_lid}" if sender_lid else "unresolved:unknown"
                    unresolved_lid += 1
                    try:
                        from modules import observability as _obs
                        _obs.inc("messages_lid_unresolved_total",
                                 labels={"db": messages_db})
                    except Exception:
                        pass
                    log.warning(f"LID unresolved → tagging phone={phone} jid={chat_jid}")

            # Text: prefer content, fallback to processed_text (STT/OCR).
            # Keep media-only rows as placeholders to avoid raw->normalized loss.
            text = (msg.get("content") or msg.get("processed_text") or "").strip()
            media_type = str(msg.get("media_type") or "").strip().lower()
            has_media_ref = bool(msg.get("filename") or msg.get("url"))
            if not text:
                if media_type and has_media_ref:
                    text = f"[media:{media_type}]"
                else:
                    skipped_no_text += 1
                    continue

            # Parse timestamp for cursor update
            try:
                ts_str = msg["timestamp"]
                # Python 3.7+ fromisoformat handles +HH:MM offsets
                ts_dt = datetime.fromisoformat(ts_str)
                if ts_dt.tzinfo is None:
                    ts_dt = ts_dt.replace(tzinfo=timezone.utc)
                if ts_dt > max_ts:
                    max_ts = ts_dt
            except Exception as _ts_err:
                log.debug(f"ts parse error for msg {msg.get('id')}: {_ts_err}")

            msg["phone"] = phone
            msg["text"] = text
            messages.append(msg)

        if bridge_tag == "bridge1" and (rows or unresolved_lid or skipped_no_text):
            log.info(
                "[GAP_DIAG] bridge=%s batch_candidates=%d persisted_candidates=%d unresolved_lid=%d skipped_empty=%d lid_map_size=%d since=%s",
                bridge_tag,
                len(rows),
                len(messages),
                unresolved_lid,
                skipped_no_text,
                len(lid_map),
                ts_iso,
            )

    except Exception as e:
        log.error(f"SQLite fetch error ({messages_db}): {e}")

    return messages, max_ts


def _fetch_image_context_rows(messages_db: str, chat_jid: str, timestamp) -> list:
    """
    Fetch the last 5 prior inbound messages' content for image OCR context
    classification. Runs synchronously — call via asyncio.to_thread().
    """
    con = sqlite3.connect(f"file:{messages_db}?mode=ro", uri=True, check_same_thread=False, timeout=3.0)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            """SELECT content FROM messages
               WHERE chat_jid = ? AND is_from_me = 0
                 AND datetime(timestamp) < datetime(?)
                 AND content IS NOT NULL
               ORDER BY datetime(timestamp) DESC LIMIT 5""",
            (chat_jid, timestamp),
        ).fetchall()
        return rows
    finally:
        con.close()


def _fetch_outgoing_escort_completions(
    messages_db: str,
    since_ts: datetime,
    trusted_group_jid: str = "",
) -> tuple[list[dict], datetime]:
    """
    Fetch outgoing (is_from_me=1) DM messages from bridge2 SQLite newer than
    since_ts. Only returns messages that look like completed escort slips so
    that handle_admin_escort_completion() can be called on them.

    These messages are sent BY the admin TO clients via bridge2, so they never
    appear in the inbound (is_from_me=0) query above. We need a separate cursor
    for these so we don't re-process on every poll.

    trusted_group_jid (2026-07-22, Owner-approved Trusted Group source):
    when set (Settings.escort_order_group_jid), one narrow, explicit
    exception is added to the blanket "@g.us excluded" filter below -- this
    exact group JID only. Every other group stays excluded exactly per the
    pre-existing 2026-04-27 "no groups" policy; empty (default) changes
    nothing at all.
    """
    messages: list[dict] = []
    max_ts = since_ts

    try:
        con = sqlite3.connect(f"file:{messages_db}?mode=ro", uri=True, check_same_thread=False, timeout=5.0)
        con.row_factory = sqlite3.Row
        ts_iso = since_ts.isoformat()

        rows = con.execute(
            """
            SELECT id, chat_jid, content, timestamp
            FROM messages
            WHERE is_from_me = 1
              AND datetime(timestamp) > datetime(:since)
              AND (chat_jid NOT LIKE '%@g.us' OR chat_jid = :trusted_group_jid)
              AND content IS NOT NULL
            ORDER BY datetime(timestamp) ASC
            LIMIT 100
            """,
            {"since": ts_iso, "trusted_group_jid": trusted_group_jid or ""},
        ).fetchall()
        con.close()

        for row in rows:
            msg = dict(row)
            text = (msg.get("content") or "").strip()
            if not text:
                continue

            try:
                ts_dt = datetime.fromisoformat(msg["timestamp"])
                if ts_dt.tzinfo is None:
                    ts_dt = ts_dt.replace(tzinfo=timezone.utc)
                if ts_dt > max_ts:
                    max_ts = ts_dt
            except Exception as _ts_err:
                log.debug(f"outgoing ts parse error {msg.get('id')}: {_ts_err}")

            msg["text"] = text
            messages.append(msg)

    except Exception as e:
        log.error(f"SQLite outgoing fetch error ({messages_db}): {e}")

    return messages, max_ts


# ── Reply cooldown (in-memory per process) ────────────────────────────────────
_last_reply: dict[str, float] = {}  # phone → unix timestamp of last reply (in-memory fallback)


async def _redis_cooldown_can_reply(phone: str):
    """Check Redis cooldown. Returns True=can reply, False=blocked, None=Redis unavailable."""
    try:
        import redis.asyncio as aioredis
        from app.config import get_settings
        r = aioredis.from_url(get_settings().redis_url, socket_connect_timeout=1)
        exists = await r.exists(f"fazle:cooldown:{phone}")
        await r.aclose()
        return not bool(exists)
    except Exception as exc:
        log.warning("[bridge_poller] Redis cooldown check failed, in-memory fallback: %s", exc)
        return None


async def _redis_cooldown_set(phone: str) -> bool:
    """Write Redis cooldown key. Returns False if Redis unavailable."""
    try:
        import redis.asyncio as aioredis
        from app.config import get_settings
        r = aioredis.from_url(get_settings().redis_url, socket_connect_timeout=1)
        await r.set(f"fazle:cooldown:{phone}", "1", ex=REPLY_COOLDOWN)
        await r.aclose()
        return True
    except Exception as exc:
        log.warning("[bridge_poller] Redis cooldown set failed, in-memory fallback: %s", exc)
        return False

# ── STEP 7: Loop detection ─────────────────────────────────────────────────────
_reply_ts_window: dict[str, list] = {}    # phone → recent reply timestamps
_loop_paused_until: dict[str, float] = {} # phone → pause-until unix timestamp
_LOOP_MAX_REPLIES = 3      # trigger if this many replies...
_LOOP_WINDOW_SECS = 120    # ...occur within this sliding window (2 minutes)
_LOOP_PAUSE_SECS = 600     # pause autoreply for 10 minutes after trigger

# ── Part I: Keyword flood protection ──────────────────────────────────────────
# Tracks: phone → {keyword_lower: [timestamps]}
_kw_flood_ts: dict[str, dict[str, list]] = {}
_KW_FLOOD_LIMIT = 3       # same keyword > this many times...
_KW_FLOOD_WINDOW_SECS = 300  # ...within 5 minutes triggers flood block
_KW_FLOOD_PAUSE_SECS = 900   # 15 minute silence after flood detected


def _check_keyword_flood(phone: str, text: str) -> bool:
    """Return True if this phone is spamming the same keyword (Part I)."""
    import time as _t
    now = _t.time()
    words = set(text.lower().split())
    flood_map = _kw_flood_ts.setdefault(phone, {})
    for word in words:
        if len(word) < 4:
            continue  # ignore short words
        ts_list = flood_map.setdefault(word, [])
        # Prune old entries
        ts_list[:] = [t for t in ts_list if now - t < _KW_FLOOD_WINDOW_SECS]
        ts_list.append(now)
        if len(ts_list) > _KW_FLOOD_LIMIT:
            log.warning(
                f"[KW_FLOOD] phone={phone} keyword={word!r} count={len(ts_list)} "
                f"in {_KW_FLOOD_WINDOW_SECS}s → suppressing reply"
            )
            return True
    return False


# ── Part H: Financial/high-risk intent → always draft ─────────────────────────
_FINANCIAL_DRAFT_INTENTS = frozenset({
    "payment", "salary", "advance", "dispute", "refund", "deduction",
    "bonus", "wage", "overdraft", "arrear", "payroll", "hisab",
    "বেতন", "পেমেন্ট", "অগ্রিম",
})

# Phase 4.5: Complaint/dispute phrases that override safe-autosend classification.
# If the inbound text contains any of these AND the intent is financial,
# the message is forced to draft regardless of _SAFE_AUTOSEND_INTENTS.
# Protects: বেতন পাইনি, পেমেন্ট সমস্যা, টাকা কম এসেছে, অ্যাডভান্স পাইনি, etc.
# Advance/money request phrases — always draft regardless of intent classification.
# Prevents "অ্যাডভান্স চাই" from auto-sending even when classifier misroutes to recruitment.
_ADVANCE_REQUEST_PHRASES: frozenset = frozenset({
    "অ্যাডভান্স চাই", "অ্যাডভান্স দরকার", "অ্যাডভান্স লাগবে",
    "অগ্রিম চাই", "অগ্রিম দরকার", "অগ্রিম লাগবে",
    "advance চাই", "advance দরকার", "advance লাগবে",
})

_COMPLAINT_PHRASES: frozenset = frozenset({
    "পাইনি", "পাই নি", "পাইনাই",
    "হয়নি", "হয় নি", "হয়নাই",
    "দেয়নি", "দেয় নি", "দেননি", "দেয় না",
    "কম এসেছে", "কম পেয়েছি", "কম পেলাম", "কম টাকা",
    "ভুল হিসাব", "হিসাব ভুল", "বেতন ভুল",
    "সমস্যা",          # catches: পেমেন্ট সমস্যা, বেতন সমস্যা, টাকা সমস্যা
    "ঝামেলা",
    "বেতন মেরে", "টাকা মেরে", "মারা",
    "অভিযোগ", "অভিযোগ করছি",
    "dispute", "issue", "problem",
})

# ── STEP 8: Prompt injection patterns ──────────────────────────────────────────
_PROMPT_INJECTION_PATTERNS: tuple = (
    "ignore instructions", "ignore previous", "ignore all",
    "show system prompt", "print system prompt", "reveal prompt",
    "print debug", "show debug", "dump config", "print config",
    "act as ", "you are now", "pretend you are", "forget you are",
    "bypass safety", "jailbreak", "dan mode", "developer mode",
    "ignore your", "disregard instructions", "override instructions",
)

# ── PATCHES 4+5: Outbound safety patterns ──────────────────────────────────────
_OUTBOUND_POISON: tuple = (
    "এআই-এর বিশ্লেষণ", "এআই-এর ইনটেন্ট",
    "| :--- |", "chain_of_thought", "Intent)",
    "প্রার্থীর মেসেজ", "প্রার্থীর সম্ভাব্য প্রশ্ন",
    "Semantic Analysis", "Tokenization",
    "RAG pipeline", "LLM pipeline", "prompt template",
    "OCR raw", "reasoning_trace",
    "বিশ্লেষণ (Intent)", "[1] ", "[2] ",
)


def _record_loop_reply(phone: str) -> None:
    """Track a sent reply timestamp for loop detection (STEP 7)."""
    import time as _t
    _reply_ts_window.setdefault(phone, []).append(_t.time())


def _check_loop_detect(phone: str) -> bool:
    """Return True if autoreply for this phone should be paused (STEP 7)."""
    import time as _t
    now = _t.time()
    if _loop_paused_until.get(phone, 0) > now:
        return True
    recent = [t for t in _reply_ts_window.get(phone, []) if now - t < _LOOP_WINDOW_SECS]
    _reply_ts_window[phone] = recent
    if len(recent) >= _LOOP_MAX_REPLIES:
        _loop_paused_until[phone] = now + _LOOP_PAUSE_SECS
        log.warning(
            f"[LOOP_DETECT] phone={phone} — {len(recent)} replies "
            f"in {_LOOP_WINDOW_SECS}s → autoreply paused {_LOOP_PAUSE_SECS}s"
        )
        return True
    return False


def _detect_prompt_injection(text: str) -> Optional[str]:
    """Return matched pattern if prompt injection detected in inbound text (STEP 8)."""
    text_lower = text.lower()
    return next((p for p in _PROMPT_INJECTION_PATTERNS if p in text_lower), None)


async def _can_reply(phone: str) -> bool:
    result = await _redis_cooldown_can_reply(phone)
    if result is not None:
        return result
    # Redis unavailable — fall back to in-memory dict
    import time
    last = _last_reply.get(phone, 0)
    return (time.time() - last) >= REPLY_COOLDOWN


async def _check_social_daemon_health() -> bool:
    """Returns True if the standalone social_auto_reply daemon appears alive.

    Reads the daemon's last heartbeat from fazle_service_heartbeats.
    Returns True (assume alive) on DB error or if no heartbeat exists yet,
    to prevent false fallback during initial startup or transient DB issues.
    Threshold: 300 seconds — daemon is considered dead if heartbeat older than 5 minutes.
    """
    try:
        row = await fetch_one(
            "SELECT EXTRACT(EPOCH FROM (NOW() - last_seen))::INT AS age "
            "FROM fazle_service_heartbeats WHERE service = 'social_auto_reply'",
        )
        if not row:
            return True  # no heartbeat yet — treat as alive to avoid false fallback
        return int(row["age"]) < 300
    except Exception:
        return True  # DB failure — assume alive, avoid disabling social engine


# Bangladeshi mobile number in any real-world format clients actually use:
# 01XXXXXXXXX, +8801XXXXXXXXX, 8801XXXXXXXXX, hyphenated/spaced digit groups.
# Presence of a mobile number is the primary escort-order signal per
# escort_business_rules.md ("Primary signal: Master Mobile Number") — it must
# not depend on MV-label spacing/punctuation/case, which vary per client and
# previously caused valid orders to be silently dropped into the (disabled)
# social-reply pipeline before ever reaching the parser.
_BD_MOBILE_HINT_RE = re.compile(r"\+?(?:880|0)[\s\-]?1[3-9](?:[\s\-]?\d){8}")


def _requires_legacy_workflow(text: str, msg: dict) -> bool:
    """Operational messages must use Fazle's workflow router, not social recruiting."""
    t = (text or "").lower()
    try:
        from modules.escort import is_completed_escort_draft
        if is_completed_escort_draft(text):
            return True
    except Exception:
        pass
    if _BD_MOBILE_HINT_RE.search(text or ""):
        return True
    operational_tokens = (
        "m.v", "mv ", "mother vessel", "lighter", "lighter vessel",
        "escort", "এস্কর্ট", "এমভি", "master number", "destination",
        "release", "রিলিজ", "[release confirmed]",
    )
    if any(token in t for token in operational_tokens):
        return True
    if msg.get("media_type") in ("image", "document", "application"):
        return True
    return False


async def _record_reply(phone: str):
    success = await _redis_cooldown_set(phone)
    if not success:
        # Redis unavailable — fall back to in-memory dict
        import time
        _last_reply[phone] = time.time()
    _record_loop_reply(phone)  # STEP 7: always track loop detection in-memory


async def _is_draft_always(phone: str, role: str, display_name: str) -> bool:
    """Return True when this contact must always be drafted, never auto-sent.

    Checks (in order):
      0. Live DB flag phone_block.{phone} (set via /api/wa/contacts/{phone}/block)
      0.5. Authoritative Owner-managed contact group
            (modules/owner_contact_registry.is_owner_managed_contact) --
            2026-08-24 Owner directive. Phone is the runtime key; the CSV is
            the SOLE source (no legacy contact group is consulted here).
            Returns True for any phone in selected_contacts_normalized_unique.csv
            regardless of display name. Protected business workflows
            (Escort order parser, Accountant cash extraction, Recruitment
            funnel intake) are NOT affected -- they run upstream of this gate.
      1. Phone is in DRAFT_ALWAYS_PHONES explicit list
      2. Identity role matches DRAFT_ALWAYS_ROLES (e.g. accountant, vip_client)
      3. Display name contains a DRAFT_ALWAYS_NAMES substring
      4. Display name starts with a DRAFT_NAME_PREFIXES prefix (client, escort, office…)
    """
    try:
        from modules.wa_chat_frontend import get_runtime_setting
        if await get_runtime_setting(f"phone_block.{phone}", "false") == "true":
            return True
    except Exception:
        pass
    # Owner-managed contact group CSV gate (2026-08-24). Fail-open
    # (returns False) if registry unavailable -- per Owner directive
    # section 12, NEVER silently fall back to another contact group.
    try:
        from modules.owner_contact_registry import is_owner_managed_contact
        if is_owner_managed_contact(phone):
            return True
    except Exception:
        pass
    s = _settings
    if phone in s.draft_always_phone_set:
        return True
    if role.lower() in s.draft_always_role_set:
        return True
    name_lower = display_name.lower()
    for exempt_name in s.draft_always_name_list:
        if exempt_name and exempt_name in name_lower:
            return True
    for prefix in s.draft_name_prefix_list:
        if name_lower.startswith(prefix):
            return True
    return False


def _is_bridge3_admin_recognition(bridge_name: str, phone: str) -> bool:
    """Bridge3 Admin Recognition Channel (Owner decision, 2026-08-04).

    True only when a message physically arrives ON bridge3 and is sent
    FROM bridge2's own number -- a narrow, local, one-directional check,
    deliberately NOT a change to identity_brain's global admin set (see the
    call site in _poll_bridge() for the full incident-history context this
    is scoped around). Extracted as its own function purely so it's
    directly unit-testable without mirroring _poll_bridge()'s much larger
    body.
    """
    return bridge_name == "bridge3" and bool(phone) and phone == _settings.bridge2_number


# ── Main poll loop ─────────────────────────────────────────────────────────────

async def _poll_bridge(config: dict):
    bridge_name: str = config["name"]
    messages_db: str = config["messages_db"]
    whatsapp_db: str = config["whatsapp_db"]
    get_bridge = config["get_bridge"]

    log.info(f"[{bridge_name}] Poller started")
    log.info("[bridge_poller] SQLite path=%s exists=%s", messages_db, os.path.exists(messages_db))

    # Load cursor from DB
    cursor = await _get_cursor(bridge_name)
    log.info(f"[{bridge_name}] Starting from cursor: {cursor.isoformat()}")

    # 2026-08-28 (send-gate timing fix): this used to be an iteration-count
    # gate (_poll_iter % _gate_check_every == 0), which assumed a roughly
    # fixed wall-clock time per iteration. That assumption breaks under
    # this same loop's own adaptive backoff (BRIDGE_POLL_MIN_S=1.0 busy ->
    # BRIDGE_POLL_MAX_S=30.0 idle): during a quiet stretch, reaching
    # _gate_check_every (=60) iterations could take up to ~30 minutes of
    # real time -- six times longer than the intended, commented "every
    # 5 min" -- silently stretching the window a bridge's send gate could
    # sit "off" (send_strict() turns it off after every real send; this
    # periodic check is the only thing that turns it back on between
    # sends). Root-caused live: Bridge2's /api/send-status sat
    # allowed=false for 18+ minutes with no bridge/session issue at all.
    # Fix: track elapsed wall-clock time via time.monotonic() instead of
    # iteration count -- the 300s intended interval is now the actual
    # interval, regardless of how the adaptive backoff has stretched this
    # iteration's own sleep. Adaptive polling itself (_sleep_s below) is
    # completely unchanged.
    _last_gate_check = time.monotonic()
    _sleep_s = BRIDGE_POLL_MIN_S

    while True:
        try:
            _had_activity = False
            loop = asyncio.get_event_loop()
            # Stage E3: local import to avoid a circular top-level import
            # (modules.group_identity itself imports from this module) --
            # same deferred-import convention already used throughout this
            # file for modules.escort/modules.identity_brain/etc.
            from modules.group_identity import list_approved_group_jids
            _approved_group_jids = tuple(await list_approved_group_jids())
            messages, new_cursor = await loop.run_in_executor(
                None, _fetch_new_messages, messages_db, whatsapp_db, cursor, _approved_group_jids
            )

            if messages:
                log.info(f"[{bridge_name}] {len(messages)} new message(s) to process")
                _had_activity = True

            dedup_skipped = 0
            inbound_saved = 0
            unresolved_router_skipped = 0
            group_read_index_saved = 0  # Stage E3: approved-group messages, persisted, never routed
            media_unprocessed_skipped = 0  # bare [media:*] placeholder, no caption/OCR/STT — never routed

            for msg in messages:
                msg_id: str = msg["id"]
                phone: str = msg["phone"]
                text: str = msg["text"]

                # Dedup check
                if await _is_processed(msg_id, bridge_name):
                    dedup_skipped += 1
                    continue

                _extracted_text = ""  # filled by media processors below; stored in extracted_text column
                # 2026-08-25 recovery-pass (cf_W001 narrow mitigation): set
                # True only when `text` below ends up being raw OCR/STT
                # output that couldn't be classified as a known slip/
                # document type (i.e. it falls through to general routing
                # instead of being handled by its own dedicated, already-
                # safe branch). See process_message()'s docstring.
                _is_extracted_media = False
                core_message_id = await _save_raw_core_copy(bridge_name, msg, phone, text)

                # Stage E3 (2026-08-28, controlled Office-group ingestion):
                # PERSISTENCE != AUTOMATION. This point is only ever
                # reached for a group JID present in _fetch_new_messages'
                # own approved-group allowlist -- the SQL query itself
                # still blanket-excludes every other @g.us chat (Owner
                # directive 2026-04-27, see this module's own docstring).
                # Re-checked here as a second, independent enforcement
                # point regardless -- same defense-in-depth pattern
                # modules.escort's trusted_group_jid exception already
                # established (SQL-level restriction + an explicit
                # code-level check that doesn't trust the SQL alone) --
                # so a future refactor of the SQL query can never silently
                # widen automation exposure to an unapproved group.
                # Deliberately stops HERE: no OCR, no identity detection,
                # no process_bridge_inbound() (routing/recruitment/
                # employee/customer automation, admin-command parsing,
                # Hermes relay, AI auto-reply) for ANY group message,
                # approved or not -- the raw copy is already persisted
                # with conversation_key/actor_type/direction/timestamp/
                # provider message ref (Stage D2/D1). intent_detected/
                # workflow_triggered are deliberately left NULL for group
                # messages this stage (Stage E4: "intent/domain only where
                # classifier semantics are valid" -- not yet judged valid
                # for group content, which can be very different in
                # character from a 1:1 conversation). Marks the queue row
                # to a terminal state so fazle_message_queue never sits
                # stuck at "processing" -- the exact incident class the
                # 2026-07-09..12 fix above (see that comment) closed for a
                # different cause; same discipline applied here.
                _group_chat_jid = str(msg.get("chat_jid") or "")
                if _group_chat_jid.endswith("@g.us"):
                    group_read_index_saved += 1
                    await _mark_core_queue_status(bridge_name, msg, "skipped", "group_read_index_only")
                    continue

                # Unreadable media: a bare "[media:<type>]" transport placeholder
                # (no caption/content, no bridge-side OCR/STT `processed_text`).
                # The current bridge binary does not deliver media bytes, so
                # core-side extraction cannot run either. The placeholder is NOT
                # sender text — never let it reach OCR/STT eligibility, the
                # intent classifier, RAG, the semantic gate, Hermes or reply
                # generation, and never fabricate a transcript. Preserve the
                # canonical record + media metadata, mark the queue row
                # terminal, and continue. (A real caption or a populated
                # legacy `processed_text` makes has_meaningful_text() True and
                # this branch is skipped — those route normally.) Group media
                # never reaches here — the @g.us guard above already handled it.
                if is_media_placeholder(text) and not has_meaningful_text(msg.get("processed_text")):
                    media_unprocessed_skipped += 1
                    await _mark_processed(msg_id, bridge_name, phone)
                    await _finalize_core_copy(
                        core_message_id, bridge_name, msg, phone, text,
                        msg_type=msg.get("media_type") or "media",
                        status="skipped", error="media_unprocessed",
                    )
                    await _mark_core_queue_status(bridge_name, msg, "skipped", "media_unprocessed")
                    log.info(
                        "[%s] MEDIA_UNPROCESSED msg_id=%s type=%s phone=%s — placeholder only, not routed",
                        bridge_name, msg_id, msg.get("media_type"), phone,
                    )
                    continue

                # Bridge-side OCR may already populate processed_text. Previously
                # release images with processed_text skipped the release workflow
                # because the full OCR block only ran when text was empty.
                if msg.get("media_type") == "image" and has_meaningful_text(text):
                    try:
                        _processed_ocr_text = text
                        from modules.ocr_processor import (
                            classify_slip_type as _classify_slip_type,
                            _extract_fields as _extract_ocr_fields,
                            _compute_confidence as _compute_ocr_confidence,
                        )
                        if _classify_slip_type(text) == "release_slip":
                            _extracted_text = _processed_ocr_text
                            fields = _extract_ocr_fields(text, "release_slip")
                            conf_pct = _compute_ocr_confidence(text, fields)
                            compat = {
                                "slip_type": "release_slip",
                                "employee_name": fields.get("employee_name"),
                                "vessel": fields.get("vessel"),
                                "date": fields.get("date"),
                                "location": fields.get("location"),
                                "amount": fields.get("amount"),
                                "confidence_score": conf_pct,
                                "raw_text": text,
                            }
                            from modules.escort_lifecycle import handle_ocr_release_slip
                            draft_txt = await handle_ocr_release_slip(compat, source=bridge_name, phone=phone)
                            if draft_txt:
                                _admin_phone = (
                                    _settings.admin_bridge2_number
                                    or _settings.admin_bridge1_number
                                    or ((_settings.admin_number_list or [""])[0])
                                    or _settings.admin_meta_number
                                )
                                await _notify_admin_bridge({
                                    "admin_phone": _admin_phone,
                                    "text": f"📋 Release Slip (from {phone}):\n\n{draft_txt}",
                                    "bridge": "bridge2",
                                    "purpose": "release-slip-review",
                                })
                            text = "✅ রিলিজ স্লিপ পাওয়া গেছে। অ্যাডমিন যাচাই করে পেমেন্ট অনুমোদন করবেন।"
                    except Exception as _pre_ocr_err:
                        log.error(
                            "[%s] processed_text release-slip handling error msg_id=%s: %s",
                            bridge_name, msg_id, _pre_ocr_err,
                        )

                # ── Phase 22: 2-step OCR for images without pre-processed text ─
                if msg.get("media_type") == "image" and not has_meaningful_text(text) and msg.get("filename"):
                    from modules.ocr_processor import classify_from_context
                    store_dir = os.path.dirname(messages_db)
                    file_path = os.path.join(store_dir, msg.get("chat_jid", ""), msg["filename"])
                    try:
                        # STEP 1: Lightweight context check (no OCR yet)
                        ctx_rows = []
                        try:
                            # asyncio.to_thread() preferred over run_in_executor on
                            # Python 3.9+ — see also _fetch_new_messages for the
                            # older loop.run_in_executor pattern
                            ctx_rows = await asyncio.to_thread(
                                _fetch_image_context_rows, messages_db, msg["chat_jid"], msg["timestamp"]
                            )
                        except Exception as _ctx_err:
                            log.debug(f"[{bridge_name}] context fetch error: {_ctx_err}")
                        context_text = " ".join(r["content"] for r in ctx_rows)

                        if classify_from_context(context_text):
                            log.info("[IMG_CONTEXT_PASS] bridge=%s msg_id=%s filename=%s", bridge_name, msg_id, msg["filename"])
                        else:
                            log.info("[IMG_CONTEXT_FAIL_FALLBACK_CHECK] bridge=%s msg_id=%s filename=%s", bridge_name, msg_id, msg["filename"])
                            eligible, reason = _is_lightweight_image_eligible_for_ocr(file_path, msg["filename"])
                            if eligible:
                                log.info("[IMG_ELIGIBLE_FOR_OCR] bridge=%s msg_id=%s reason=%s", bridge_name, msg_id, reason)
                            else:
                                log.info("[IMG_SKIPPED_NOT_ELIGIBLE] bridge=%s msg_id=%s reason=%s", bridge_name, msg_id, reason)
                                await _finalize_core_copy(
                                    core_message_id, bridge_name, msg, phone, text,
                                    msg_type=msg.get("type") or "image",
                                    status="skipped", error=f"image_not_eligible:{reason}",
                                )
                                continue  # Not a probable slip/document image — safe terminal skip

                        # STEP 2: Full extraction via escort_slip_extractor (3-pass OCR + field parsing)
                        log.info(f"[{bridge_name}] probable slip image, running full extraction: {file_path}")
                        try:
                            from modules.escort_slip_extractor import extract_escort_slip as _extract_slip
                            ocr_result = await _extract_slip(file_path, source_label=bridge_name)
                        except Exception as _ocr_err:
                            log.error(f"[{bridge_name}] extraction error for {file_path}: {_ocr_err}")
                            await _finalize_core_copy(
                                core_message_id, bridge_name, msg, phone, text,
                                msg_type=msg.get("type") or "image",
                                status="failed", error=f"ocr_error:{_ocr_err}",
                            )
                            continue

                        # completion_date present → supervisor stamped second date → duty-completion document
                        is_release = ocr_result.get("completion_date") is not None
                        doc_type = ocr_result.get("document_type", "unknown_document")
                        conf_pct = int(ocr_result.get("confidence", 0) * 100)

                        if is_release:
                            # Translate EscortSlipResult → compat dict for handle_ocr_release_slip
                            compat = {
                                "slip_type": "release_slip",
                                "employee_name": ocr_result.get("escort_name"),
                                "vessel": ocr_result.get("lighter_vessel") or ocr_result.get("mother_vessel"),
                                "date": ocr_result.get("completion_date"),
                                "location": ocr_result.get("release_place"),
                                "confidence_score": conf_pct,
                                "raw_text": ocr_result.get("raw_ocr_text", ""),
                                "master_mobile": ocr_result.get("master_mobile"),
                                "start_date": ocr_result.get("start_date"),
                            }
                            _extracted_text = ocr_result.get("raw_ocr_text", "") or _extracted_text
                            from modules.escort_lifecycle import handle_ocr_release_slip
                            try:
                                draft_txt = await handle_ocr_release_slip(compat, source=bridge_name, phone=phone)
                                if draft_txt:
                                    _admin_phone = (
                                        _settings.admin_bridge2_number
                                        or _settings.admin_bridge1_number
                                        or ((_settings.admin_number_list or [""])[0])
                                        or _settings.admin_meta_number
                                    )
                                    await _notify_admin_bridge({
                                        "admin_phone": _admin_phone,
                                        "text": f"📋 Release Slip (from {phone}):\n\n{draft_txt}",
                                        "bridge": "bridge2",
                                        "purpose": "release-slip-review",
                                    })
                                missing = ocr_result.get("missing_fields", [])
                                if missing or conf_pct < 60:
                                    escort_reply = (
                                        "✅ আপনার রিলিজ স্লিপ পাওয়া গেছে।"
                                        + (" কিছু তথ্য স্পষ্ট নয়।" if missing else "")
                                        + " অ্যাডমিন যাচাই করছেন — অনুগ্রহ করে আপনার নাম ও আইডি নম্বর পাঠান।"
                                    )
                                else:
                                    escort_reply = "✅ রিলিজ স্লিপ পাওয়া গেছে। অ্যাডমিন যাচাই করে পেমেন্ট অনুমোদন করবেন।"
                                text = escort_reply
                            except Exception as _rs_err:
                                log.error(f"[{bridge_name}] release slip handling error: {_rs_err}")
                                await _finalize_core_copy(
                                    core_message_id, bridge_name, msg, phone, text,
                                    msg_type=msg.get("type") or "image",
                                    status="failed", error=f"release_slip_error:{_rs_err}",
                                )
                                continue
                            # Fall through to normal routing to send escort_reply

                        elif doc_type == "unknown_document" or conf_pct < 10:
                            # If sender has active escort program, flag to admin — may be unclear release slip
                            try:
                                from modules.escort_lifecycle import check_active_program_for_phone
                                has_program = await check_active_program_for_phone(phone)
                                if has_program:
                                    await _notify_admin_bridge({
                                        "admin_phone": _settings.admin_bridge2_number,
                                        "text": (
                                            f"⚠️ অস্পষ্ট ছবি (OCR ব্যর্থ) from {phone}\n"
                                            "এই নম্বরে সক্রিয় এস্কর্ট প্রোগ্রাম আছে — রিলিজ স্লিপ হতে পারে।\n"
                                            "ম্যানুয়ালি যাচাই করুন অথবা এস্কর্টকে স্পষ্ট ছবি পাঠাতে বলুন।"
                                        ),
                                        "bridge": "bridge2",
                                    })
                                    text = "ছবিটি স্পষ্ট নয়। অনুগ্রহ করে ভালো আলোতে আবার ছবি তুলে পাঠান।"
                                else:
                                    await _finalize_core_copy(
                                        core_message_id, bridge_name, msg, phone, text,
                                        msg_type=msg.get("type") or "image",
                                        status="skipped", error="unknown_image_no_active_program",
                                    )
                                    continue  # Unknown image, no active program — skip
                            except Exception:
                                await _finalize_core_copy(
                                    core_message_id, bridge_name, msg, phone, text,
                                    msg_type=msg.get("type") or "image",
                                    status="failed", error="unknown_image_active_program_check_failed",
                                )
                                continue

                        else:
                            # escort slip (assignment, not release) — use extracted text for routing
                            text = ocr_result.get("raw_ocr_text") or f"[image: {doc_type}]"
                            _extracted_text = ocr_result.get("raw_ocr_text") or ""
                            _is_extracted_media = True
                    finally:
                        await _mark_processed(msg_id, bridge_name, phone)
                        log.info("[IMG_MARK_PROCESSED_FINAL] bridge=%s msg_id=%s phone=%s", bridge_name, msg_id, phone)
                else:
                    # Audio transcription for voice messages
                    if msg.get("media_type") in ("audio", "ptt") and not has_meaningful_text(text) and msg.get("filename"):
                        from modules.voice_processor import process_voice as _proc_voice
                        store_dir = os.path.dirname(messages_db)
                        file_path = os.path.join(store_dir, msg.get("chat_jid", ""), msg["filename"])
                        try:
                            voice_result = await _proc_voice(file_path)
                            if voice_result["confident"]:
                                text = voice_result["transcript"]
                                _extracted_text = voice_result["transcript"]
                                _is_extracted_media = True
                                log.info("[AUDIO_TRANSCRIBED] bridge=%s msg_id=%s words=%d", bridge_name, msg_id, voice_result["word_count"])
                            else:
                                text = "[audio: unclear]"
                                log.info("[AUDIO_LOW_CONFIDENCE] bridge=%s msg_id=%s transcript=%r", bridge_name, msg_id, voice_result["transcript"])
                        except Exception as _audio_err:
                            log.error("[AUDIO_ERR] bridge=%s msg_id=%s err=%s conf=%s",
                                      bridge_name, msg_id, _audio_err,
                                      getattr(_audio_err, "__class__", type(_audio_err)).__name__)
                            # Prevent silent drop: save draft so admin knows STT failed
                            await _save_draft(
                                bridge_name, phone,
                                f"[STT FAILED] ভয়েস বার্তা ট্রান্সক্রাইব করা যায়নি।\n"
                                f"ফাইল: {os.path.basename(file_path)}\n"
                                f"এরর: {str(_audio_err)[:120]}\n"
                                "ব্যবহারকারীকে টেক্সট পাঠাতে বলুন অথবা ম্যানুয়ালি উত্তর দিন।",
                                "stt_failed",
                            )
                            await _mark_processed(msg_id, bridge_name, phone)
                            await _finalize_core_copy(
                                core_message_id, bridge_name, msg, phone, text,
                                msg_type=msg.get("type") or "audio",
                                status="failed", error=f"stt_failed:{_audio_err}",
                            )
                            continue
                    # ── Parts C+D: PDF/document pipeline ───────────────────
                    elif (
                        msg.get("media_type") in ("document", "application")
                        or (msg.get("filename") or "").lower().endswith(".pdf")
                    ) and not has_meaningful_text(text) and msg.get("filename"):
                        from modules.ocr_processor import process_document as _proc_doc
                        store_dir = os.path.dirname(messages_db)
                        file_path = os.path.join(store_dir, msg.get("chat_jid", ""), msg["filename"])
                        filename = msg.get("filename", "")
                        _doc_ack_sent = False
                        try:
                            doc_result = await _proc_doc(file_path, filename)
                            doc_type = doc_result.get("doc_type", "unknown")
                            log.info(
                                "[DOC_PROCESSED] bridge=%s msg_id=%s filename=%s "
                                "doc_type=%s conf=%d",
                                bridge_name, msg_id, filename, doc_type,
                                doc_result.get("confidence_score", 0),
                            )
                            ack_reply = doc_result.get("reply", "")
                            extracted = doc_result.get("extracted_text") or ""
                            _extracted_text = extracted
                            # Store extracted text as the routing text
                            text = extracted[:300] or f"[document:{doc_type}:{filename}]"
                            if not (doc_type != "unknown" and doc_result.get("auto_send_safe")):
                                # Not a cleanly-classified candidate document
                                # (falls through to general routing below,
                                # not auto-acked+continue) -- see
                                # process_message()'s is_extracted_media
                                # docstring, cf_W001.
                                _is_extracted_media = True

                            if doc_type != "unknown" and doc_result.get("auto_send_safe"):
                                # Part H: candidate docs → auto-send acknowledgement
                                # Bypasses AUTO_REPLY_ENABLED (fixed safe reply only)
                                bridge_obj = config["get_bridge"]()
                                sent = await bridge_obj.send(phone, ack_reply)
                                if sent:
                                    await _record_reply(phone)
                                    _doc_ack_sent = True
                                    log.info(
                                        "[DOC_ACK_SENT] bridge=%s phone=%s doc_type=%s",
                                        bridge_name, phone, doc_type,
                                    )
                                else:
                                    log.warning("[DOC_ACK_FAIL] bridge=%s phone=%s", bridge_name, phone)
                                    await _save_draft(bridge_name, phone, ack_reply, f"doc:{doc_type}")
                            else:
                                await _save_draft(bridge_name, phone, ack_reply,
                                                  f"doc:{doc_type}" if doc_type != "unknown" else "doc:unknown")
                        except Exception as _doc_err:
                            log.error(
                                "[DOC_ERR] bridge=%s msg_id=%s filename=%s err=%s",
                                bridge_name, msg_id, filename, _doc_err,
                            )
                            await _save_draft(
                                bridge_name, phone,
                                f"[DOC FAILED] ডকুমেন্ট প্রসেস করা যায়নি।\n"
                                f"ফাইল: {filename}\nএরর: {str(_doc_err)[:100]}",
                                "doc_failed",
                            )
                        await _mark_processed(msg_id, bridge_name, phone)
                        log.info("[DOC_MARK_PROCESSED] bridge=%s msg_id=%s phone=%s", bridge_name, msg_id, phone)
                        if _doc_ack_sent:
                            await _finalize_core_copy(
                                core_message_id, bridge_name, msg, phone, text,
                                msg_type=msg.get("type") or "document",
                                extracted_text=_extracted_text,
                                status="done",
                            )
                            continue
                    # Mark non-image and already-normalized messages immediately as before.
                    await _mark_processed(msg_id, bridge_name, phone)

                log.info(f"[{bridge_name}] from={phone} text={text[:80]!r}")

                # Detect identity before routing (for logging).
                #
                # Wrapped (2026-08-04 -- closes a real incident, see
                # knowledge_base/00_governance/management_decisions.md "55
                # Stuck WhatsApp Messages"): _mark_processed() above already
                # committed this message's bridge-side dedup marker, so
                # before this fix, any unhandled exception in this window
                # propagated past the entire per-message loop to
                # _poll_bridge()'s single top-level `except Exception:
                # log.exception(...)` -- leaving Postgres bookkeeping
                # (wbom_whatsapp_messages.is_processed,
                # fazle_message_queue.status) stuck at "processing" with
                # attempts=0 forever, with no retry possible (the dedup
                # marker permanently blocks re-fetch). This happened for
                # real 2026-07-09..12 (identity_brain._lookup_contact() had
                # wrong column names, fixed in 47a9c8e) and could recur for
                # any different future cause in this same window. Now: any
                # exception here finalizes the message to a terminal
                # "failed" state (_finalize_core_copy already updates both
                # tables and never itself raises) and skips routing for
                # this message only -- the rest of the batch still
                # processes normally.
                try:
                    from modules.identity_brain import detect_identity
                    identity = await detect_identity(phone, text)
                    id_role = identity["identity_role"]
                    id_conf = identity["identity_confidence"]
                    id_name = (identity.get("display_name") or "").strip()

                    # Bridge3 Admin Recognition Channel (Owner decision,
                    # 2026-08-04). Deliberately NOT implemented as a global
                    # identity change (i.e. NOT re-adding bridge3 to
                    # ADMIN_NUMBERS / identity_brain._check_admin_settings())
                    # -- that broader mechanism was built earlier the same
                    # day, caused a real ~28-minute cross-bridge message
                    # loop (see management_decisions.md "INCIDENT: Live
                    # Cross-Bridge Message Loop"), and was explicitly
                    # reversed with the comment "ADMIN_BRIDGE3_NUMBER
                    # intentionally removed; do not re-add without a fresh
                    # Owner decision" (.env, identity_brain/__init__.py).
                    # This is that fresh decision, but scoped narrower and
                    # differently than what caused the incident: a LOCAL,
                    # per-message override -- only a message physically
                    # arriving ON bridge3, sent FROM bridge2's own number,
                    # is treated as Admin for ITS OWN processing. bridge2
                    # itself gains no new admin recognition of bridge3 (the
                    # relationship is one-directional), and every other
                    # bridge/sender combination is completely unaffected.
                    # The existing LOOP_GUARD below (search
                    # "cross-bridge bounce") independently still catches
                    # and diverts-to-draft any admin-role reply whose
                    # destination is another bridge's own number,
                    # regardless of how id_role was determined -- that
                    # safety net is unchanged by this addition.
                    if _is_bridge3_admin_recognition(bridge_name, phone):
                        log.info(
                            "[bridge3] ADMIN_RECOGNITION: sender=%s matches bridge2's own "
                            "number, arriving on bridge3 -- treating as Admin for this message",
                            phone,
                        )
                        id_role = "admin"
                        id_conf = 100  # identity_confidence is an int 0-100 scale, not 0.0-1.0
                        id_name = id_name or "Admin"

                    # Update the raw core copy with identity metadata + extracted fields.
                    await _finalize_core_copy(
                        core_message_id, bridge_name, msg, phone, text,
                        identity_role=id_role, identity_confidence=id_conf,
                        msg_type=msg.get("type") or "text",
                        extracted_text=_extracted_text,
                        status="processing",
                    )
                except Exception as _identity_err:
                    log.error(
                        "[%s] identity/finalize error msg_id=%s phone=%s: %s",
                        bridge_name, msg_id, phone, _identity_err,
                    )
                    await _finalize_core_copy(
                        core_message_id, bridge_name, msg, phone, text,
                        msg_type=msg.get("type") or "text",
                        status="failed", error=f"identity_or_finalize_error:{_identity_err}",
                    )
                    continue
                inbound_saved += 1
                try:
                    from modules import observability as _obs
                    _obs.inc("dm_messages_ingested_total",
                             labels={"bridge": bridge_name})
                except Exception:
                    pass

                # v1.0.2: never run router/draft/reply on unresolved-LID messages
                # (we still kept them in DB above for audit).
                if phone.startswith("unresolved:"):
                    unresolved_router_skipped += 1
                    await _mark_core_queue_status(bridge_name, msg, "skipped", "unresolved_lid")
                    continue

                # 2026-07-21: identity-first routing — social_auto_reply's
                # keyword-classifier/template engine is retired for WhatsApp
                # DM traffic (bridge1/bridge2/meta). Every inbound message
                # now goes through process_bridge_inbound() → process_message(),
                # which itself dispatches Route A (KB/templates, existing
                # roles) vs Route B (Ollama-only — escort/security_guard/
                # candidate/unknown, see modules.route_b) by sender identity
                # instead of by message content. social_auto_reply.
                # ingest_social_event() is still used for Messenger DMs and
                # Facebook Page comments (app/main.py _handle_messenger_message
                # / _handle_fb_comment) — those are unaffected.
                try:
                    await process_bridge_inbound(
                        bridge_name, phone, text,
                        id_role=id_role, id_conf=id_conf, id_name=id_name,
                        message_id=str(msg.get("id") or msg.get("message_id") or ""),
                        chat_jid=msg.get("chat_jid") or "",
                        msg_timestamp=_parse_source_timestamp(msg.get("timestamp")),
                        is_extracted_media=_is_extracted_media,
                        core_message_id=core_message_id,
                    )
                    await _mark_core_queue_status(bridge_name, msg, "done")
                except Exception as route_err:
                    await _mark_core_queue_status(bridge_name, msg, "failed", f"routing_error:{route_err}")
                    raise

            if bridge_name == "bridge1":
                log.info(
                    "[GAP_DIAG] bridge=%s poll_summary fetched=%d dedup_skipped=%d inbound_saved=%d unresolved_router_skipped=%d group_read_index_saved=%d media_unprocessed_skipped=%d cursor_from=%s cursor_to=%s",
                    bridge_name,
                    len(messages),
                    dedup_skipped,
                    inbound_saved,
                    unresolved_router_skipped,
                    group_read_index_saved,
                    media_unprocessed_skipped,
                    cursor.isoformat(),
                    new_cursor.isoformat(),
                )

            # Advance cursor even if no messages (uses max_ts from fetch)
            if new_cursor > cursor:
                cursor = new_cursor
                await _set_cursor(bridge_name, cursor)

            # ── Outgoing admin confirmation processing ────────────────────────
            # Both bridges may carry admin confirmations. Reading outgoing SQLite
            # rows does not modify bridge sessions or QR state.
            # Those messages are is_from_me=1 in bridge2 SQLite and never appear
            # in the inbound query above. We read them separately and route to
            # handle_admin_escort_completion() so escort_name/mobile get saved.
            if bridge_name in ("bridge1", "bridge2"):
                out_cursor = await _get_outgoing_cursor(bridge_name)
                loop2 = asyncio.get_event_loop()
                # 2026-07-22, Owner-approved Trusted Group: the group-JID
                # exception is only ever passed for bridge2 -- bridge1's
                # query is never extended to include any group, full stop,
                # per Rule 4 ("valid ONLY ... via Bridge2"). This is the
                # first of two enforcement points (SQL-level); the second
                # is the explicit bridge_name check below.
                trusted_group_jid = (
                    _settings.escort_order_group_jid if bridge_name == "bridge2" else ""
                )
                out_msgs, new_out_cursor = await loop2.run_in_executor(
                    None, _fetch_outgoing_escort_completions, messages_db, out_cursor, trusted_group_jid
                )

                if out_msgs:
                    log.info(f"[{bridge_name}] {len(out_msgs)} outgoing message(s) to check for escort completions")
                    _had_activity = True

                from modules.escort import (
                    is_completed_escort_draft, handle_admin_escort_completion,
                    is_order_complete, handle_group_escort_order,
                )
                from modules.escort_lifecycle import is_release_confirmation, handle_admin_release_confirmation
                # Hermes self-relay (2026-08-12, Owner-directed): reuses the
                # exact regex admin_directives.router's own inbound HERMES
                # command uses, so the two paths recognize identical text.
                # process_admin_natural_query (2026-08-27 addition, see the
                # unprefixed-fallback block below): the same unprefixed-text
                # bypass genuinely-inbound messages already get.
                from modules.admin_directives.router import (
                    process_admin_ai_command, process_admin_natural_query, _RE_HERMES,
                )

                admin_number = (
                    _settings.bridge1_number if bridge_name == "bridge1"
                    else _settings.bridge2_number
                )

                for omsg in out_msgs:
                    omsg_id: str = omsg["id"]
                    otext: str = omsg["text"]
                    chat_jid: str = omsg.get("chat_jid", "")

                    if await _is_outgoing_processed(omsg_id, bridge_name):
                        continue

                    await _mark_outgoing_processed(omsg_id, bridge_name)

                    # 2026-07-22: second enforcement point for "bridge2 only"
                    # -- even though the SQL query above already only ever
                    # requests this JID for bridge2, this explicit check
                    # means a future refactor of the query-building code
                    # can't silently widen the group exception to bridge1.
                    is_trusted_group_msg = bool(
                        trusted_group_jid
                        and bridge_name == "bridge2"
                        and chat_jid == trusted_group_jid
                    )

                    # Hermes self-relay (2026-08-12, Owner-directed): a
                    # message the Owner sends to himself on bridge2 is
                    # is_from_me=1 by construction -- WhatsApp multi-device's
                    # is_from_me flag is account-level, so the inbound
                    # (is_from_me=0) pipeline can never see a self-sent
                    # message, on any device, by design of the protocol
                    # itself (not a bug in this codebase). This is therefore
                    # the ONLY channel through which the Owner's own
                    # "hermes <request>" WhatsApp message can ever reach the
                    # real Hermes agent. Scoped to bridge2 only (never
                    # bridge1 -- that's the HR number, not Super Admin).
                    # Reuses process_admin_ai_command()/_handle_hermes_relay()
                    # unchanged -- no new authorization logic here; sender==
                    # admin_number (== settings.bridge2_number ==
                    # settings.admin_bridge2_number in this deployment) and
                    # source_bridge=="bridge2" both trivially satisfy the
                    # existing gate, exactly as the inbound path already
                    # requires. Only the HERMES pattern is recognized here,
                    # deliberately -- not the other 5 admin_ai_command
                    # patterns (AI/autoreply/remember/forget/send), which
                    # stay inbound-only until a separate Owner decision.
                    if bridge_name == "bridge2" and _RE_HERMES.match(otext.strip()):
                        log.info(f"[{bridge_name}] outgoing HERMES self-relay detected → msg={omsg_id}")
                        try:
                            ack = await process_admin_ai_command(
                                otext, sender_phone=admin_number, source_bridge=bridge_name,
                            )
                            if ack:
                                from modules.outbound import enqueue as enqueue_outbound
                                await enqueue_outbound(
                                    admin_number, ack,
                                    source_bridge=bridge_name,
                                    purpose="hermes-self-relay-ack",
                                    idempotency_key=f"hermes-self-relay:{omsg_id}",
                                )
                        except Exception as _hermes_err:
                            log.error(f"[{bridge_name}] hermes self-relay error for msg {omsg_id}: {_hermes_err}")
                        continue

                    # Phase 22: release confirmation takes priority
                    if is_release_confirmation(otext):
                        if _is_system_generated_notification(otext):
                            log.warning(
                                f"[{bridge_name}] outgoing text matches is_release_confirmation() but "
                                f"is a system-generated notification (e.g. RELEASE REVIEW REQUIRED, "
                                f"which itself contains the [RELEASE CONFIRMED] anchor as instructional "
                                f"text) -- rejecting as a false-positive confirmation candidate, "
                                f"msg={omsg_id}"
                            )
                            continue
                        log.info(f"[{bridge_name}] outgoing RELEASE confirmation → jid={chat_jid}")
                        try:
                            await handle_admin_release_confirmation(otext, chat_jid, source=bridge_name)
                        except Exception as _rc_err:
                            log.error(f"[{bridge_name}] release confirm error for msg {omsg_id}: {_rc_err}")
                        continue

                    if is_completed_escort_draft(otext):
                        if _is_system_generated_notification(otext):
                            log.warning(
                                f"[{bridge_name}] outgoing text matches is_completed_escort_draft() but "
                                f"starts with a known system-notification prefix -- rejecting as a false-"
                                f"positive confirmation candidate, msg={omsg_id}"
                            )
                            continue

                        log.info(f"[{bridge_name}] outgoing escort completion detected → jid={chat_jid}")
                        try:
                            # 2026-07-22: a group JID is never a phone number --
                            # passing "" here (instead of digit-stripping the
                            # group JID into garbage) routes correctly through
                            # _is_authoritative_bridge_confirmation()'s existing
                            # "admin messaging with no specific recipient"
                            # branch, unmodified.
                            recipient_phone = "" if is_trusted_group_msg else re.sub(r"\D", "", chat_jid or "")
                            await handle_admin_escort_completion(
                                otext,
                                admin_number,
                                bridge_name,
                                recipient_phone=recipient_phone,
                                message_id=omsg_id,
                            )
                        except Exception as _ec_err:
                            log.error(f"[{bridge_name}] escort completion error for msg {omsg_id}: {_ec_err}")
                        continue

                    # 2026-07-22: New-Order path -- only reachable for the
                    # Trusted Group (never for ordinary DMs; a plain outgoing
                    # DM that merely looks order-shaped is not a business
                    # event on its own, unlike an admin actively posting a
                    # dispatch order into the verified group).
                    if is_trusted_group_msg and is_order_complete(otext):
                        log.info(f"[{bridge_name}] outgoing group order detected → jid={chat_jid}")
                        try:
                            await handle_group_escort_order(
                                otext, admin_number, bridge_name, chat_jid, message_id=omsg_id,
                            )
                        except Exception as _go_err:
                            log.error(f"[{bridge_name}] group order error for msg {omsg_id}: {_go_err}")
                        continue

                    # Bridge2 self-chat natural-language Admin->Hermes fallback
                    # (2026-08-27, Owner-directed): closes the gap where an
                    # unprefixed Super Admin self-chat message -- one that
                    # doesn't start with "hermes,"/"hermes:" -- fell through
                    # every check above and was silently dropped. Mirrors,
                    # for this is_from_me=1 self-chat scanner, the exact same
                    # unprefixed-text bypass process_admin_natural_query()
                    # already applies to genuinely-inbound (is_from_me=0)
                    # messages on bridge1/bridge2 (see that function's own
                    # docstring) -- this is the missing second call site for
                    # the identical rule, not a new routing system.
                    #
                    # Scoped narrowly, in this order, so nothing else in
                    # this loop can ever be reinterpreted as a Hermes turn:
                    #   1. only reached once none of the deterministic
                    #      operational patterns above matched (explicit
                    #      "hermes," prefix, release confirmation, escort
                    #      completion, group order) -- every one of those
                    #      branches `continue`s before reaching here;
                    #   2. bridge2 only, never bridge1 -- bridge1 is the HR
                    #      number, not Super Admin, and has no self-chat
                    #      ambiguity to begin with (see _handle_hermes_
                    #      relay's docstring);
                    #   3. never inside the Trusted Group -- a message
                    #      posted into the shared dispatch group is not a
                    #      private Admin<->Hermes conversation turn no
                    #      matter who is_from_me it is; only the explicit
                    #      prefix (checked unconditionally above) may reach
                    #      Hermes from inside the group.
                    #
                    # Reuses process_admin_natural_query() completely
                    # unchanged -- no new authorization logic here. Its own
                    # identity check (sender_phone == settings.
                    # admin_bridge2_number) and channel check (source_bridge
                    # in _HERMES_CONTROL_CHANNELS) both trivially pass for
                    # this call (sender is always admin_number == bridge2's
                    # own admin number, source_bridge is always "bridge2"),
                    # so it always delegates to _handle_hermes_relay() --
                    # the exact same RBAC + cooldown + RUN-toolset + session
                    # + approval-gate path the explicit-prefix branch above
                    # already uses, never process_admin_natural_query()'s AI
                    # console fallback. _handle_hermes_relay() returns ""
                    # here (its immediate ack is intentionally empty, same
                    # as the prefixed path) -- the real answer is delivered
                    # moments later as its own outbound message via
                    # _deliver_hermes_reply(), unchanged and unaffected by
                    # this fallback.
                    if bridge_name == "bridge2" and not is_trusted_group_msg:
                        # 2026-08-28 (Stage B3 -- canonical-history
                        # persistence): records this device-originated
                        # Admin message, independent of and before the
                        # command processing below. direction='outbound'
                        # -- this is a genuine is_from_me=1 WhatsApp
                        # transport-outbound message; Fazle Core consuming
                        # it as an admin instruction is a business-purpose
                        # classification (workflow_triggered=
                        # "admin_instruction"), never a transport-direction
                        # reinterpretation (Owner directive, Stage A
                        # Correction 1). actor_type='human_device' -- typed
                        # on the linked phone itself, not fazle-core
                        # automation and not the real Hermes agent.
                        # Idempotent via source_message_ref=omsg_id (the
                        # same WhatsApp message id _mark_outgoing_processed()
                        # already dedups command-processing on, above --
                        # reused here only as a stable message_hash
                        # reference, not a second command-dedup mechanism:
                        # this INSERT can never itself execute anything, so
                        # canonical persistence and command processing stay
                        # independently idempotent). Best-effort, its own
                        # try/except, never able to block or alter the
                        # routing/execution below.
                        try:
                            await _save_message(
                                bridge_name, admin_number, otext, "outbound",
                                workflow="admin_instruction",
                                actor_type="human_device",
                                actor_id=admin_number,
                                source_message_ref=omsg_id,
                            )
                        except Exception as _persist_exc:
                            log.error(
                                f"[{bridge_name}] self-chat canonical-history persist failed for msg {omsg_id}: {_persist_exc}"
                            )

                        log.info(f"[{bridge_name}] outgoing unprefixed self-chat → routing to Hermes natural-query, msg={omsg_id}")
                        try:
                            ack = await process_admin_natural_query(
                                otext, sender_phone=admin_number, source_bridge=bridge_name,
                            )
                            if ack:
                                from modules.outbound import enqueue as enqueue_outbound
                                await enqueue_outbound(
                                    admin_number, ack,
                                    source_bridge=bridge_name,
                                    purpose="hermes-self-relay-ack",
                                    idempotency_key=f"hermes-self-relay-nl:{omsg_id}",
                                )
                        except Exception as _hermes_nl_err:
                            log.error(f"[{bridge_name}] hermes natural-query self-relay error for msg {omsg_id}: {_hermes_nl_err}")
                        continue

                if new_out_cursor > out_cursor:
                    await _set_outgoing_cursor(bridge_name, new_out_cursor)

            # Heartbeat (B15.3)
            try:
                from app.database import execute as _exec
                await _exec(
                    """INSERT INTO fazle_service_heartbeats (service, last_seen, last_message_id, queue_depth)
                       VALUES ($1, NOW(), $2, $3)
                       ON CONFLICT (service)
                       DO UPDATE SET last_seen = NOW(),
                                     last_message_id = EXCLUDED.last_message_id,
                                     queue_depth = EXCLUDED.queue_depth""",
                    f"bridge_poller:{bridge_name}",
                    (messages[-1]["id"] if messages else None),
                    len(messages),
                )
            except Exception as _hb_err:
                log.warning(f"[{bridge_name}] heartbeat write failed: {_hb_err}")

            # Update fazle_bridge_heartbeats so the watchdog stale-bridge check reflects real liveness
            try:
                from shared.queue import record_heartbeat as _record_hb
                await _record_hb(bridge_id=bridge_name)
            except Exception as _hb2_err:
                log.debug(f"[{bridge_name}] bridge_heartbeats write failed: {_hb2_err}")

            # Periodic send-gate re-enable (survives independent bridge restarts)
            # -- elapsed wall-clock time, not iteration count; see the
            # comment on _last_gate_check's initialization above.
            _now_monotonic = time.monotonic()
            if _now_monotonic - _last_gate_check >= _SEND_GATE_CHECK_INTERVAL:
                _last_gate_check = _now_monotonic
                try:
                    ok = await get_bridge().ensure_enabled()
                    if not ok:
                        log.warning(f"[{bridge_name}] send-gate periodic re-enable returned false")
                except Exception as _ge_err:
                    log.warning(f"[{bridge_name}] send-gate periodic check failed: {_ge_err}")

            # Adaptive backoff: reset to MIN on activity, ramp toward MAX when idle
            if _had_activity:
                _sleep_s = BRIDGE_POLL_MIN_S
            else:
                _sleep_s = min(_sleep_s * BRIDGE_POLL_BACKOFF, BRIDGE_POLL_MAX_S)
            if _sleep_s > BRIDGE_POLL_MIN_S:
                log.debug(f"[{bridge_name}] idle backoff: sleep={_sleep_s:.1f}s")

        except asyncio.CancelledError:
            log.info(f"[{bridge_name}] Poller stopped")
            break
        except Exception as e:
            log.exception(f"[{bridge_name}] Poll loop error: {e}")

        await asyncio.sleep(_sleep_s)


# ── Public API ─────────────────────────────────────────────────────────────────

async def start_pollers():
    """Initialize DB tables and launch background poll tasks for both bridges."""
    await init_tables()
    # Part B: ensure send-control is active on startup (survives bridge restarts)
    try:
        ok1 = await get_bridge1().ensure_enabled()
        ok2 = await get_bridge2().ensure_enabled()
        ok3 = await get_bridge3().ensure_enabled()
        log.info(f"[send-control] startup enable: bridge1={ok1} bridge2={ok2} bridge3={ok3}")
    except Exception as _sce:
        log.warning(f"[send-control] startup enable failed: {_sce}")
    for config in BRIDGE_CONFIGS:
        asyncio.create_task(_poll_bridge(config))
    log.info("Bridge pollers running for bridge1 + bridge2 + bridge3")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _build_admin_review_draft(
    *, sender_name: str, sender_mobile: str, sender_message: str,
    ai_reply: str, draft_no: str = "(pending)", category: str = "",
) -> str:
    """Build the 5-part admin draft-review message.

    Required parts (Owner spec, 2026-07-27):
      1. Sender Name + Mobile
      2. Draft No
      3. Sender's Message
      4. AI Generated Reply
      5. Options: 1. Approved  2. Edit  3. Reject

    category (Stage G7, 2026-08-28): optional sender category label
    (Client/Office/Operations/Internal/...) -- an additive 6th line, only
    rendered when a caller actually supplies one, so every existing
    caller of this function (and _save_draft(), which never passes it)
    produces byte-identical output to before this change. Derive the
    value via modules.authority_lanes -- never invent a new taxonomy
    here."""
    name_display = sender_name or "(unknown)"
    mobile_display = sender_mobile or "(unknown)"
    msg_display = (sender_message or "").strip() or "(empty)"
    reply_display = (ai_reply or "").strip() or "(no AI reply generated)"
    category_line = f"Category: {category}\n" if category else ""
    return (
        f"📋 ADMIN REVIEW DRAFT\n"
        f"─────────────────\n"
        f"Sender: {name_display} | {mobile_display}\n"
        f"Draft No: {draft_no}\n"
        f"{category_line}"
        f"─────────────────\n"
        f"Sender's Message:\n{msg_display}\n"
        f"─────────────────\n"
        f"AI Generated Reply:\n{reply_display}\n"
        f"─────────────────\n"
        f"Options:\n"
        f"  1. Approved  2. Edit  3. Reject\n"
        f"Reply: APPROVE <draft_no> | EDIT <draft_no> <text> | REJECT <draft_no>"
    )


async def _save_draft(
    source: str, recipient: str, reply_text: str, intent: str,
    *,
    sender_name: str = "",
    sender_message: str = "",
    category: str = "",
    auto_notify_admin: bool = False,
):
    """Store a suppressed reply as a draft (safe mode).

    2026-07-27: When sender_name and sender_message are provided, the draft
    text is enriched to the 5-part admin review format (Sender Name+Mobile,
    Draft No, Sender's Message, AI Reply, Options). The raw AI reply is still
    stored separately in meta for the approve/edit flow to send.

    category (Stage G7, 2026-08-28): optional, passed straight through to
    _build_admin_review_draft()'s own optional category param -- every
    existing call site (none of which pass this) is unaffected.

    auto_notify_admin (Stage G5.1, 2026-08-28): when True, actively pushes
    the admin review card (modules.supervised_draft.notify_admin_of_pending_
    draft) right after the draft transaction succeeds -- default False, so
    every pre-existing call site (all 14+ of them) is completely unaffected;
    only the DRAFT_ALWAYS_GATE call site below passes True, and only for
    the Owner-supervised-draft lane (Client/Office-Internal). Never called
    for a quality-rejected draft (those deliberately never reach the admin
    pending list at all, per the B25 hotfix below). Wrapped in its own
    try/except that can never make the draft itself disappear or raise --
    matches the established Phase 5B trigger_admin_investigation()
    discipline elsewhere in this codebase: a notification failure is
    logged, never fatal to the underlying operation it's reporting on.

    B25 hotfix: passes through draft_quality gate first. Bad drafts are still
    persisted (for audit) but with status='rejected_quality'/'rejected_fallback'
    and meta.quality_reason — they never appear in the admin pending list.
    """
    from modules.draft_quality import check_draft_quality, strip_reply_emoji
    from modules import observability as _obs
    reply_text = strip_reply_emoji(reply_text) if reply_text else reply_text
    ok, reason = check_draft_quality(reply_text)

    # Build enriched 5-part admin review draft when sender context is available
    use_enriched = bool(sender_name or sender_message)
    if use_enriched:
        # For enriched drafts, the AI reply quality is checked but the draft
        # is still created with the 5-part format even if AI reply is empty/
        # fallback — admin needs to see the sender's message regardless.
        # Insert with a placeholder draft_no, then UPDATE reply_text with
        # the real ID so admin sees the actual draft number in WhatsApp.
        draft_body_placeholder = _build_admin_review_draft(
            sender_name=sender_name,
            sender_mobile=recipient,
            sender_message=sender_message,
            ai_reply=reply_text if ok else "(AI generation failed/unavailable)",
            draft_no="__DRAFT_NO_PLACEHOLDER__",
            category=category,
        )
        meta_ctx = json.dumps({
            "ai_reply_raw": reply_text or "",
            "sender_message": sender_message,
            "sender_name": sender_name,
            "ai_failed": not ok,
            "quality_reason": reason or "",
        })
        draft_id = await create_draft_reply(
            sender=recipient,
            bridge=source,
            draft_text=draft_body_placeholder,
            role="unknown",
            intent=intent,
            context=meta_ctx,
            source_module="bridge_poller",
        )
        # Replace placeholder with the real draft ID so admin sees the
        # actual draft number they need to reply with.
        if draft_id:
            final_body = draft_body_placeholder.replace(
                "__DRAFT_NO_PLACEHOLDER__", str(draft_id)
            )
            try:
                await execute(
                    "UPDATE fazle_draft_replies SET reply_text = $1 WHERE id = $2",
                    final_body, draft_id,
                )
            except Exception as _upd_err:
                log.warning("[draft_reply] draft_no post-update failed id=%s: %s", draft_id, _upd_err)
            if not ok:
                await execute(
                    "UPDATE fazle_draft_replies SET status = $1 WHERE id = $2",
                    "rejected_fallback" if reason == "llm_fallback" else "rejected_quality",
                    draft_id,
                )
            elif auto_notify_admin:
                try:
                    from modules.supervised_draft import notify_admin_of_pending_draft
                    notify_result = await notify_admin_of_pending_draft(draft_id, category)
                    if not notify_result.get("ok"):
                        log.warning("[G5.1] auto-notify failed draft_id=%s: %s", draft_id, notify_result.get("error"))
                except Exception as _notify_err:
                    log.warning("[G5.1] auto-notify raised draft_id=%s: %s", draft_id, _notify_err)
        return

    if not ok:
        _obs.inc("drafts_rejected_total", labels={"reason": reason or "unknown", "source": source})
        log.warning(f"[draft_quality] rejected source={source} recipient={recipient} reason={reason}")
        draft_id = await create_draft_reply(
            sender=recipient,
            bridge=source,
            draft_text=reply_text or "",
            role="unknown",
            intent=intent,
            context=json.dumps({"quality_reason": reason or "unknown", "gate": "b25"}),
            source_module="bridge_poller",
        )
        if draft_id:
            await execute(
                "UPDATE fazle_draft_replies SET status = $1 WHERE id = $2",
                "rejected_fallback" if reason == "llm_fallback" else "rejected_quality",
                draft_id,
            )
        return
    await create_draft_reply(
        sender=recipient,
        bridge=source,
        draft_text=reply_text,
        role="unknown",
        intent=intent,
        source_module="bridge_poller",
    )


def _core_message_hash(source: str, source_message_ref: str) -> str:
    return hashlib.sha256(f"{source}|{source_message_ref}".encode("utf-8")).hexdigest()


def _own_bridge_numbers() -> dict[str, str]:
    """Map each of our own configured bridge numbers to the bridge name
    that owns it. A genuine external customer/candidate can never have a
    sender number equal to one of these -- these are our own business
    WhatsApp numbers."""
    return {
        num: name
        for num, name in (
            (_settings.bridge1_number, "bridge1"),
            (_settings.bridge2_number, "bridge2"),
            (_settings.bridge3_number, "bridge3"),
        )
        if num
    }


def _classify_internal_echo(source: str, sender: str, text: str) -> tuple[Optional[str], Optional[str]]:
    """2026-08-28 (Stage C7, generalizing B.5-2's narrower Hermes-only
    version): a GENERAL internal-message attribution mechanism, not an
    expanding list of message-text signatures -- see this function's own
    live motivation below.

    PRIMARY signal, pure provenance, zero content inspection: `sender` is
    one of OUR OWN configured bridge numbers (see _own_bridge_numbers()),
    but this message arrived on a DIFFERENT bridge's inbound (`source`).
    That combination alone -- sender identity, never message text --
    already proves fazle-core itself sent this content via some bridge,
    and it bounced back as a genuine is_from_me=0 row on a different
    bridge's own account. This one check covers every current AND future
    internal notification type (Hermes admin-relay replies,
    recruitment_recovery_sweep's digest, scripts/ai_console_monitor.sh's
    health alert -- confirmed live, 2026-08-28: message_ids 19309/19314
    were exactly this shape, sender=bridge1_number arriving on bridge2,
    for a recovery-sweep digest and an AI Console alert respectively,
    neither of which carries the "🤖 Hermes:" prefix) without ever adding
    a new prefix rule for the next one. Returns actor_type='system' by
    default here -- the most this signal alone can prove ("fazle-core's
    own number sent this"), actor_id=the origin bridge name.

    REFINEMENT, on top of (never instead of) the primary signal: when the
    content ALSO carries the exact, stable "🤖 Hermes:" prefix
    _deliver_hermes_reply's own outbound.enqueue() call always uses (the
    same literal modules.message_router's own cross-bridge echo guard
    checks), narrow the classification to the more precise
    actor_type='hermes', actor_id='hermes-runner' -- this is the one case
    where content genuinely adds provable information beyond sender
    identity alone, not a substitute for it.

    Returns (None, None) when this is not an internal echo at all --
    caller falls through to its normal external_user classification."""
    origin_bridge = _own_bridge_numbers().get(sender)
    if not origin_bridge or origin_bridge == source:
        return None, None
    if text.lstrip().startswith("🤖 Hermes:"):
        return "hermes", "hermes-runner"
    return "system", origin_bridge


def _parse_source_timestamp(value) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    except Exception:
        return None


async def _save_raw_core_copy(source: str, msg: dict, phone: str, text: str) -> Optional[int]:
    """Persist a raw core-side copy before OCR/routing mutates the message."""
    msg_id = str(msg.get("id") or msg.get("message_id") or "")
    if not msg_id:
        return None
    media_type = str(msg.get("media_type") or msg.get("type") or "text").strip() or "text"
    raw_text = (msg.get("content") or "").strip()
    if not raw_text:
        raw_text = f"[media:{media_type}]" if media_type != "text" else (text or "")
    # Stage D2 (2026-08-28, forward-readiness only -- group ingestion
    # itself is still blanket-excluded upstream, see _poll_bridge()'s SQL
    # query and this module's own module docstring; that exclusion is a
    # separate, larger decision Stage D was not asked to flip). A group
    # conversation's identity is the GROUP (chat_jid), not whichever
    # individual participant happens to post -- keying conversation_key
    # off `phone` for a group message would silently fragment one group
    # thread into one fake "conversation" per participant. If/when the
    # upstream exclusion is ever lifted, this makes conversation_key
    # correct from day one with no further change here.
    _chat_jid = str(msg.get("chat_jid") or "")
    _group_identity = _chat_jid if _chat_jid.endswith("@g.us") else phone
    conv_key = f"{_group_identity}:{source}" if _group_identity else None
    receiver = _source_to_receiver(source)
    source_ts = _parse_source_timestamp(msg.get("timestamp"))
    msg_hash = _core_message_hash(source, msg_id)
    context = {
        "stage": "raw_received",
        "source_message_ref": msg_id,
        "chat_jid": msg.get("chat_jid"),
        "filename": msg.get("filename"),
        "media_type": msg.get("media_type"),
        "has_processed_text": bool(msg.get("processed_text")),
        "bridge_store_preserved": True,
    }
    # 2026-08-28 (Stage B1/B4, generalized Stage C7): actor_type=
    # 'external_user' for a genuine is_from_me=0 inbound message -- EXCEPT
    # when _classify_internal_echo() proves (by sender-identity provenance,
    # never message text alone) that this is fazle-core's own content
    # bouncing back on a different bridge. Confirmed live in production,
    # 2026-08-28: message_ids 19294/19297/19300/19305/19308 (Hermes-reply
    # echoes) and separately 19309/19314 (a recovery-sweep digest and an
    # AI Console alert echo -- neither carries the "🤖 Hermes:" prefix,
    # which is exactly why the general provenance check exists, not a
    # third hardcoded prefix). This does NOT change `direction` (still
    # genuinely 'inbound' at the transport level, preserved) and does NOT
    # suppress or delete the row -- it only corrects WHO produced the
    # content.
    _echo_actor_type, _echo_actor_id = _classify_internal_echo(source, phone, raw_text)
    _actor_type = _echo_actor_type or "external_user"
    _actor_id = _echo_actor_id
    try:
        row = await fetch_one(
            """
            INSERT INTO wbom_whatsapp_messages
                (sender_number, original_sender_number, message_body, message_type,
                 direction, platform, is_processed, contact_identifier,
                 receiver_number, conversation_key, source_message_ref,
                 source_timestamp, source_context, message_hash,
                 canonical_phone, phone_last10, metadata_json, actor_type, actor_id)
            VALUES ($1, $2, $3, $4, 'inbound', $5, false, $1,
                    $6, $7, $8, $9::timestamptz, $10, $11, $12, $13, $14::jsonb, $15, $16)
            ON CONFLICT (message_hash) DO UPDATE SET
                 source_timestamp = COALESCE(wbom_whatsapp_messages.source_timestamp, EXCLUDED.source_timestamp),
                 metadata_json = COALESCE(wbom_whatsapp_messages.metadata_json, '{}'::jsonb) || EXCLUDED.metadata_json
            RETURNING message_id
            """,
            phone, phone, raw_text, media_type, source,
            receiver or None, conv_key, msg_id, source_ts,
            json.dumps(context, ensure_ascii=False), msg_hash,
            phone if phone and not phone.startswith("unresolved:") else None,
            re.sub(r"\D", "", phone or "")[-10:] if phone else None,
            json.dumps(context, ensure_ascii=False),
            _actor_type, _actor_id,
        )
        core_id = int(row["message_id"]) if row and row.get("message_id") is not None else None
        try:
            await execute(
                """
                INSERT INTO fazle_message_queue
                    (source, sender_phone, direction, message_type, content_text,
                     media_url, media_id, idempotency_key, status, processor_id, extra)
                VALUES ($1, $2, 'inbound', $3, $4, $5, $6, $7, 'processing',
                        'bridge_poller', $8::jsonb)
                ON CONFLICT (idempotency_key) DO NOTHING
                """,
                source, phone, media_type, raw_text,
                msg.get("url") or msg.get("filename"), msg_id,
                f"bridge_poller:{source}:{msg_id}",
                json.dumps({**context, "core_message_id": core_id}, ensure_ascii=False),
            )
        except Exception as q_err:
            log.warning("[core_copy] queue insert failed bridge=%s msg_id=%s err=%s", source, msg_id, q_err)
        return core_id
    except Exception as e:
        log.warning("[core_copy] raw save failed bridge=%s msg_id=%s err=%s", source, msg_id, e)
        return None


async def _finalize_core_copy(
    core_message_id: Optional[int],
    source: str,
    msg: dict,
    phone: str,
    text: str,
    *,
    identity_role: str = "",
    identity_confidence: int = 0,
    workflow: str = "",
    intent_detected: str = "",
    msg_type: str = "text",
    extracted_text: str = "",
    status: str = "done",
    error: str = "",
) -> None:
    """Update the raw core copy after OCR/identity/routing context is known."""
    msg_id = str(msg.get("id") or msg.get("message_id") or "")
    metadata = {
        "stage": "processed",
        "status": status,
        "source_message_ref": msg_id,
        "filename": msg.get("filename"),
        "media_type": msg.get("media_type"),
    }
    if error:
        metadata["error"] = error[:500]
    try:
        if core_message_id:
            await execute(
                """
                UPDATE wbom_whatsapp_messages
                   SET message_body = $2,
                       message_type = $3,
                       is_processed = true,
                       identity_role = $4,
                       identity_confidence = $5,
                       workflow_triggered = $6,
                       intent_detected = $7,
                       extracted_text = $8,
                       metadata_json = COALESCE(metadata_json, '{}'::jsonb) || $9::jsonb
                 WHERE message_id = $1
                """,
                core_message_id, text, msg_type,
                identity_role or None, identity_confidence or None,
                workflow or None, intent_detected or None, extracted_text or None,
                json.dumps(metadata, ensure_ascii=False),
            )
        else:
            await _save_message(
                source, phone, text, "inbound",
                identity_role=identity_role, identity_confidence=identity_confidence,
                workflow=workflow, intent_detected=intent_detected,
                msg_type=msg_type, extracted_text=extracted_text,
            )
        if msg_id:
            await execute(
                """
                UPDATE fazle_message_queue
                   SET status=$1,
                       attempts=attempts + 1,
                       last_error=$2,
                       processed_at=CASE WHEN $1 IN ('done','failed','skipped') THEN NOW() ELSE processed_at END
                 WHERE idempotency_key=$3
                """,
                status, error[:500] if error else None,
                f"bridge_poller:{source}:{msg_id}",
            )
    except Exception as e:
        log.warning("[core_copy] finalize failed bridge=%s msg_id=%s err=%s", source, msg_id, e)


async def _mark_core_queue_status(source: str, msg: dict, status: str, error: str = "") -> None:
    msg_id = str(msg.get("id") or msg.get("message_id") or "")
    if not msg_id:
        return
    try:
        await execute(
            """
            UPDATE fazle_message_queue
               SET status=$1,
                   last_error=$2,
                   processed_at=CASE WHEN $1 IN ('done','failed','skipped') THEN NOW() ELSE processed_at END
             WHERE idempotency_key=$3
            """,
            status, error[:500] if error else None,
            f"bridge_poller:{source}:{msg_id}",
        )
    except Exception as e:
        log.warning("[core_copy] queue status update failed bridge=%s msg_id=%s err=%s", source, msg_id, e)


async def _save_message(
    source: str,
    sender: str,
    text: str,
    direction: str,
    identity_role: str = "",
    identity_confidence: int = 0,
    workflow: str = "",
    intent_detected: str = "",
    msg_type: str = "text",
    extracted_text: str = "",
    actor_type: str = "",
    actor_id: str = "",
    source_message_ref: str = "",
) -> Optional[int]:
    """actor_type/actor_id (2026-08-28, Stage B1/B4 -- canonical-history
    attribution): who/what produced THIS row's content -- a different
    concept from identity_role (the counterparty's business identity,
    unchanged). Every existing caller of this function is fazle-core's
    own automated reply-generation (the customer-reply and internal
    "admin-hermes-reply" AI-console cascades, and
    recruitment_recovery_sweep's reconciliation of confirmed-sent
    automation replies) -- each already passes actor_type="automation"
    at its own call site, known with certainty there, never guessed here.
    Optional/blank so every pre-existing caller (and any future one that
    doesn't yet know its actor) keeps working unchanged -- stored as NULL,
    not a fabricated default.

    source_message_ref (2026-08-28, Stage B2/B3): optional stable
    reference (an outbound queue row id, a WhatsApp message id, ...) that,
    when given, is hashed with the exact same _core_message_hash()
    convention _save_raw_core_copy() already uses for inbound rows, and
    the INSERT becomes ON CONFLICT (message_hash) DO NOTHING -- genuine
    idempotency on retry, reusing the table's own pre-existing unique
    index (uq_wbom_messages_hash) rather than inventing a second dedup
    mechanism. Omitted (the default): message_hash is stored as NULL,
    byte-identical to this function's behavior before this parameter
    existed -- NULL is never equal to any other value under the unique
    index's own semantics, so pre-existing callers that don't pass this
    can never spuriously collide with each other or with a caller that
    does. Returns the inserted row's message_id, or None if the row
    already existed (a genuine retry-noop) or an error occurred."""
    _receiver = _source_to_receiver(source)
    _conv_key = f"{sender}:{source}" if sender else None
    _msg_hash = (
        _core_message_hash(f"outbound:{source}", source_message_ref)
        if source_message_ref else None
    )
    try:
        row = await fetch_one(
            """
            INSERT INTO wbom_whatsapp_messages
                (sender_number, message_body, message_type, direction,
                 platform, is_processed, contact_identifier,
                 identity_role, identity_confidence, workflow_triggered,
                 receiver_number, conversation_key, intent_detected, extracted_text,
                 actor_type, actor_id, message_hash)
            VALUES ($1, $2, $3, $4, $5, true, $1, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
            ON CONFLICT (message_hash) DO NOTHING
            RETURNING message_id
            """,
            sender, text, msg_type, direction, source,
            identity_role or None, identity_confidence or None, workflow or None,
            _receiver or None, _conv_key,
            intent_detected or None, extracted_text or None,
            actor_type or None, actor_id or None, _msg_hash,
        )
        return int(row["message_id"]) if row else None
    except Exception as e:
        log.warning(f"Message save error: {e}")
        return None


def _source_to_receiver(source: str) -> str:
    """Map bridge source name to the receiving phone number."""
    try:
        from app.config import get_settings as _gs
        s = _gs()
        return {
            "bridge1": s.bridge1_number,
            "bridge2": s.bridge2_number,
            "bridge3": s.bridge3_number,
            "meta":    s.admin_meta_number,
            "whatsapp": s.admin_meta_number,
        }.get(source, "")
    except Exception:
        return ""


async def _notify_admin_bridge(notification: dict):
    """Durably queue Bridge1-first internal notification with Bridge2 failover."""
    admin_phone = notification.get("admin_phone", "")
    text = notification.get("text", "")
    bridge_src = "bridge1"
    if not admin_phone or not text:
        return
    try:
        from modules.outbound import enqueue
        import hashlib
        purpose = notification.get("purpose", "internal-notification")
        from datetime import datetime, timezone
        hour_bucket = datetime.now(timezone.utc).strftime("%Y%m%d%H")
        digest = hashlib.sha256(
            f"{purpose}|{admin_phone}|{text}|{hour_bucket}".encode("utf-8")
        ).hexdigest()[:24]
        qid = await enqueue(
            admin_phone,
            text,
            source_bridge=bridge_src,
            fallback_channel="bridge2",
            purpose=purpose,
            idempotency_key=f"internal:{digest}",
            meta={"internal": True},
        )
        log.info("[bridge_poller] admin notification queued: phone=%s qid=%s", admin_phone, qid)
    except Exception as e:
        log.error(f"[bridge_poller] admin notify error: {e}")


async def _record_safety_incident(
    bridge: str, recipient: str, reason: str, preview: str, queue_id: str = None
) -> None:
    """STEP 3: Persist every outbound safety block to DB for permanent auditability."""
    try:
        await execute(
            """
            INSERT INTO outbound_safety_incidents
                (ts, recipient, bridge, blocked_reason, message_preview, queue_id, source_module)
            VALUES (NOW(), $1, $2, $3, $4, $5, 'bridge_poller')
            """,
            recipient, bridge, reason[:500], (preview or "")[:200], queue_id,
        )
    except Exception as e:
        log.warning(f"[SAFETY_INCIDENT] DB write failed: {e}")


async def _alert_admin_safety_block(
    bridge_name: str, phone: str, reason: str, preview: str
) -> None:
    """STEP 4: Notify admin via Bridge2 WhatsApp when outbound sanitizer blocks a message."""
    admin_phone = _settings.admin_bridge2_number
    if not admin_phone:
        return
    short_preview = (preview or "")[:60].replace("\n", " ").replace("\r", "")
    await _notify_admin_bridge({
        "admin_phone": admin_phone,
        "text": (
            f"⚠️ AI outbound blocked by safety filter\n"
            f"Reason: {reason}\n"
            f"Recipient: {phone}\n"
            f"Preview: {short_preview}"
        ),
        "bridge": "bridge1",
    })
    log.info(f"[SAFETY_ALERT] admin alerted reason={reason} recipient={phone}")


# ── Shared inbound processing pipeline ────────────────────────────────────────

async def process_bridge_inbound(
    bridge_name: str,
    phone: str,
    text: str,
    *,
    id_role: str = "unknown",
    id_conf: int = 0,
    id_name: str = "",
    message_id: str = "",
    chat_jid: str = "",
    msg_timestamp=None,
    is_extracted_media: bool = False,
    core_message_id: Optional[int] = None,
) -> None:
    """Unified bridge inbound processing pipeline.

    Called by both _poll_bridge() and the webhook handler in main.py after
    inbound save and identity detection.  Contains all safety gates so both
    entry paths behave identically.

    core_message_id (2026-08-28, Stage B5): the wbom_whatsapp_messages row
    id _save_raw_core_copy() already returned for this exact message, when
    the caller has it (only _poll_bridge()'s own SQLite-polling path does
    today -- the webhook path defaults to None and simply skips the
    intent-persistence step below, unchanged from before this parameter
    existed). Threaded through so the genuine inbound intent
    classify_intent() computes below can be written onto the SAME row the
    raw text was already captured on -- "capture/persist first, classify
    later," never a second copy of the message.

    Does NOT save the inbound message — callers must do that beforehand.

    chat_jid / msg_timestamp (2026-08-25 recovery-pass wiring, optional):
    when both are provided, enables shared.draft.should_generate_draft()'s
    "admin already replied" guard right before the customer-facing send
    decision below. That check existed in shared/draft.py but was never
    called from any live send path (unit-tested only) -- callers that omit
    these two kwargs get the prior behavior unchanged (check skipped, fail-
    open). msg_timestamp should be the actual inbound message's timestamp
    (tz-aware or naive-UTC datetime), not the current time.

    is_extracted_media (2026-08-25 recovery-pass, narrow cf_W001
    mitigation): threaded straight through to modules.message_router.
    process_message() -- see that function's docstring for the full
    rationale. True only when `text` is raw OCR/STT output that could not
    be classified as a known slip/document type.
    """
    # Defense in depth: a bare "[media:<type>]" transport placeholder is NOT
    # sender text. _poll_bridge already intercepts these before routing;
    # guard the webhook entry path (and any future caller) identically so the
    # intent classifier / RAG / semantic gate / Hermes / reply generation
    # never run on a placeholder and no transcript is ever fabricated.
    # is_extracted_media=True means `text` is genuine OCR/STT output — route it.
    if is_media_placeholder(text) and not is_extracted_media:
        log.info(
            "[%s] process_bridge_inbound: media placeholder from %s (%s) — not routed",
            bridge_name, phone, text,
        )
        return

    # 0. Recruitment eligibility + best-effort funnel entry (2026-08-21,
    # Owner Correction 1 / regression case ...1609): this used to be step 3,
    # AFTER the cooldown early-return below. A candidate's SECOND message
    # (e.g. an explicit "আমি কাজ করতে চাই" arriving less than
    # REPLY_COOLDOWN=60s after their first) never reached recruitment_
    # eligibility() at all — the cooldown `return` at old step 1 exited the
    # whole function first, so no fazle_recruitment_sessions/wbom_candidates
    # row was ever opened for them, only a silent DEBUG log line. Live-
    # confirmed 2026-08-21: sender 8801966011609's "আমি কাজ করতে চাই"
    # (received 1s after their first message, well inside the cooldown
    # window) produced zero reply and zero lead — this is the generic bug
    # behind that case, not specific to that phone number.
    #
    # Fix: compute recruitment eligibility unconditionally, and — mirroring
    # route_recruitment_message()'s own existing best-effort session-open
    # pattern for a new, high-confidence job inquiry (explicit trigger or
    # entity-layer detector) — open the funnel here too when cooldown is
    # about to suppress the reply that would otherwise have done it. This
    # never touches the reply/send path: cooldown still fully governs
    # whether anything is actually sent, exactly as before. If cooldown
    # does NOT block, step 3 below still runs its own (idempotent, cheap)
    # eligibility check for msg_intent/draft-gate purposes as it always did.
    msg_intent = classify_intent(text)
    # 2026-08-28 (Stage B5 -- genuine inbound intent persistence): snapshot
    # classify_intent()'s own raw output (e.g. "salary_query",
    # "escort_duty", "complaint" -- see modules.intent.INTENT_KEYWORDS)
    # BEFORE the recruitment-eligibility override two lines below can
    # coarsen it to "recruitment". msg_intent itself is left completely
    # unchanged for every existing downstream use (workflow_triggered
    # tagging, draft-gate logic, etc.) -- this snapshot is a read-only
    # side observation, never fed back into routing/reply logic. This is
    # the actual, already-computed inbound intent the workflow_triggered
    # column has never carried (that column reflects the coarser
    # domain/workflow OUTCOME on the reply, not this).
    _raw_inbound_intent = msg_intent
    recruit_decision = await recruitment_eligibility(
        phone, text, role=id_role, intent=msg_intent, source=bridge_name,
    )
    if recruit_decision["eligible"]:
        msg_intent = "recruitment"
    if core_message_id is not None and _raw_inbound_intent:
        try:
            await execute(
                "UPDATE wbom_whatsapp_messages SET intent_detected = $1 "
                "WHERE message_id = $2 AND intent_detected IS NULL",
                _raw_inbound_intent, core_message_id,
            )
        except Exception as _intent_persist_exc:
            log.warning(
                "[%s] intent_detected persist failed for message_id=%s: %s",
                bridge_name, core_message_id, _intent_persist_exc,
            )

    # 1. Cooldown
    _cooldown_blocked = not await _can_reply(phone)
    if _cooldown_blocked and recruit_decision.get("active_session"):
        # 2026-08-25 (P0 production incident): an active recruitment intake
        # session is a direct, expected answer to a question THIS system
        # just asked (e.g. "আপনার বয়স কত বছর?" -> "32") -- not spam risk,
        # so it must not be silently dropped by the same 60s cooldown that
        # exists to stop flooding a human with unsolicited replies. Live-
        # confirmed against real bridge3 candidates: every checked case
        # replied 13-50s after the question (well inside REPLY_COOLDOWN),
        # and the candidate's answer was dropped entirely -- no field
        # saved, no step advance, no reply ever sent for that turn, which
        # then caused the exact same question to be re-asked on their next
        # message (the "asks the same question repeatedly" / "no reply
        # after a valid age" symptom). Scoped narrowly to
        # active_session=True (an existing fazle_recruitment_sessions row
        # already open for this phone) -- every other cooldown-blocked
        # message (no session yet, or a non-recruitment sender) keeps the
        # exact prior behavior below unchanged.
        log.info(
            "[%s] cooldown would have blocked %s but an active recruitment "
            "session is a direct answer, not spam -- proceeding to full "
            "processing instead of dropping it",
            bridge_name, phone,
        )
    elif _cooldown_blocked:
        log.debug("[%s] cooldown active for %s", bridge_name, phone)
        if (
            recruit_decision.get("eligible")
            and recruit_decision.get("autosend")
        ):
            try:
                from modules.recruitment_flow import intake_message
                # 2026-08-21: force-pass intake_message()'s own narrower
                # gate -- reaching this line already required recruit_
                # decision["eligible"] and ["autosend"] to be True, i.e.
                # recruitment_eligibility() already made the authoritative
                # call (possibly via bridge3's dedicated-channel rule or
                # the detector layers, neither of which intake_message()'s
                # bare-keyword gate recognizes on its own). See the
                # identical fix + full rationale in
                # modules.recruitment_flow.route_recruitment_message().
                await intake_message(
                    phone, text, bridge_name, role=id_role, message_id=message_id or None,
                    trigger_text="চাকরি",
                )
                log.info(
                    "[%s] cooldown suppressed reply but opened recruitment funnel for %s "
                    "(explicit job-interest, reply=%s)",
                    bridge_name, phone, recruit_decision.get("reason"),
                )
            except Exception as e:
                log.debug("[recruit] best-effort cooldown-path session-open failed phone=%s: %s", phone, e)
        return

    # 2. Keyword flood
    if _check_keyword_flood(phone, text):
        log.warning("[KW_FLOOD] suppressed reply bridge=%s phone=%s", bridge_name, phone)
        return

    # 3. Intent classification / recruitment eligibility already computed at
    # step 0 above (kept as one step there so both cooldown-blocked and
    # normal paths see the identical decision; msg_intent/recruit_decision
    # are reused here unchanged, not recomputed).

    # 4. Prompt injection defense
    _inj = _detect_prompt_injection(text)
    if _inj:
        log.warning(
            "[PROMPT_INJECTION] bridge=%s phone=%s pattern=%r — quarantined",
            bridge_name, phone, _inj,
        )
        await _save_draft(
            bridge_name, phone,
            f"⛔ Quarantined (prompt injection detected).\nPattern: {_inj!r}\nOriginal: {text[:200]!r}",
            msg_intent,
            sender_name=id_name,
            sender_message=text,
        )
        return

    # 5. Full routing
    reply, admin_note = await process_message(
        phone, text, bridge_name, message_id=message_id or None,
        is_extracted_media=is_extracted_media,
    )

    # Write intent back to the most recent inbound row for this sender+source
    if msg_intent:
        try:
            await execute(
                """
                UPDATE wbom_whatsapp_messages
                SET intent_detected = $1
                WHERE id = (
                    SELECT id FROM wbom_whatsapp_messages
                    WHERE sender_number = $2 AND platform = $3 AND direction = 'inbound'
                    ORDER BY id DESC LIMIT 1
                )
                """,
                msg_intent, phone, bridge_name,
            )
        except Exception as _e:
            log.debug("[intent_writeback] %s: %s", phone, _e)

    if reply:
        # Owner decision 2026-08-04 (Phase E Part 3, Admin<->Hermes safety-
        # policy audit): everything in the `else` branch below is a
        # CUSTOMER-facing safety cascade (AI_SAFE_MODE, DRAFT_ALWAYS,
        # financial-intent draft gate, poison filter, length guard,
        # recruit gate) -- it exists to protect against risky, unreviewed
        # auto-sends to EXTERNAL numbers. It was incorrectly also applying
        # to the Admin's own conversation with Hermes: the Admin is already
        # the human who typed the question and is the approver of Hermes's
        # own answers -- there is no external recipient to protect against
        # here, and diverting the answer into a draft the Admin never
        # proactively checks on WhatsApp made Hermes appear silently broken
        # (see management_decisions.md, same-dated entry, for the incident
        # this traces back to). Admin-role replies now send directly,
        # bypassing that entire cascade. External/customer-facing replies
        # (the `else` branch) are completely unchanged -- this is an
        # additive `if` in front of the pre-existing logic, not a
        # modification to it.
        if id_role == "admin":
            # Owner-approved incident fix (2026-08-04, same day as the
            # direct-send change above): a real, active cross-bridge
            # message loop was found live -- bridge2/bridge3 (and, via the
            # same mechanism, bridge1) share overlapping WhatsApp account/
            # LID identity, so a reply sent to another bridge's OWN number
            # arrives there as a brand-new genuine inbound message, gets
            # re-recognized as admin, and replies again, bouncing
            # indefinitely. Before this session's direct-send change,
            # every admin reply landed in a draft requiring manual
            # APPROVE, which incidentally broke this loop; direct-send
            # removed that brake. Guard: if the resolved recipient phone
            # equals ANOTHER bridge's own number (not the one this reply
            # is going out on), that's a strong signal this is a bounce,
            # not a genuine human message -- divert to draft instead of
            # auto-sending, same as the pre-existing customer cascade
            # does. Replying to the Admin's own number via the SAME
            # bridge (the established self-chat pattern, e.g. bridge2's
            # existing "AI <question>" feature) is unaffected.
            _other_bridge_numbers = {
                name: num for name, num in (
                    ("bridge1", _settings.bridge1_number),
                    ("bridge2", _settings.bridge2_number),
                    ("bridge3", _settings.bridge3_number),
                )
                if name != bridge_name and num
            }
            if phone in _other_bridge_numbers.values():
                log.warning(
                    "[%s] LOOP_GUARD: admin reply recipient=%s matches another "
                    "bridge's own number -- likely cross-bridge bounce, "
                    "diverting to draft instead of auto-sending",
                    bridge_name, phone,
                )
                await _save_draft(bridge_name, phone, reply, msg_intent,
                                  sender_name=id_name, sender_message=text)
            else:
                from modules.outbound import enqueue as enqueue_outbound
                qid = await enqueue_outbound(
                    phone,
                    reply,
                    source_bridge=bridge_name,
                    fallback_channel=None,
                    purpose=f"admin-hermes-reply:{msg_intent or 'unknown'}",
                    meta={"admin_reply": True, "intent": msg_intent},
                )
                if qid:
                    await _record_reply(phone)
                    await _save_message(bridge_name, phone, reply, "outbound",
                                        identity_role=id_role, identity_confidence=id_conf,
                                        workflow=msg_intent, actor_type="automation")
                    log.info(
                        "[%s] Admin-Hermes reply sent directly (customer safety "
                        "cascade bypassed for admin role) to %s qid=%s",
                        bridge_name, phone, qid,
                    )
                else:
                    log.warning("[%s] Admin-Hermes reply enqueue failed/deduped for %s", bridge_name, phone)
        else:
            # 2026-08-25 recovery-pass wiring: "admin already manually
            # replied to this thread" guard (shared.draft.should_generate_
            # draft() -- previously dead code, unit-tested but never called
            # from any live send path; see recovery-2026-08-25 branch).
            # Runs before the entire customer-facing safety cascade below:
            # if a human admin has already answered this chat on WhatsApp
            # since this inbound came in, there is nothing for Hermes to
            # add and no draft should be created either. Fail-open by
            # design (see should_generate_draft docstring) and a no-op
            # unless the caller supplied both chat_jid and msg_timestamp.
            if chat_jid and msg_timestamp is not None:
                try:
                    from shared.draft import should_generate_draft
                    if not await should_generate_draft(chat_jid, msg_timestamp):
                        log.info(
                            "[%s] MANUAL_REPLY_GUARD: admin already replied to %s "
                            "(chat_jid=%s) — skipping auto-reply/draft",
                            bridge_name, phone, chat_jid,
                        )
                        return
                except Exception as _mrg_err:
                    log.debug(
                        "[%s] should_generate_draft check failed phone=%s: %s — "
                        "proceeding (fail-open)",
                        bridge_name, phone, _mrg_err,
                    )

            # P17-FIX-2: recruit_gate — recruitment intake bypasses SAFE MODE
            # Role-based auto-reply: read live from DB (via wa_chat_frontend) so the UI toggle takes effect immediately.
            _recruit_gate = False
            try:
                from modules.wa_chat_frontend import get_effective_auto_reply as _get_ar, _id_role_to_setting_key
                _role_key = _id_role_to_setting_key(id_role)
                _auto_reply_on = await _get_ar(_role_key)
                _recruit_autoreply_on = await _get_ar("recruitment")
            except Exception:
                _auto_reply_on = _settings.auto_reply_enabled
                _recruit_autoreply_on = _settings.recruitment_autoreply_enabled

            if not _auto_reply_on and _recruit_autoreply_on:
                _admin_set = {
                    _settings.admin_meta_number,
                    _settings.admin_bridge1_number,
                    _settings.admin_bridge2_number,
                    *_settings.admin_number_list,
                }
                if phone not in _admin_set and recruit_decision["autosend"]:
                    _recruit_gate = True
                    log.info(
                        "[P17-RECRUIT] [%s] recruit_gate=%s phone=%s role=%s",
                        bridge_name, recruit_decision["reason"], phone, id_role,
                    )

            if _auto_reply_on or _recruit_gate:
                # Phase 4.5: advance request phrase guard
                if any(phrase in text.lower() for phrase in _ADVANCE_REQUEST_PHRASES):
                    log.info("[ADVANCE_REQUEST_DRAFT] phone=%s intent=%r → forced draft", phone, msg_intent)
                    await _save_draft(bridge_name, phone, reply, msg_intent,
                                      sender_name=id_name, sender_message=text)
                    return

                # Part H: financial intent draft gate
                _financial_hit = any(fi in msg_intent.lower() for fi in _FINANCIAL_DRAFT_INTENTS)
                if _financial_hit and not _is_safe_autosend_intent(msg_intent, id_role):
                    log.info("[FINANCIAL_DRAFT] phone=%s intent=%r → forced draft", phone, msg_intent)
                    await _save_draft(bridge_name, phone, reply, msg_intent,
                                      sender_name=id_name, sender_message=text)
                    return

                # Phase 4.5: complaint-phrase override
                if _financial_hit and any(phrase in text.lower() for phrase in _COMPLAINT_PHRASES):
                    log.info(
                        "[COMPLAINT_DRAFT] phone=%s intent=%r → complaint phrase → forced draft",
                        phone, msg_intent,
                    )
                    await _save_draft(bridge_name, phone, reply, msg_intent,
                                      sender_name=id_name, sender_message=text)
                    return

                if _financial_hit:
                    log.info("[SAFE_AUTOSEND] phone=%s intent=%r → bypassing financial draft gate", phone, msg_intent)

                # DRAFT_ALWAYS gate (phone, role, name, prefix)
                draft_gate = await _is_draft_always(phone, id_role, id_name)
                if draft_gate:
                    log.info(
                        "[DRAFT_ALWAYS] phone=%s intent=%r role=%s name=%r → no direct auto-reply",
                        phone, msg_intent, id_role, id_name,
                    )

                # STEP 6: risk level gate
                if not draft_gate:
                    _contact_risk = _settings.contact_risk_map.get(phone, "")
                    if _contact_risk == "admin_review_only":
                        draft_gate = True
                        log.info("[RISK_LEVEL] %s risk=admin_review_only → forced draft", phone)
                    elif _contact_risk == "monitored":
                        log.info("[RISK_LEVEL] %s risk=monitored — reply allowed with extra logging", phone)

                if draft_gate:
                    log.info("[DRAFT-GATE] %s name=%r role=%s → draft", phone, id_name, id_role)
                    # Stage G5.1 (2026-08-28): auto-notify admin only for the
                    # Owner-supervised-draft lane (Client/Office-Internal) --
                    # resolve_authority_lane() classifies purely from id_role
                    # (never message text), matching every other lane
                    # decision in this codebase. Every other reason a
                    # message lands in DRAFT_ALWAYS (accountant, risk-level
                    # gate, complaint phrase, loop detection) is left
                    # exactly as before -- silent draft, no active push --
                    # unless it also happens to be a Client/Office role.
                    from modules.authority_lanes import resolve_authority_lane, LaneContext, OWNER_SUPERVISED_DRAFT
                    _lane_decision = resolve_authority_lane(LaneContext(identity_role=id_role or "unknown"))
                    _g51_category = ""
                    if _lane_decision.lane == OWNER_SUPERVISED_DRAFT and id_role in (
                        "vip_client", "security_service_client", "client_escort_buyer", "escort_client", "repeat_client",
                    ):
                        _g51_category = "Client"
                    elif _lane_decision.lane == OWNER_SUPERVISED_DRAFT and id_role in (
                        "internal_coordinator", "internal_operation", "employee_internal",
                    ):
                        _g51_category = "Office/Internal"
                    await _save_draft(bridge_name, phone, reply, msg_intent,
                                      sender_name=id_name, sender_message=text,
                                      category=_g51_category, auto_notify_admin=bool(_g51_category))
                else:
                    # STEP 7: loop detection
                    if _check_loop_detect(phone):
                        log.warning("[LOOP_DETECT] autoreply paused for %s bridge=%s", phone, bridge_name)
                        await _save_draft(bridge_name, phone, reply, msg_intent,
                                          sender_name=id_name, sender_message=text)
                        return

                    # Outbound poison block
                    _poison_hit = next((p for p in _OUTBOUND_POISON if p in reply), None)
                    if _poison_hit:
                        _block_reason = f"POISON:{_poison_hit!r}"
                        log.error(
                            "[OUTBOUND_SANITIZER_BLOCK] bridge=%s phone=%s pattern=%r",
                            bridge_name, phone, _poison_hit,
                        )
                        await _save_draft(bridge_name, phone, reply, msg_intent,
                                          sender_name=id_name, sender_message=text)
                        await _record_safety_incident(bridge_name, phone, _block_reason, reply)
                        await _alert_admin_safety_block(bridge_name, phone, _block_reason, reply)
                        return

                    # Length/structure guard
                    # 2026-08-24 (Owner BREAK_GLASS_APPROVED for temporary live
                    # Hermes Recruitment/Employee conversational authority):
                    # skip STRUCT guard for legitimate recruitment/employee
                    # conversational replies. Salary breakdowns, office
                    # address responses, and multi-section duty/role
                    # explanations naturally exceed 400 chars and use
                    # markdown structure; the STRUCT guard diverts them to
                    # drafts and admin alerts but the production draft queue
                    # is not consumed by the Hermes live Recruitment/Employee
                    # path -- so legit replies got stuck. Other safety
                    # layers (POISON filter above, AI_SAFE_MODE below,
                    # DRAFT_ALWAYS, contact-risk, loop detection,
                    # financial-intent draft gate) remain fully operational
                    # for non-Recruitment/non-Employee contexts. Protected
                    # flows (payroll, attendance, escort, admin alerts) are
                    # not touched because this only relaxes a length/format
                    # heuristic, never any write path or authorization gate.
                    _reply_len = len(reply)
                    _has_table = reply.count(" | ") >= 2 or ":---" in reply
                    _is_long = _reply_len > 400
                    _has_headings = reply.count("\n#") >= 2
                    _is_recruitment_employee = msg_intent in ("recruitment", "employee_inquiry")
                    if (not _is_recruitment_employee) and (_is_long or _has_table or _has_headings):
                        _block_reason = f"STRUCT:len={_reply_len},table={_has_table},headings={_has_headings}"
                        log.warning(
                            "[OUTBOUND_LENGTH_GUARD] bridge=%s phone=%s len=%d table=%s headings=%s",
                            bridge_name, phone, _reply_len, _has_table, _has_headings,
                        )
                        await _save_draft(bridge_name, phone, reply, msg_intent,
                                          sender_name=id_name, sender_message=text)
                        await _record_safety_incident(bridge_name, phone, _block_reason, reply)
                        await _alert_admin_safety_block(bridge_name, phone, _block_reason, reply)
                        return

                    # AI_SAFE_MODE
                    if _settings.ai_safe_mode:
                        _safe_reason = None
                        if len(reply) > 200:
                            _safe_reason = f"SAFE_MODE:LONG({len(reply)}chars)"
                        elif (id_conf or 0) < 50:
                            _safe_reason = f"SAFE_MODE:LOW_CONF({id_conf})"
                        elif msg_intent in ("unknown", "unclear", ""):
                            _safe_reason = f"SAFE_MODE:UNCERTAIN_INTENT({msg_intent!r})"
                        if _safe_reason:
                            log.warning("[AI_SAFE_MODE] %s bridge=%s phone=%s", _safe_reason, bridge_name, phone)
                            await _save_draft(bridge_name, phone, reply, msg_intent,
                                              sender_name=id_name, sender_message=text)
                            return

                    # Send
                    from modules.outbound import enqueue as enqueue_outbound
                    fallback = None
                    if msg_intent != "recruitment":
                        # bridge3 (Admin device) intentionally has no cross-bridge
                        # fallback -- falling over to bridge1/bridge2 would send an
                        # Admin-directed reply from a different WhatsApp number,
                        # which is more surprising than a retry via the outbound
                        # queue. See shared/bridge_orchestrator.py's bridge3 note.
                        fallback = {"bridge1": "bridge2", "bridge2": "bridge1"}.get(bridge_name)
                    qid = await enqueue_outbound(
                        phone,
                        reply,
                        source_bridge=bridge_name,
                        fallback_channel=fallback,
                        purpose=f"customer-reply:{msg_intent or 'unknown'}",
                        idempotency_key=f"{bridge_name}-recruit:{hashlib.sha256(f'{phone}|{text}'.encode()).hexdigest()[:40]}"
                        if msg_intent == "recruitment" else None,
                        meta={"customer_reply": True, "intent": msg_intent},
                    )
                    if qid:
                        await _record_reply(phone)
                        await _save_message(bridge_name, phone, reply, "outbound",
                                            identity_role=id_role, identity_confidence=id_conf,
                                            workflow=msg_intent, actor_type="automation")
                        log.info("[%s] Queued reply to %s qid=%s", bridge_name, phone, qid)
                    else:
                        log.warning("[%s] Reply enqueue failed/deduped for %s", bridge_name, phone)
            else:
                log.warning("SAFE MODE: reply suppressed for %s (%s). Saving draft.", phone, bridge_name)
                await _save_draft(bridge_name, phone, reply, msg_intent,
                                  sender_name=id_name, sender_message=text)

    if admin_note:
        if admin_note.get("purpose") == "draft-only-contact-review":
            await _save_draft(
                bridge_name,
                phone,
                admin_note.get("text") or text,
                msg_intent or "draft_only_contact",
                sender_name=id_name,
                sender_message=text,
            )
        if _settings.internal_notifications_enabled:
            await _notify_admin_bridge(admin_note)
