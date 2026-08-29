"""
Fazle Payroll Engine — Historical sync engine.

Reads from bridge SQLite message stores and backfills fpe_wa_messages
with historical owner→accountant payment messages.

Key differences from bridge_poller:
- Reads ALL messages (both from_me=1 and from_me=0) for the configured chat JIDs
- Does NOT send replies — pure read/ingest
- Processes from oldest to newest, updating checkpoints per batch
- LID resolution uses the same whatsapp.db lid_map technique
- Runs in a thread pool for SQLite (never blocks asyncio loop)

Target chat JIDs (owner → accountant, historical payment data):
  Bridge2: owner=8801880446111 → accountant=8801844836824@s.whatsapp.net
  Bridge1: Can be configured via FPE_SYNC_CHAT_JIDS setting

SQLite paths (same as bridge_poller BRIDGE_CONFIGS):
  Bridge1: /home/azim/whatsapp1/store/messages.db
  Bridge2: /home/azim/whatsapp2/store/messages.db
"""
from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from app.config import get_settings
from shared.sqlite_compat import processed_text_column
from .checkpoint import get_checkpoint, update_checkpoint, touch_checkpoint
from .ingestion import ingest_message
from .models import IngestionRequest

log = logging.getLogger("fazle.fpe.hsync")
_settings = get_settings()

# Bridge configurations (matches bridge_poller.BRIDGE_CONFIGS)
_BRIDGE_CONFIGS = [
    {
        "name": "bridge1",
        "source_number": _settings.admin_bridge1_number,
        "messages_db": "/home/azim/whatsapp1/store/messages.db",
        "whatsapp_db": "/home/azim/whatsapp1/store/whatsapp.db",
        # When all_dms=True, enumerate ALL individual DM chat JIDs from SQLite
        # instead of relying solely on FPE_SYNC_CHAT_JIDS.  Controlled by
        # env var BRIDGE1_INGEST_ALL_DMS=true.
    },
    {
        "name": "bridge2",
        "source_number": _settings.admin_bridge2_number,
        "messages_db": "/home/azim/whatsapp2/store/messages.db",
        "whatsapp_db": "/home/azim/whatsapp2/store/whatsapp.db",
    },
]

BATCH_SIZE = 200      # rows per SQLite fetch
SYNC_INTERVAL = int(os.getenv("FPE_HSYNC_INTERVAL_S", "15"))  # seconds between full sync passes


# ── Public API ────────────────────────────────────────────────────────

async def run_historical_sync_once(chat_jids: Optional[list[str]] = None) -> dict:
    """
    Run one full sync pass across all bridge configs for the given chat JIDs.
    If chat_jids is None, reads FPE_SYNC_CHAT_JIDS from settings.

    Returns per-bridge ingestion counts.
    """
    settings = get_settings()
    # chat_jids can be passed or read from env (comma-separated)
    target_jids: list[str] = chat_jids or _get_target_jids(settings)

    # BRIDGE1_INGEST_ALL_DMS: when true, bridge1 ingests ALL individual DM JIDs
    bridge1_all_dms: bool = os.getenv("BRIDGE1_INGEST_ALL_DMS", "false").lower() in ("true", "1", "yes")

    if bridge1_all_dms:
        log.warning(
            "[fpe.hsync] bridge1 all_dms override ignored; explicit chat JIDs only"
        )

    if not target_jids and not bridge1_all_dms:
        log.warning("[fpe.hsync] No target chat JIDs configured — skipping historical sync")
        return {}

    results = {}
    for bridge in _BRIDGE_CONFIGS:
        if not target_jids:
            continue
        count = await _sync_bridge(bridge, target_jids)
        results[bridge["name"]] = count

    return results


async def historical_sync_loop(chat_jids: Optional[list[str]] = None) -> None:
    """
    Continuous background loop: run sync every SYNC_INTERVAL seconds.
    Designed to be started as an asyncio task.
    """
    log.info("[fpe.hsync] historical sync loop started (interval=%ds)", SYNC_INTERVAL)
    while True:
        try:
            results = await run_historical_sync_once(chat_jids)
            total = sum(results.values())
            if total:
                log.info("[fpe.hsync] pass complete: %s", results)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            log.error("[fpe.hsync] sync pass error: %s", exc, exc_info=True)
        await asyncio.sleep(SYNC_INTERVAL)


# ── Bridge-level sync ─────────────────────────────────────────────────

async def _sync_bridge_all_dms(bridge: dict) -> int:
    """
    Sync bridge1 for ALL individual DM chat JIDs found in its SQLite.
    Only processes @s.whatsapp.net and @lid JIDs (excludes groups/@g.us).
    Used when BRIDGE1_INGEST_ALL_DMS=true.
    """
    loop = asyncio.get_event_loop()
    jids = await loop.run_in_executor(
        None, _fetch_all_dm_jids_sync, bridge["messages_db"]
    )
    if not jids:
        log.debug("[fpe.hsync] bridge1 all_dms: no DM JIDs found")
        return 0
    log.info("[fpe.hsync] bridge1 all_dms: syncing %d DM JIDs", len(jids))
    return await _sync_bridge(bridge, jids)


def _fetch_all_dm_jids_sync(messages_db: str) -> list[str]:
    """
    Return all individual DM chat JIDs from the SQLite messages store.
    Excludes group chats (@g.us), newsletters (@newsletter), and status.
    """
    jids: list[str] = []
    try:
        con = sqlite3.connect(
            f"file:{messages_db}?mode=ro", uri=True, check_same_thread=False
        )
        rows = con.execute(
            """
            SELECT DISTINCT chat_jid FROM messages
            WHERE chat_jid NOT LIKE '%@g.us'
              AND chat_jid NOT LIKE '%@newsletter'
              AND chat_jid != 'status@broadcast'
              AND (chat_jid LIKE '%@s.whatsapp.net' OR chat_jid LIKE '%@lid')
            """
        ).fetchall()
        con.close()
        jids = [r[0] for r in rows]
    except sqlite3.OperationalError as exc:
        log.warning("[fpe.hsync] _fetch_all_dm_jids_sync error: %s", exc)
    return jids


async def _sync_bridge(bridge: dict, target_jids: list[str]) -> int:
    """Sync one bridge for all target JIDs. Returns total messages ingested."""
    total_ingested = 0

    for jid in target_jids:
        checkpoint = await get_checkpoint(bridge["name"], bridge["source_number"], jid)
        last_ts = checkpoint["last_timestamp"] if checkpoint else None

        # Run SQLite fetch in thread pool — never block asyncio loop
        loop = asyncio.get_event_loop()
        rows, lid_map = await loop.run_in_executor(
            None,
            _fetch_messages_sync,
            bridge["messages_db"],
            bridge["whatsapp_db"],
            jid,
            last_ts,
            BATCH_SIZE,
        )

        if not rows:
            # No new messages — still touch the checkpoint so UI shows sync is alive
            if checkpoint:
                await touch_checkpoint(bridge["name"], bridge["source_number"], jid)
            continue

        ingested = 0
        last_msg_id = None
        last_msg_ts = None

        for row in rows:
            fpe_id = await _ingest_row(row, bridge, lid_map)
            if fpe_id is not None:
                ingested += 1
            last_msg_id = row["id"]
            last_msg_ts = _parse_sqlite_ts(row["timestamp"])

        if ingested > 0 or last_msg_id:
            await update_checkpoint(
                bridge["name"],
                bridge["source_number"],
                jid,
                str(last_msg_id),
                last_msg_ts,
                ingested,
            )
            log.info(
                "[fpe.hsync] bridge=%s jid=%s ingested=%d last_id=%s",
                bridge["name"], jid, ingested, last_msg_id,
            )

        total_ingested += ingested

    return total_ingested


async def _ingest_row(row: dict, bridge: dict, lid_map: dict) -> Optional[int]:
    """Convert a SQLite messages row to an IngestionRequest and ingest it."""
    content = row.get("content") or row.get("processed_text") or ""
    if not content.strip():
        return None  # skip media-only messages with no text

    # Resolve sender phone
    sender_raw = row.get("sender") or ""
    sender_phone = _resolve_phone(sender_raw, lid_map)

    ts = _parse_sqlite_ts(row["timestamp"])
    if ts is None:
        return None

    req = IngestionRequest(
        wa_message_id=str(row["id"]),
        source=bridge["name"],
        source_number=bridge["source_number"],
        chat_jid=str(row["chat_jid"]),
        sender_phone=sender_phone,
        is_from_me=bool(row.get("is_from_me", 0)),
        raw_content=content,
        media_type=row.get("media_type"),
        timestamp_wa=ts,
    )
    return await ingest_message(req)


# ── SQLite helpers (run in thread pool) ──────────────────────────────────

def _fetch_messages_sync(
    messages_db: str,
    whatsapp_db: str,
    chat_jid: str,
    since_ts: Optional[datetime],
    limit: int,
) -> tuple[list[dict], dict]:
    """
    Read messages + lid_map from SQLite synchronously.
    Returns (rows_as_dicts, lid_map).
    """
    lid_map = _load_lid_map_sync(whatsapp_db)
    rows: list[dict] = []

    try:
        con = sqlite3.connect(
            f"file:{messages_db}?mode=ro", uri=True, check_same_thread=False
        )
        con.row_factory = sqlite3.Row

        # `processed_text` is a legacy in-house column absent from the stock
        # bridge schema — degrade to NULL for stores that lack it (see
        # shared.sqlite_compat). Read-only.
        _pt = processed_text_column(con)

        if since_ts:
            ts_str = since_ts.isoformat()
            raw_rows = con.execute(
                f"""
                SELECT id, chat_jid, sender, content, timestamp,
                       is_from_me, media_type, {_pt}
                FROM messages
                WHERE chat_jid = ?
                  AND datetime(timestamp) > datetime(?)
                ORDER BY datetime(timestamp) ASC
                LIMIT ?
                """,
                (chat_jid, ts_str, limit),
            ).fetchall()
        else:
            # Full backfill from beginning
            raw_rows = con.execute(
                f"""
                SELECT id, chat_jid, sender, content, timestamp,
                       is_from_me, media_type, {_pt}
                FROM messages
                WHERE chat_jid = ?
                ORDER BY datetime(timestamp) ASC
                LIMIT ?
                """,
                (chat_jid, limit),
            ).fetchall()

        con.close()
        rows = [dict(r) for r in raw_rows]

    except sqlite3.OperationalError as exc:
        log.warning("[fpe.hsync] SQLite error db=%s: %s", messages_db, exc)

    return rows, lid_map


def _load_lid_map_sync(whatsapp_db: str) -> dict:
    """Load LID→phone mapping from whatsapp.db (same as bridge_poller)."""
    lid_map: dict[str, str] = {}
    try:
        con = sqlite3.connect(
            f"file:{whatsapp_db}?mode=ro", uri=True, check_same_thread=False
        )
        rows = con.execute("SELECT lid, pn FROM whatsmeow_lid_map").fetchall()
        con.close()
        for lid, pn in rows:
            lid_map[str(lid)] = str(pn)
    except Exception as exc:
        log.debug("[fpe.hsync] LID map load failed (%s): %s", whatsapp_db, exc)
    return lid_map


# ── Helpers ──────────────────────────────────────────────────────────────

def _resolve_phone(sender: str, lid_map: dict) -> Optional[str]:
    """Resolve sender string to 01XXXXXXXXX phone. Handles JID and LID formats."""
    from .normalizer import normalize_bd_phone, jid_to_phone
    if not sender:
        return None
    if "@s.whatsapp.net" in sender:
        return jid_to_phone(sender)
    if "@lid" in sender:
        pn = lid_map.get(sender.split("@")[0])
        return normalize_bd_phone(pn) if pn else None
    # bare number
    return normalize_bd_phone(sender)


def _parse_sqlite_ts(ts_str: Optional[str]) -> Optional[datetime]:
    """Parse SQLite timestamp string (various formats) to aware datetime."""
    if not ts_str:
        return None
    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
    ):
        try:
            dt = datetime.strptime(str(ts_str)[:25], fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    log.debug("[fpe.hsync] unparseable timestamp: %r", ts_str)
    return None


def _get_target_jids(settings) -> list[str]:
    """Read target JIDs from settings attribute or return default accountant JID."""
    raw = getattr(settings, "fpe_sync_chat_jids", None) or ""
    if raw:
        return [j.strip() for j in raw.split(",") if j.strip()]
    # Default: accountant chat on bridge2
    accountant = getattr(settings, "accountant_phone", "8801844836824")
    return [f"{accountant}@s.whatsapp.net"]