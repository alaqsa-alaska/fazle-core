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
from datetime import datetime, timezone, timedelta
from typing import Optional

from app.config import get_settings
from app.database import execute, fetch_one, fetch_all, fetch_val
from shared.draft_reply import create_draft_reply
from app.bridge import get_bridge1, get_bridge2
from modules.message_router import process_message, get_primary_admin, _is_safe_autosend_intent
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
) -> tuple[list[dict], datetime]:
    """
    Fetch inbound personal messages newer than since_ts from SQLite.
    Returns (messages, max_ts_seen).
    Runs synchronously — call via run_in_executor.
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

        # Normalize cursor boundary to UTC so bridge-local timezone offsets
        # cannot cause false "older/newer" comparisons in SQLite filtering.
        ts_utc = since_ts.astimezone(timezone.utc).replace(microsecond=0)
        ts_iso = ts_utc.isoformat(sep=" ")

        rows = con.execute(
            """
            SELECT id, chat_jid, sender, content, timestamp, media_type, processed_text,
                   filename, url
            FROM messages
            WHERE is_from_me = 0
              AND datetime(timestamp) > datetime(:since)
              AND chat_jid NOT LIKE '%@newsletter'
              AND chat_jid != 'status@broadcast'
              AND chat_jid NOT LIKE '%@g.us'
              AND (media_type IS NULL
                   OR media_type NOT IN ('reaction', 'receipt', 'revoke', 'deleted', 'protocol'))
              AND (
                content IS NOT NULL
                OR processed_text IS NOT NULL
                OR ((media_type IS NOT NULL) AND (filename IS NOT NULL OR url IS NOT NULL))
              )
            ORDER BY datetime(timestamp) ASC
            LIMIT 50
            """,
            {"since": ts_iso},
        ).fetchall()

        # v1.0.2: visibility on what we filter out (groups/newsletters/status)
        try:
            skipped_row = con.execute(
                """
                SELECT
                  SUM(CASE WHEN chat_jid LIKE '%@g.us' THEN 1 ELSE 0 END) AS groups,
                  SUM(CASE WHEN chat_jid LIKE '%@newsletter' THEN 1 ELSE 0 END) AS newsletters,
                  SUM(CASE WHEN chat_jid = 'status@broadcast' THEN 1 ELSE 0 END) AS status
                FROM messages
                WHERE is_from_me = 0
                  AND datetime(timestamp) > datetime(:since)
                """,
                {"since": ts_iso},
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


def _fetch_outgoing_escort_completions(
    messages_db: str,
    since_ts: datetime,
) -> tuple[list[dict], datetime]:
    """
    Fetch outgoing (is_from_me=1) DM messages from bridge2 SQLite newer than
    since_ts. Only returns messages that look like completed escort slips so
    that handle_admin_escort_completion() can be called on them.

    These messages are sent BY the admin TO clients via bridge2, so they never
    appear in the inbound (is_from_me=0) query above. We need a separate cursor
    for these so we don't re-process on every poll.
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
              AND chat_jid NOT LIKE '%@g.us'
              AND content IS NOT NULL
            ORDER BY datetime(timestamp) ASC
            LIMIT 100
            """,
            {"since": ts_iso},
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


def _requires_legacy_workflow(text: str, msg: dict) -> bool:
    """Operational messages must use Fazle's workflow router, not social recruiting."""
    t = (text or "").lower()
    try:
        from modules.escort import is_completed_escort_draft
        if is_completed_escort_draft(text):
            return True
    except Exception:
        pass
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


def _is_draft_always(phone: str, role: str, display_name: str) -> bool:
    """Return True when this contact must always be drafted, never auto-sent.

    Checks (in order):
      1. Phone is in DRAFT_ALWAYS_PHONES explicit list
      2. Identity role matches DRAFT_ALWAYS_ROLES (e.g. accountant, vip_client)
      3. Display name contains a DRAFT_ALWAYS_NAMES substring
      4. Display name starts with a DRAFT_NAME_PREFIXES prefix (client, escort, office…)
    """
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

    _poll_iter = 0  # tracks iterations for periodic send-gate re-check
    _gate_check_every = max(1, _SEND_GATE_CHECK_INTERVAL // POLL_INTERVAL)
    _sleep_s = BRIDGE_POLL_MIN_S

    while True:
        try:
            _had_activity = False
            loop = asyncio.get_event_loop()
            messages, new_cursor = await loop.run_in_executor(
                None, _fetch_new_messages, messages_db, whatsapp_db, cursor
            )

            if messages:
                log.info(f"[{bridge_name}] {len(messages)} new message(s) to process")
                _had_activity = True

            dedup_skipped = 0
            inbound_saved = 0
            unresolved_router_skipped = 0

            for msg in messages:
                msg_id: str = msg["id"]
                phone: str = msg["phone"]
                text: str = msg["text"]

                # Dedup check
                if await _is_processed(msg_id, bridge_name):
                    dedup_skipped += 1
                    continue

                _extracted_text = ""  # filled by media processors below; stored in extracted_text column
                core_message_id = await _save_raw_core_copy(bridge_name, msg, phone, text)

                # Bridge-side OCR may already populate processed_text. Previously
                # release images with processed_text skipped the release workflow
                # because the full OCR block only ran when text was empty.
                if msg.get("media_type") == "image" and text:
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
                if msg.get("media_type") == "image" and not text and msg.get("filename"):
                    from modules.ocr_processor import classify_from_context
                    store_dir = os.path.dirname(messages_db)
                    file_path = os.path.join(store_dir, msg.get("chat_jid", ""), msg["filename"])
                    try:
                        # STEP 1: Lightweight context check (no OCR yet)
                        ctx_rows = []
                        try:
                            _ctx_con = sqlite3.connect(f"file:{messages_db}?mode=ro", uri=True, check_same_thread=False, timeout=3.0)
                            _ctx_con.row_factory = sqlite3.Row
                            ctx_rows = _ctx_con.execute(
                                """SELECT content FROM messages
                                   WHERE chat_jid = ? AND is_from_me = 0
                                     AND datetime(timestamp) < datetime(?)
                                     AND content IS NOT NULL
                                   ORDER BY datetime(timestamp) DESC LIMIT 5""",
                                (msg["chat_jid"], msg["timestamp"]),
                            ).fetchall()
                            _ctx_con.close()
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
                    finally:
                        await _mark_processed(msg_id, bridge_name, phone)
                        log.info("[IMG_MARK_PROCESSED_FINAL] bridge=%s msg_id=%s phone=%s", bridge_name, msg_id, phone)
                else:
                    # Audio transcription for voice messages
                    if msg.get("media_type") in ("audio", "ptt") and not text and msg.get("filename"):
                        from modules.voice_processor import process_voice as _proc_voice
                        store_dir = os.path.dirname(messages_db)
                        file_path = os.path.join(store_dir, msg.get("chat_jid", ""), msg["filename"])
                        try:
                            voice_result = await _proc_voice(file_path)
                            if voice_result["confident"]:
                                text = voice_result["transcript"]
                                _extracted_text = voice_result["transcript"]
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
                    ) and not text and msg.get("filename"):
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

                # Detect identity before routing (for logging)
                from modules.identity_brain import detect_identity
                identity = await detect_identity(phone, text)
                id_role = identity["identity_role"]
                id_conf = identity["identity_confidence"]
                id_name = (identity.get("display_name") or "").strip()

                # Update the raw core copy with identity metadata + extracted fields.
                await _finalize_core_copy(
                    core_message_id, bridge_name, msg, phone, text,
                    identity_role=id_role, identity_confidence=id_conf,
                    msg_type=msg.get("type") or "text",
                    extracted_text=_extracted_text,
                    status="processing",
                )
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

                if (
                    os.getenv("SOCIAL_AUTO_REPLY_SINGLE_ENGINE", "true").lower() in ("1", "true", "yes")
                    and not _requires_legacy_workflow(text, msg)
                ):
                    if await _check_social_daemon_health():
                        try:
                            from modules.social_auto_reply import ingest_social_event
                            await ingest_social_event(
                                platform=bridge_name,
                                event_type="message",
                                sender_id=phone,
                                text=text,
                                message_id=str(msg.get("id") or msg.get("message_id") or ""),
                                media_flag=False,
                                raw_payload=dict(msg),
                            )
                        except Exception as _social_err:
                            log.warning(f"[social] poller ingest failed bridge={bridge_name} phone={phone}: {_social_err}")
                        log.debug(f"[{bridge_name}] social daemon is single reply engine; poller legacy router/send skipped for {phone}")
                        await _mark_core_queue_status(bridge_name, msg, "done")
                        continue
                    else:
                        log.error(
                            f"[social] daemon heartbeat stale >300s — bridge={bridge_name} phone={phone}; "
                            "falling through to legacy router"
                        )
                        # fall through to legacy path below — no 'continue'

                try:
                    await process_bridge_inbound(
                        bridge_name, phone, text,
                        id_role=id_role, id_conf=id_conf, id_name=id_name,
                    )
                    await _mark_core_queue_status(bridge_name, msg, "done")
                except Exception as route_err:
                    await _mark_core_queue_status(bridge_name, msg, "failed", f"routing_error:{route_err}")
                    raise

            if bridge_name == "bridge1":
                log.info(
                    "[GAP_DIAG] bridge=%s poll_summary fetched=%d dedup_skipped=%d inbound_saved=%d unresolved_router_skipped=%d cursor_from=%s cursor_to=%s",
                    bridge_name,
                    len(messages),
                    dedup_skipped,
                    inbound_saved,
                    unresolved_router_skipped,
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
                out_msgs, new_out_cursor = await loop2.run_in_executor(
                    None, _fetch_outgoing_escort_completions, messages_db, out_cursor
                )

                if out_msgs:
                    log.info(f"[{bridge_name}] {len(out_msgs)} outgoing message(s) to check for escort completions")
                    _had_activity = True

                from modules.escort import is_completed_escort_draft, handle_admin_escort_completion
                from modules.escort_lifecycle import is_release_confirmation, handle_admin_release_confirmation

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

                    # Phase 22: release confirmation takes priority
                    if is_release_confirmation(otext):
                        log.info(f"[{bridge_name}] outgoing RELEASE confirmation → jid={chat_jid}")
                        try:
                            await handle_admin_release_confirmation(otext, chat_jid, source=bridge_name)
                        except Exception as _rc_err:
                            log.error(f"[{bridge_name}] release confirm error for msg {omsg_id}: {_rc_err}")
                        continue

                    if not is_completed_escort_draft(otext):
                        continue

                    log.info(f"[{bridge_name}] outgoing escort completion detected → jid={chat_jid}")
                    try:
                        recipient_phone = re.sub(r"\D", "", chat_jid or "")
                        await handle_admin_escort_completion(
                            otext,
                            admin_number,
                            bridge_name,
                            recipient_phone=recipient_phone,
                        )
                    except Exception as _ec_err:
                        log.error(f"[{bridge_name}] escort completion error for msg {omsg_id}: {_ec_err}")

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
            _poll_iter += 1
            if _poll_iter % _gate_check_every == 0:
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
        log.info(f"[send-control] startup enable: bridge1={ok1} bridge2={ok2}")
    except Exception as _sce:
        log.warning(f"[send-control] startup enable failed: {_sce}")
    for config in BRIDGE_CONFIGS:
        asyncio.create_task(_poll_bridge(config))
    log.info("Bridge pollers running for bridge1 + bridge2")


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _save_draft(source: str, recipient: str, reply_text: str, intent: str):
    """Store a suppressed reply as a draft (safe mode).

    B25 hotfix: passes through draft_quality gate first. Bad drafts are still
    persisted (for audit) but with status='rejected_quality'/'rejected_fallback'
    and meta.quality_reason — they never appear in the admin pending list.
    """
    from modules.draft_quality import check_draft_quality, strip_reply_emoji
    from modules import observability as _obs
    reply_text = strip_reply_emoji(reply_text) if reply_text else reply_text
    ok, reason = check_draft_quality(reply_text)
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
    conv_key = f"{phone}:{source}" if phone else None
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
    try:
        row = await fetch_one(
            """
            INSERT INTO wbom_whatsapp_messages
                (sender_number, original_sender_number, message_body, message_type,
                 direction, platform, is_processed, contact_identifier,
                 receiver_number, conversation_key, source_message_ref,
                 source_timestamp, source_context, message_hash,
                 canonical_phone, phone_last10, metadata_json)
            VALUES ($1, $2, $3, $4, 'inbound', $5, false, $1,
                    $6, $7, $8, $9::timestamptz, $10, $11, $12, $13, $14::jsonb)
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
):
    _receiver = _source_to_receiver(source)
    _conv_key = f"{sender}:{source}" if sender else None
    try:
        await execute(
            """
            INSERT INTO wbom_whatsapp_messages
                (sender_number, message_body, message_type, direction,
                 platform, is_processed, contact_identifier,
                 identity_role, identity_confidence, workflow_triggered,
                 receiver_number, conversation_key, intent_detected, extracted_text)
            VALUES ($1, $2, $3, $4, $5, true, $1, $6, $7, $8, $9, $10, $11, $12)
            """,
            sender, text, msg_type, direction, source,
            identity_role or None, identity_confidence or None, workflow or None,
            _receiver or None, _conv_key,
            intent_detected or None, extracted_text or None,
        )
    except Exception as e:
        log.warning(f"Message save error: {e}")


def _source_to_receiver(source: str) -> str:
    """Map bridge source name to the receiving phone number."""
    try:
        from app.config import get_settings as _gs
        s = _gs()
        return {
            "bridge1": s.bridge1_number,
            "bridge2": s.bridge2_number,
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
) -> None:
    """Unified bridge inbound processing pipeline.

    Called by both _poll_bridge() and the webhook handler in main.py after
    inbound save and identity detection.  Contains all safety gates so both
    entry paths behave identically.

    Does NOT save the inbound message — callers must do that beforehand.
    """
    # 1. Cooldown
    if not await _can_reply(phone):
        log.debug("[%s] cooldown active for %s", bridge_name, phone)
        return

    # 2. Keyword flood
    if _check_keyword_flood(phone, text):
        log.warning("[KW_FLOOD] suppressed reply bridge=%s phone=%s", bridge_name, phone)
        return

    # 3. Intent classification (router re-classifies internally; this copy drives draft gates)
    msg_intent = classify_intent(text)
    recruit_decision = await recruitment_eligibility(
        phone, text, role=id_role, intent=msg_intent,
    )
    if recruit_decision["eligible"]:
        msg_intent = "recruitment"

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
        )
        return

    # 5. Full routing
    reply, admin_note = await process_message(phone, text, bridge_name)

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
                await _save_draft(bridge_name, phone, reply, msg_intent)
                return

            # Part H: financial intent draft gate
            _financial_hit = any(fi in msg_intent.lower() for fi in _FINANCIAL_DRAFT_INTENTS)
            if _financial_hit and not _is_safe_autosend_intent(msg_intent, id_role):
                log.info("[FINANCIAL_DRAFT] phone=%s intent=%r → forced draft", phone, msg_intent)
                await _save_draft(bridge_name, phone, reply, msg_intent)
                return

            # Phase 4.5: complaint-phrase override
            if _financial_hit and any(phrase in text.lower() for phrase in _COMPLAINT_PHRASES):
                log.info(
                    "[COMPLAINT_DRAFT] phone=%s intent=%r → complaint phrase → forced draft",
                    phone, msg_intent,
                )
                await _save_draft(bridge_name, phone, reply, msg_intent)
                return

            if _financial_hit:
                log.info("[SAFE_AUTOSEND] phone=%s intent=%r → bypassing financial draft gate", phone, msg_intent)

            # DRAFT_ALWAYS gate (phone, role, name, prefix)
            draft_gate = _is_draft_always(phone, id_role, id_name)
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
                await _save_draft(bridge_name, phone, reply, msg_intent)
            else:
                # STEP 7: loop detection
                if _check_loop_detect(phone):
                    log.warning("[LOOP_DETECT] autoreply paused for %s bridge=%s", phone, bridge_name)
                    await _save_draft(bridge_name, phone, reply, msg_intent)
                    return

                # Outbound poison block
                _poison_hit = next((p for p in _OUTBOUND_POISON if p in reply), None)
                if _poison_hit:
                    _block_reason = f"POISON:{_poison_hit!r}"
                    log.error(
                        "[OUTBOUND_SANITIZER_BLOCK] bridge=%s phone=%s pattern=%r",
                        bridge_name, phone, _poison_hit,
                    )
                    await _save_draft(bridge_name, phone, reply, msg_intent)
                    await _record_safety_incident(bridge_name, phone, _block_reason, reply)
                    await _alert_admin_safety_block(bridge_name, phone, _block_reason, reply)
                    return

                # Length/structure guard
                _reply_len = len(reply)
                _has_table = reply.count(" | ") >= 2 or ":---" in reply
                _is_long = _reply_len > 400
                _has_headings = reply.count("\n#") >= 2
                if _is_long or _has_table or _has_headings:
                    _block_reason = f"STRUCT:len={_reply_len},table={_has_table},headings={_has_headings}"
                    log.warning(
                        "[OUTBOUND_LENGTH_GUARD] bridge=%s phone=%s len=%d table=%s headings=%s",
                        bridge_name, phone, _reply_len, _has_table, _has_headings,
                    )
                    await _save_draft(bridge_name, phone, reply, msg_intent)
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
                        await _save_draft(bridge_name, phone, reply, msg_intent)
                        return

                # Send
                from modules.outbound import enqueue as enqueue_outbound
                fallback = None
                if msg_intent != "recruitment":
                    fallback = "bridge2" if bridge_name == "bridge1" else "bridge1"
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
                                        workflow=msg_intent)
                    log.info("[%s] Queued reply to %s qid=%s", bridge_name, phone, qid)
                else:
                    log.warning("[%s] Reply enqueue failed/deduped for %s", bridge_name, phone)
        else:
            log.warning("SAFE MODE: reply suppressed for %s (%s). Saving draft.", phone, bridge_name)
            await _save_draft(bridge_name, phone, reply, msg_intent)

    if admin_note:
        if admin_note.get("purpose") == "draft-only-contact-review":
            await _save_draft(
                bridge_name,
                phone,
                admin_note.get("text") or text,
                msg_intent or "draft_only_contact",
            )
        if _settings.internal_notifications_enabled:
            await _notify_admin_bridge(admin_note)
