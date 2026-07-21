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
import os
import sqlite3
import re
from datetime import datetime, timezone
from typing import Optional

from app.config import get_settings
from app.database import execute, fetch_all, fetch_one, fetch_val

log = logging.getLogger("fazle.contact_sync")
_settings = get_settings()

# Bridge SQLite sources.
# 2026-07-22: was hardcoded to "/home/azim/bridges/bridge2/store/whatsapp.db",
# which does not exist -- the real, actively-updated file is wherever
# app/config.py's bridge{1,2}_whatsapp_db_path settings point (confirmed via
# `ls`: "/home/azim/whatsapp2/store/whatsapp.db"). bridge2 contact sync has
# been silently reading zero contacts (caught by _read_bridge_contacts'
# broad except, only logged). Now reads from the same settings-driven paths
# bridge_poller itself uses, so there is exactly one source of truth for
# "where is this bridge's SQLite store."
BRIDGE_SOURCES = [
    {
        "bridge": "bridge1",
        "whatsapp_db": _settings.bridge1_whatsapp_db_path,
        "number": _settings.admin_bridge1_number,
    },
    {
        "bridge": "bridge2",
        "whatsapp_db": _settings.bridge2_whatsapp_db_path,
        "number": _settings.admin_bridge2_number,
    },
]

# 2026-07-22: hard-delete threshold + daily sync hour, tunable without a
# settings-class change (matches the existing os.getenv() convention already
# used for other scheduler tunables, e.g. PAYROLL_AUTO_COMPUTE_HOUR).
HARD_DELETE_AFTER_ABSENCES = int(os.getenv("CONTACT_SYNC_HARD_DELETE_AFTER", "3"))
DAILY_SYNC_HOUR = int(os.getenv("CONTACT_SYNC_HOUR", "4"))

# ── Phone normalization ────────────────────────────────────────────────────────

def normalize_phone(raw: str) -> Optional[str]:
    """Normalize a raw JID/phone to 8801XXXXXXXXXX (13-digit BD mobile). Returns None if invalid."""
    from modules.phone_normalizer import normalize_phone as _pn
    if not raw:
        return None
    # Strip JID suffix (@s.whatsapp.net, @lid, etc.) before normalizing
    jid_stripped = raw.split("@")[0].split(":")[0].strip()
    return _pn(jid_stripped)


def _best_name(full_name: Optional[str], first_name: Optional[str],
                push_name: Optional[str], business_name: Optional[str]) -> str:
    """Canonical contact-name authority (2026-07-22 Owner decision): the name
    saved in the admin's own phone contact book is authoritative for routing
    and display -- WhatsApp profile name / WhatsApp ID display name is not.

    whatsmeow_contacts semantics (verified against live data): full_name /
    first_name are populated from the phone's own synced address book (what
    *we* called this person); push_name is what the other person set as
    their own WhatsApp profile name; business_name is a WhatsApp Business
    API field. Confirmed empirically these genuinely differ (e.g. our own
    saved "Escort Saimun" vs their self-set push_name "Saimun").

    Priority: full_name > first_name > push_name > business_name -- the
    WhatsApp-side fields are only a fallback for a phone number the phone
    hasn't saved a name for at all, never an override of one that exists.
    """
    for candidate in (full_name, first_name, push_name, business_name):
        c = (candidate or "").strip()
        if c:
            return c
    return ""


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

-- 2026-07-22: per-(phone, bridge) absence tracking for the daily
-- soft-delete-then-hard-delete-after-N-consecutive-misses policy. Deliberately
-- a separate small table, not a wbom_contacts column: absence is scoped per
-- bridge (a contact present in bridge1's phone book but not bridge2's is
-- normal -- two different phones -- not evidence of deletion), and a single
-- phone-keyed column on wbom_contacts couldn't represent that without
-- conflating the two bridges' independent contact-book state.
CREATE TABLE IF NOT EXISTS fazle_contact_presence (
    phone               TEXT        NOT NULL,
    bridge              TEXT        NOT NULL,
    consecutive_absences INTEGER    NOT NULL DEFAULT 0,
    last_present_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (phone, bridge)
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

-- 2026-07-22: audit trail written BEFORE every hard-delete (irreversible by
-- definition) -- last known display_name/relation preserved so a mistaken
-- deletion is at least fully reconstructable, never a silent loss.
CREATE TABLE IF NOT EXISTS fazle_contact_hard_delete_log (
    id              BIGSERIAL   PRIMARY KEY,
    phone           TEXT        NOT NULL,
    bridge          TEXT        NOT NULL,
    last_display_name TEXT,
    last_relation   TEXT,
    consecutive_absences INTEGER,
    deleted_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
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


# ── Presence tracking / soft-delete then hard-delete-after-N-misses ───────────
# Owner policy (2026-07-22): a phone missing from one sync could mean the
# phone was offline, the bridge export was incomplete, or the contact was
# genuinely deleted -- indistinguishable from a single sync alone. So:
#   1st consecutive miss  -> soft-delete only (wbom_contacts.is_active=false,
#                            marked_for_delete_at=NOW()) -- fully reversible,
#                            the contact reappears automatically if seen again.
#   Nth consecutive miss  -> hard-delete (row removed), N = HARD_DELETE_AFTER_ABSENCES.
# is_protected=true contacts are never touched by either step.

async def _mark_present(phone: str, bridge: str) -> None:
    """Contact seen in this sync: reset its absence streak and, if it had
    been soft-deleted by a prior sync, reactivate it."""
    await execute(
        """
        INSERT INTO fazle_contact_presence (phone, bridge, consecutive_absences, last_present_at)
        VALUES ($1, $2, 0, NOW())
        ON CONFLICT (phone, bridge) DO UPDATE
            SET consecutive_absences = 0, last_present_at = NOW()
        """,
        phone, bridge,
    )
    await execute(
        """
        UPDATE wbom_contacts
           SET is_active = true, marked_for_delete_at = NULL, updated_at = NOW()
         WHERE whatsapp_number = $1 AND platform = 'whatsapp'
           AND (is_active = false OR marked_for_delete_at IS NOT NULL)
        """,
        phone,
    )


async def _handle_missing_contacts(bridge: str, current_phones: set[str]) -> dict:
    """Diff this sync's phone set against what was previously tracked present
    for this bridge; soft-delete on first miss, hard-delete at the threshold.
    Returns {"soft_deleted": n, "hard_deleted": n}."""
    result = {"soft_deleted": 0, "hard_deleted": 0}
    try:
        tracked = await fetch_all(
            "SELECT phone, consecutive_absences FROM fazle_contact_presence WHERE bridge = $1",
            bridge,
        )
    except Exception as e:
        log.warning(f"[contact_sync] presence read failed bridge={bridge}: {e}")
        return result

    for row in tracked:
        phone = row["phone"]
        if phone in current_phones:
            continue  # still present, nothing to do (already reset by _mark_present)

        try:
            protected = await fetch_val(
                "SELECT is_protected FROM wbom_contacts WHERE whatsapp_number = $1 AND platform = 'whatsapp'",
                phone,
            )
        except Exception:
            protected = False
        if protected:
            continue  # never soft- or hard-delete a protected contact

        new_count = int(row["consecutive_absences"] or 0) + 1
        await execute(
            """
            UPDATE fazle_contact_presence SET consecutive_absences = $2
             WHERE phone = $1 AND bridge = $3
            """,
            phone, new_count, bridge,
        )

        if new_count >= HARD_DELETE_AFTER_ABSENCES:
            await _hard_delete_contact(phone, bridge, new_count)
            result["hard_deleted"] += 1
        elif new_count == 1:
            # First miss this bridge has seen -> soft-delete only.
            await execute(
                """
                UPDATE wbom_contacts
                   SET is_active = false,
                       marked_for_delete_at = COALESCE(marked_for_delete_at, NOW()),
                       updated_at = NOW()
                 WHERE whatsapp_number = $1 AND platform = 'whatsapp' AND is_active = true
                """,
                phone,
            )
            result["soft_deleted"] += 1
            log.info(f"[contact_sync] soft-delete phone={phone} bridge={bridge} (miss 1/{HARD_DELETE_AFTER_ABSENCES})")

    return result


async def _hard_delete_contact(phone: str, bridge: str, consecutive_absences: int) -> None:
    """Irreversible -- audit-logged first, always."""
    try:
        row = await fetch_one(
            "SELECT display_name, relation FROM wbom_contacts WHERE whatsapp_number = $1 AND platform = 'whatsapp'",
            phone,
        )
        await execute(
            """
            INSERT INTO fazle_contact_hard_delete_log
                (phone, bridge, last_display_name, last_relation, consecutive_absences)
            VALUES ($1, $2, $3, $4, $5)
            """,
            phone, bridge,
            row["display_name"] if row else None,
            row["relation"] if row else None,
            consecutive_absences,
        )
        await execute("DELETE FROM wbom_contacts WHERE whatsapp_number = $1 AND platform = 'whatsapp'", phone)
        await execute("DELETE FROM fazle_contact_presence WHERE phone = $1 AND bridge = $2", phone, bridge)
        log.warning(
            f"[contact_sync] HARD-DELETE phone={phone} bridge={bridge} "
            f"after {consecutive_absences} consecutive missed syncs (audit-logged first)"
        )
    except Exception as e:
        log.error(f"[contact_sync] hard-delete failed phone={phone} bridge={bridge}: {e}")


# ── Public sync functions ─────────────────────────────────────────────────────

async def sync_bridge(bridge_cfg: dict) -> dict:
    """Full sync for one bridge: upsert everything currently present, then
    detect and act on contacts that disappeared since the last sync. Returns
    {"upserted": n, "soft_deleted": n, "hard_deleted": n}."""
    bridge = bridge_cfg["bridge"]
    whatsapp_db = bridge_cfg["whatsapp_db"]
    bridge_number = bridge_cfg["number"]

    loop = asyncio.get_event_loop()
    contacts = await loop.run_in_executor(
        None, _read_bridge_contacts, whatsapp_db, bridge_number
    )

    log.info(f"[contact_sync] {bridge}: {len(contacts)} contacts from SQLite")
    count = await _upsert_contacts(contacts)
    current_phones = {c["phone"] for c in contacts}
    for phone in current_phones:
        await _mark_present(phone, bridge)
    absence_result = await _handle_missing_contacts(bridge, current_phones)
    await _record_sync(bridge, count)
    log.info(
        f"[contact_sync] {bridge}: {count} upserted, "
        f"{absence_result['soft_deleted']} soft-deleted, "
        f"{absence_result['hard_deleted']} hard-deleted"
    )
    return {"upserted": count, **absence_result}


async def sync_all_contacts() -> dict[str, dict]:
    """Full sync from all bridge SQLite sources. Returns {bridge: {upserted, soft_deleted, hard_deleted}}."""
    results = {}
    for cfg in BRIDGE_SOURCES:
        try:
            results[cfg["bridge"]] = await sync_bridge(cfg)
        except Exception as e:
            log.error(f"[contact_sync] sync_bridge failed for {cfg['bridge']}: {e}")
            results[cfg["bridge"]] = {"upserted": 0, "soft_deleted": 0, "hard_deleted": 0, "error": str(e)}
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


# ── Scheduled job (2026-07-22) ────────────────────────────────────────────────
# Registered in modules.scheduler as "daily_contact_sync" — see start_scheduler().
# This is the actual "at least once a day" mechanism the Owner asked for.

async def job_daily_contact_sync() -> dict:
    """Daily phone-contact-book <-> wbom_contacts sync, called by the
    scheduler (CONTACT_SYNC_HOUR, default 04:00 Asia/Dhaka)."""
    results = await sync_all_contacts()
    log.info(f"[contact_sync] daily job done: {results}")
    return {"status": "ok", "results": results}


# ── Background periodic re-sync (superseded 2026-07-22 by the scheduler job
# above — never actually wired up before that; kept only as a documented
# reference, not the live mechanism) ──────────────────────────────────────────

async def start_contact_sync_loop(interval_seconds: int = 3600):
    """Run full contact sync every interval_seconds (default 1 hour).

    Not called anywhere — job_daily_contact_sync() via modules.scheduler is
    the live daily sync mechanism. Left in place undeleted per repo
    convention (flag superseded code, don't silently remove it)."""
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
