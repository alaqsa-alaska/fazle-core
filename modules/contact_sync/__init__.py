"""
Fazle Core — Unified Contact Sync

Merges contacts from all three WhatsApp sources into the central
wbom_contacts PostgreSQL table:

  Source 1: bridge1 — /home/azim/whatsapp1/store/whatsapp.db
  Source 2: bridge2 — /home/azim/bridges/bridge2/store/whatsapp.db
  Source 3: Meta    — populated from inbound webhook messages (no SQLite)

Design:
  - Canonical phone key: normalized 880XXXXXXXXXX (13-digit BD mobile)
  - One row per phone in wbom_contacts (unique on whatsapp_number + platform)
  - display_name = first non-empty: full_name → push_name → saved_name from contacts_all.db
  - Merging: if name from a newer source differs, update display_name only when
    the new name is more complete (longer, non-empty).
  - Duplicates: resolved at merge time via ON CONFLICT DO UPDATE
  - Sync runs:
      a) Full sync on startup (all contacts from both bridge SQLite DBs)
      b) Incremental on each bridge poll cycle (last 10-min window)
      c) On-demand via sync_all_contacts()

Phone normalization rules:
  - Strip leading + or 00
  - BD numbers: accept 01XXXXXXXXX → prepend 880 → 8801XXXXXXXXX
  - Already 880XXXXXXXXX → keep as-is
  - Non-BD or LID JIDs (@lid) → skip
"""

import asyncio
import logging
import sqlite3
import re
from datetime import datetime, timezone
from typing import Optional

from app.database import execute, fetch_val

log = logging.getLogger("fazle.contact_sync")

# Bridge SQLite sources
BRIDGE_SOURCES = [
    {
        "bridge": "bridge1",
        "whatsapp_db": "/home/azim/whatsapp1/store/whatsapp.db",
        "number": "8801958122300",
    },
    {
        "bridge": "bridge2",
        "whatsapp_db": "/home/azim/bridges/bridge2/store/whatsapp.db",
        "number": "8801880446111",
    },
]

# ── Phone normalization ────────────────────────────────────────────────────────

def normalize_phone(raw: str) -> Optional[str]:
    """Normalize a raw JID/phone to 8801XXXXXXXXXX (13-digit BD mobile). Returns None if invalid."""
    from modules.phone_normalizer import normalize_phone as _pn
    if not raw:
        return None
    # Strip JID suffix (@s.whatsapp.net, @lid, etc.) before normalizing
    jid_stripped = raw.split("@")[0].split(":")[0].strip()
    return _pn(jid_stripped)


def _best_name(*names: Optional[str]) -> str:
    """Return the longest non-empty name from the candidates."""
    candidates = [n.strip() for n in names if n and n.strip()]
    if not candidates:
        return ""
    return max(candidates, key=len)


# ── SQLite read (sync — run in executor) ─────────────────────────────────────

def _read_bridge_contacts(whatsapp_db: str, bridge_number: str) -> list[dict]:
    """Read all personal contacts from a bridge's whatsapp.db. Sync."""
    contacts = []
    try:
        con = sqlite3.connect(f"file:{whatsapp_db}?mode=ro", uri=True, check_same_thread=False, timeout=5.0)  # S-03 patch
        con.row_factory = sqlite3.Row
        rows = con.execute(
            """
            SELECT their_jid, first_name, full_name, push_name, business_name
            FROM whatsmeow_contacts
            WHERE their_jid LIKE '%@s.whatsapp.net'
            """
        ).fetchall()
        con.close()

        for row in rows:
            jid = row["their_jid"]
            phone = normalize_phone(jid)
            if not phone:
                continue
            # Skip self
            if phone == bridge_number:
                continue
            name = _best_name(row["full_name"], row["first_name"], row["push_name"], row["business_name"])
            contacts.append({
                "phone": phone,
                "display_name": name,
                "source_bridge": bridge_number,
            })
    except Exception as e:
        log.error(f"[contact_sync] SQLite read error ({whatsapp_db}): {e}")
    return contacts


# ── DB schema init ────────────────────────────────────────────────────────────

_SCHEMA_SQL = """
-- Track per-bridge sync metadata
CREATE TABLE IF NOT EXISTS fazle_contact_sync_log (
    bridge          TEXT        NOT NULL,
    synced_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    contacts_upserted INTEGER   NOT NULL DEFAULT 0,
    PRIMARY KEY (bridge)
);

-- Unified contact names table — one row per normalized phone
-- Used to normalize/deduplicate contact names across all bridges
CREATE TABLE IF NOT EXISTS fazle_unified_contacts (
    phone           TEXT        PRIMARY KEY,
    display_name    TEXT        NOT NULL DEFAULT '',
    source_bridge   TEXT        NOT NULL DEFAULT '',
    first_seen      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_updated    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS fazle_contact_aliases (
    phone           TEXT        NOT NULL,
    alias_name      TEXT        NOT NULL,
    source_bridge   TEXT        NOT NULL DEFAULT '',
    first_seen      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (phone, alias_name)
);

CREATE INDEX IF NOT EXISTS idx_fuc_name ON fazle_unified_contacts (display_name);
CREATE INDEX IF NOT EXISTS idx_fazle_contact_aliases_phone ON fazle_contact_aliases (phone, last_seen DESC);
"""


async def init_tables():
    for stmt in _SCHEMA_SQL.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            await execute(stmt)
    log.info("[contact_sync] tables ready")


# ── Upsert helpers ────────────────────────────────────────────────────────────

async def _upsert_contacts(contacts: list[dict]) -> int:
    """Upsert contacts into both wbom_contacts and fazle_unified_contacts. Returns count."""
    if not contacts:
        return 0

    count = 0
    for c in contacts:
        phone = c["phone"]
        name = c["display_name"]
        bridge = c["source_bridge"]

        # Upsert into wbom_contacts (master contact table)
        try:
            await execute(
                """
                INSERT INTO wbom_contacts
                    (whatsapp_number, display_name, platform, last_seen, updated_at)
                VALUES ($1, $2, 'whatsapp', NOW(), NOW())
                ON CONFLICT (whatsapp_number, platform) DO UPDATE
                    SET display_name = CASE
                            WHEN length(EXCLUDED.display_name) > length(wbom_contacts.display_name)
                            THEN EXCLUDED.display_name
                            ELSE wbom_contacts.display_name
                        END,
                        last_seen  = NOW(),
                        updated_at = NOW()
                """,
                phone, name,
            )
        except Exception as e:
            log.debug(f"[contact_sync] wbom_contacts upsert error {phone}: {e}")

        # Upsert into unified contacts (dedup/normalize layer)
        try:
            await execute(
                """
                INSERT INTO fazle_unified_contacts (phone, display_name, source_bridge, last_updated)
                VALUES ($1, $2, $3, NOW())
                ON CONFLICT (phone) DO UPDATE
                    SET display_name = CASE
                            WHEN length(EXCLUDED.display_name) > length(fazle_unified_contacts.display_name)
                            THEN EXCLUDED.display_name
                            ELSE fazle_unified_contacts.display_name
                        END,
                        source_bridge = EXCLUDED.source_bridge,
                        last_updated  = NOW()
                """,
                phone, name, bridge,
            )
            count += 1
        except Exception as e:
            log.debug(f"[contact_sync] unified_contacts upsert error {phone}: {e}")

        if name:
            await _upsert_alias(phone, name, bridge)

    return count


async def _upsert_alias(phone: str, alias_name: str, bridge: str) -> None:
    alias = (alias_name or "").strip()
    if not alias:
        return
    try:
        await execute(
            """
            INSERT INTO fazle_contact_aliases (phone, alias_name, source_bridge, last_seen)
            VALUES ($1, $2, $3, NOW())
            ON CONFLICT (phone, alias_name) DO UPDATE
                SET source_bridge = EXCLUDED.source_bridge,
                    last_seen = NOW()
            """,
            phone, alias, bridge,
        )
    except Exception as e:
        log.debug(f"[contact_sync] alias upsert error {phone}/{alias}: {e}")


async def _record_sync(bridge: str, count: int):
    try:
        await execute(
            """
            INSERT INTO fazle_contact_sync_log (bridge, synced_at, contacts_upserted)
            VALUES ($1, NOW(), $2)
            ON CONFLICT (bridge) DO UPDATE
                SET synced_at = NOW(), contacts_upserted = EXCLUDED.contacts_upserted
            """,
            bridge, count,
        )
    except Exception as e:
        log.debug(f"[contact_sync] sync log error: {e}")


# ── Public sync functions ─────────────────────────────────────────────────────

async def sync_bridge(bridge_cfg: dict) -> int:
    """Full sync for one bridge. Returns number of contacts upserted."""
    bridge = bridge_cfg["bridge"]
    whatsapp_db = bridge_cfg["whatsapp_db"]
    bridge_number = bridge_cfg["number"]

    loop = asyncio.get_event_loop()
    contacts = await loop.run_in_executor(
        None, _read_bridge_contacts, whatsapp_db, bridge_number
    )

    log.info(f"[contact_sync] {bridge}: {len(contacts)} contacts from SQLite")
    count = await _upsert_contacts(contacts)
    await _record_sync(bridge, count)
    log.info(f"[contact_sync] {bridge}: {count} upserted into DB")
    return count


async def sync_all_contacts() -> dict[str, int]:
    """Full sync from all bridge SQLite sources. Returns {bridge: count}."""
    results = {}
    for cfg in BRIDGE_SOURCES:
        try:
            results[cfg["bridge"]] = await sync_bridge(cfg)
        except Exception as e:
            log.error(f"[contact_sync] sync_bridge failed for {cfg['bridge']}: {e}")
            results[cfg["bridge"]] = 0
    return results


async def upsert_contact_from_message(phone: str, push_name: str, bridge_number: str):
    """
    Called on each inbound message to ensure the sender is in the contact DB.
    Lightweight — only inserts if not already present.
    """
    norm = normalize_phone(phone)
    if not norm:
        return
    name = (push_name or "").strip()
    try:
        # Insert-only — don't overwrite a better existing name
        await execute(
            """
            INSERT INTO fazle_unified_contacts (phone, display_name, source_bridge, last_updated)
            VALUES ($1, $2, $3, NOW())
            ON CONFLICT (phone) DO UPDATE
                SET display_name = CASE
                        WHEN length(EXCLUDED.display_name) > length(fazle_unified_contacts.display_name)
                        THEN EXCLUDED.display_name
                        ELSE fazle_unified_contacts.display_name
                    END,
                    last_updated = NOW()
            """,
            norm, name, bridge_number,
        )
        if name:
            await _upsert_alias(norm, name, bridge_number)
        await execute(
            """
            INSERT INTO wbom_contacts (whatsapp_number, display_name, platform, last_seen, updated_at)
            VALUES ($1, $2, 'whatsapp', NOW(), NOW())
            ON CONFLICT (whatsapp_number, platform) DO UPDATE
                SET display_name = CASE
                        WHEN length(EXCLUDED.display_name) > length(wbom_contacts.display_name)
                        THEN EXCLUDED.display_name
                        ELSE wbom_contacts.display_name
                    END,
                    last_seen  = NOW(),
                    updated_at = NOW()
            """,
            norm, name,
        )
    except Exception as e:
        log.debug(f"[contact_sync] upsert_from_message error {norm}: {e}")


async def get_display_name(phone: str) -> str:
    """Look up best known display name for a phone number."""
    norm = normalize_phone(phone)
    if not norm:
        return ""
    try:
        row = await fetch_val(
            "SELECT display_name FROM fazle_unified_contacts WHERE phone = $1",
            norm,
        )
        return row or ""
    except Exception:
        return ""


# ── Background periodic re-sync ───────────────────────────────────────────────

async def start_contact_sync_loop(interval_seconds: int = 3600):
    """Run full contact sync every interval_seconds (default 1 hour)."""
    log.info(f"[contact_sync] sync loop started (interval={interval_seconds}s)")
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            results = await sync_all_contacts()
            log.info(f"[contact_sync] periodic sync done: {results}")
        except asyncio.CancelledError:
            log.info("[contact_sync] sync loop stopped")
            break
        except Exception as e:
            log.error(f"[contact_sync] sync loop error: {e}")
