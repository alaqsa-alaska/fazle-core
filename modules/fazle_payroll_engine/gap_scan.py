"""
Fazle Payroll Engine — ID-based gap scanner.

The historical_sync loop advances by `last_timestamp`, which means any message
written to the bridge SQLite store *out of timestamp order* (e.g. resync from
WhatsApp servers, clock skew, replay) can be skipped forever.

This module performs an ID-based diff:
  set(SQLite messages.id WHERE chat_jid=X) − set(fpe_wa_messages.wa_message_id
                                                  WHERE source=B AND chat_jid=X)
and ingests anything missing via the same ingest_message() pipeline. Newly
ingested rows enter the standard FSM (pending → parsed → done), so the
accounting path is unchanged — only the "did this message reach archive?"
guarantee is hardened.

Idempotent: ingest_message() rejects duplicates on (wa_message_id, source).
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from datetime import datetime, timezone
from typing import Optional

from app.database import execute, fetch_all, fetch_val
from modules import observability as obs

from .historical_sync import (
    _BRIDGE_CONFIGS,
    _get_target_jids,
    _load_lid_map_sync,
    _parse_sqlite_ts,
    _resolve_phone,
)
from .ingestion import ingest_message
from .models import IngestionRequest

log = logging.getLogger("fazle.fpe.gapscan")

GAP_SCAN_INTERVAL = 300  # seconds — every 5 minutes
GAP_SCAN_LIMIT = 5000    # max IDs compared per chat per pass


async def run_gap_scan_once(chat_jids: Optional[list[str]] = None) -> list[dict]:
    """Run one ID-diff pass across all bridges + target JIDs. Returns per-run rows."""
    from app.config import get_settings
    settings = get_settings()
    target_jids: list[str] = chat_jids or _get_target_jids(settings)
    if not target_jids:
        return []

    runs: list[dict] = []
    for bridge in _BRIDGE_CONFIGS:
        for jid in target_jids:
            run = await _scan_bridge_chat(bridge, jid)
            runs.append(run)
    return runs


async def gap_scan_loop(chat_jids: Optional[list[str]] = None) -> None:
    log.info("[fpe.gapscan] gap-scan loop started (interval=%ds)", GAP_SCAN_INTERVAL)
    while True:
        try:
            runs = await run_gap_scan_once(chat_jids)
            missing = sum(r.get("missing_count", 0) for r in runs)
            backfilled = sum(r.get("backfilled", 0) for r in runs)
            skipped = sum(r.get("skipped_no_content", 0) for r in runs)
            real_gaps = missing - skipped
            if real_gaps > 0:
                log.warning(
                    "[fpe.gapscan] pass complete missing=%d skipped=%d real_gaps=%d backfilled=%d",
                    missing, skipped, real_gaps, backfilled,
                )
            elif missing > 0:
                log.debug(
                    "[fpe.gapscan] pass complete missing=%d skipped=%d all skipped (media-only)",
                    missing, skipped,
                )
            if backfilled > 0:
                log.info("[fpe.gapscan] pass complete backfilled=%d", backfilled)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            log.error("[fpe.gapscan] pass error: %s", exc, exc_info=True)
        await asyncio.sleep(GAP_SCAN_INTERVAL)


# ── Per-bridge / per-chat scan ───────────────────────────────────────────────

async def _scan_bridge_chat(bridge: dict, chat_jid: str) -> dict:
    started = time.perf_counter()
    err: Optional[str] = None
    sqlite_count = 0
    archive_count = 0
    missing_count = 0
    backfilled = 0
    skipped_no_content = 0
    try:
        loop = asyncio.get_event_loop()
        sqlite_rows, lid_map = await loop.run_in_executor(
            None,
            _fetch_sqlite_rows_for_diff,
            bridge["messages_db"],
            bridge["whatsapp_db"],
            chat_jid,
            GAP_SCAN_LIMIT,
        )
        sqlite_ids = {str(r["id"]) for r in sqlite_rows}
        sqlite_count = len(sqlite_ids)

        if not sqlite_ids:
            return await _record_run(
                bridge["name"], chat_jid, sqlite_count, archive_count,
                missing_count, backfilled, skipped_no_content, started, err,
            )

        # Archive uniqueness is (wa_message_id, source) — NOT scoped by chat_jid.
        # WhatsApp can deliver the same outbound msg to multiple chats (operator
        # DM + group @lid), so we ask "does this id exist anywhere for this
        # source?" otherwise the same msg appears as missing in every chat
        # except the first one we ingested.
        archive_rows = await fetch_all(
            """
            SELECT wa_message_id
            FROM fpe_wa_messages
            WHERE source = $1
              AND wa_message_id = ANY($2::text[])
            """,
            bridge["name"], list(sqlite_ids),
        )
        archive_id_set = {r["wa_message_id"] for r in archive_rows}
        archive_count = len(archive_id_set)

        missing_ids = sqlite_ids - archive_id_set
        missing_count = len(missing_ids)

        if missing_ids:
            obs.inc(
                "fpe_gap_scan_missing_total",
                missing_count,
                labels={"source": bridge["name"]},
            )
            for row in sqlite_rows:
                if str(row["id"]) not in missing_ids:
                    continue
                content = (row.get("content") or row.get("processed_text") or "").strip()
                if not content:
                    skipped_no_content += 1
                    continue
                fpe_id = await _ingest_sqlite_row(row, bridge, chat_jid, lid_map)
                if fpe_id is not None:
                    backfilled += 1

            actionable_missing = max(missing_count - skipped_no_content, 0)
            if actionable_missing > 0:
                log.warning(
                    "[fpe.gapscan] bridge=%s jid=%s missing=%d actionable=%d skipped_empty=%d (archive=%d sqlite=%d)",
                    bridge["name"], chat_jid, missing_count, actionable_missing,
                    skipped_no_content, archive_count, sqlite_count,
                )
            else:
                log.debug(
                    "[fpe.gapscan] bridge=%s jid=%s missing=%d all_empty_or_media skipped_empty=%d (archive=%d sqlite=%d)",
                    bridge["name"], chat_jid, missing_count, skipped_no_content,
                    archive_count, sqlite_count,
                )
            obs.inc(
                "fpe_gap_scan_backfilled_total",
                backfilled,
                labels={"source": bridge["name"]},
            )

    except Exception as exc:
        err = str(exc)[:500]
        log.error(
            "[fpe.gapscan] error bridge=%s jid=%s: %s",
            bridge["name"], chat_jid, exc, exc_info=True,
        )

    return await _record_run(
        bridge["name"], chat_jid, sqlite_count, archive_count,
        missing_count, backfilled, skipped_no_content, started, err,
    )


async def _record_run(
    source: str, chat_jid: str,
    sqlite_count: int, archive_count: int,
    missing_count: int, backfilled: int,
    skipped_no_content: int,
    started: float, err: Optional[str],
) -> dict:
    duration_ms = int((time.perf_counter() - started) * 1000)
    await execute(
        """
        INSERT INTO fpe_gap_scan_runs
            (source, chat_jid, sqlite_count, archive_count,
             missing_count, backfilled, skipped_no_content,
             duration_ms, error, finished_at)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,NOW())
        """,
        source, chat_jid, sqlite_count, archive_count,
        missing_count, backfilled, skipped_no_content, duration_ms, err,
    )
    obs.gauge(
        "fpe_gap_scan_last_missing",
        float(missing_count),
        labels={"source": source, "chat_jid": chat_jid},
    )
    return {
        "source": source,
        "chat_jid": chat_jid,
        "sqlite_count": sqlite_count,
        "archive_count": archive_count,
        "missing_count": missing_count,
        "backfilled": backfilled,
        "skipped_no_content": skipped_no_content,
        "duration_ms": duration_ms,
        "error": err,
    }


# ── SQLite helper (thread pool) ───────────────────────────────────────────────

def _fetch_sqlite_rows_for_diff(
    messages_db: str,
    whatsapp_db: str,
    chat_jid: str,
    limit: int,
) -> tuple[list[dict], dict]:
    lid_map = _load_lid_map_sync(whatsapp_db)
    rows: list[dict] = []
    try:
        con = sqlite3.connect(
            f"file:{messages_db}?mode=ro", uri=True, check_same_thread=False
        )
        con.row_factory = sqlite3.Row
        # Newest-first, bounded — we only diff a recent window per pass.
        raw = con.execute(
            """
            SELECT id, chat_jid, sender, content, timestamp,
                   is_from_me, media_type, processed_text
            FROM messages
            WHERE chat_jid = ?
            ORDER BY datetime(timestamp) DESC
            LIMIT ?
            """,
            (chat_jid, limit),
        ).fetchall()
        con.close()
        rows = [dict(r) for r in raw]
    except sqlite3.OperationalError as exc:
        log.warning("[fpe.gapscan] SQLite error db=%s: %s", messages_db, exc)
    return rows, lid_map


async def _ingest_sqlite_row(
    row: dict, bridge: dict, chat_jid: str, lid_map: dict,
) -> Optional[int]:
    content = row.get("content") or row.get("processed_text") or ""
    if not content.strip():
        return None
    sender_raw = row.get("sender") or ""
    sender_phone = _resolve_phone(sender_raw, lid_map)
    ts = _parse_sqlite_ts(row["timestamp"])
    if ts is None:
        return None
    req = IngestionRequest(
        wa_message_id=str(row["id"]),
        source=bridge["name"],
        source_number=bridge["source_number"],
        chat_jid=str(row.get("chat_jid") or chat_jid),
        sender_phone=sender_phone,
        is_from_me=bool(row.get("is_from_me", 0)),
        raw_content=content,
        media_type=row.get("media_type"),
        timestamp_wa=ts,
    )
    return await ingest_message(req)
