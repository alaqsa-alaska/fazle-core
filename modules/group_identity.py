"""Stage D1 (2026-08-28) -- group identity, synced FROM the WhatsApp
bridges' own authoritative metadata, never hand-maintained.

Discovery finding: each bridge (bridge1/2/3, whatsmeow-based) already
keeps its own `chats` table inside its SQLite store (the same messages.db
file modules.bridge_poller already reads), populated live by whatsmeow as
it syncs group subjects from WhatsApp -- columns (jid, name,
last_message_time). That IS the authoritative source Stage D1 asked to be
found first; this module only mirrors it into
wbom_group_identities (migration 075), it never invents or hand-types a
JID.

Deliberately NOT wired into the always-running poll loop in this stage --
Stage D's own top-level constraint is "do NOT build autonomous
action/execution yet", and continuously scheduling a new background sync
against a live, sensitive service is a separate decision from providing
the mechanism. sync_group_identities()/sync_all_bridges() are ordinary
callables usable on demand (a script, an admin endpoint, or a future
periodic hook once explicitly asked for).
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Optional

from app.config import get_settings
from app.database import fetch_one
from modules.bridge_poller import _parse_source_timestamp

log = logging.getLogger(__name__)

_BRIDGE_DB_PATH_ATTR = {
    "bridge1": "bridge1_db_path",
    "bridge2": "bridge2_db_path",
    "bridge3": "bridge3_db_path",
}


def _bridge_db_path(bridge_name: str) -> Optional[str]:
    settings = get_settings()
    attr = _BRIDGE_DB_PATH_ATTR.get(bridge_name)
    return getattr(settings, attr, None) if attr else None


def _read_bridge_group_chats(db_path: str) -> list[dict]:
    """Read-only: SELECT from the bridge's own chats table. Never writes
    to the bridge's SQLite store -- same safety convention every other
    bridge_poller SQLite read in this codebase already follows."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, check_same_thread=False, timeout=5.0)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT jid, name, last_message_time FROM chats WHERE jid LIKE '%@g.us'"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


async def sync_group_identities(bridge_name: str) -> int:
    """Upsert one bridge's authoritative group-chat cache into
    wbom_group_identities. Returns the number of groups synced from this
    bridge. Read-only against the bridge's own store; only writes to our
    own Postgres table. Never sets business_label -- that column is left
    untouched on conflict, exactly per Stage D1's "never auto-derived"
    rule."""
    db_path = _bridge_db_path(bridge_name)
    if not db_path:
        log.warning("[group_identity] no db path configured for %s", bridge_name)
        return 0
    try:
        import asyncio
        loop = asyncio.get_event_loop()
        rows = await loop.run_in_executor(None, _read_bridge_group_chats, db_path)
    except Exception as exc:
        log.warning("[group_identity] failed to read %s chats: %s", bridge_name, exc)
        return 0

    synced = 0
    for row in rows:
        jid = (row.get("jid") or "").strip()
        if not jid:
            continue
        name = row.get("name")
        # asyncpg requires a real datetime.datetime for a timestamptz
        # bound param even with an explicit ::timestamptz SQL cast (same
        # class of bug fixed live in app.main's Stage C endpoints) --
        # reuse the same parsing convention already established there.
        last_seen = _parse_source_timestamp(row.get("last_message_time"))
        try:
            await fetch_one(
                """
                INSERT INTO wbom_group_identities
                    (group_jid, source_bridge, display_name, last_seen_at, metadata_updated_at)
                VALUES ($1, $2, $3, $4::timestamptz, NOW())
                ON CONFLICT (group_jid, source_bridge) DO UPDATE SET
                    display_name = EXCLUDED.display_name,
                    last_seen_at = COALESCE(EXCLUDED.last_seen_at, wbom_group_identities.last_seen_at),
                    metadata_updated_at = NOW()
                RETURNING group_jid
                """,
                jid, bridge_name, name or None, last_seen or None,
            )
            synced += 1
        except Exception as exc:
            log.warning("[group_identity] upsert failed jid=%s bridge=%s: %s", jid, bridge_name, exc)
    return synced


async def sync_all_bridges() -> dict:
    """Convenience wrapper: sync every configured bridge, return a
    per-bridge count. Bridge3 is expected to legitimately return 0 (it is
    not a member of any WhatsApp group at the time of writing) -- that is
    a real, observed fact, not an error."""
    result = {}
    for bridge_name in ("bridge1", "bridge2", "bridge3"):
        result[bridge_name] = await sync_group_identities(bridge_name)
    return result


# ── Business identity assignment (Stage E1, explicit/administrative) ────
# display_name (provider-observed, can differ per source_bridge and can
# change any time WhatsApp's group subject changes) vs business_label
# (OUR meaning for the group, e.g. 'office_internal') are deliberately
# separate concepts -- see migration 075's own column comments. This
# function is the ONLY writer of business_label; sync_group_identities()
# above never touches it. Never called with a value derived from
# display_name text -- every call site must pass an explicit,
# Owner-directed label.
#
# business_label conceptually belongs to the group (the JID), not to any
# one bridge's observation of it -- applied to every existing
# (group_jid, source_bridge) row for this JID so it can never disagree
# with itself across bridges, and every future sync_group_identities()
# upsert for a NEW source_bridge observing the same JID needs its own
# explicit set_group_business_label() call (sync itself never invents
# one) -- see is_approved_group_jid()'s own docstring for why that
# matters for E3's allowlist gate.
async def set_group_business_label(group_jid: str, business_label: Optional[str]) -> int:
    """Explicitly assign (or clear, if business_label=None) the business
    label for every known row of this group_jid. Returns the number of
    rows updated. Does not create a new row -- the group must already
    have been discovered via sync_group_identities() first (identity
    still comes only from authoritative bridge metadata, never a
    hand-typed JID here)."""
    from app.database import execute
    result = await execute(
        "UPDATE wbom_group_identities SET business_label = $1, metadata_updated_at = NOW() WHERE group_jid = $2",
        business_label, group_jid,
    )
    try:
        return int(str(result).split()[-1])
    except (ValueError, IndexError):
        return 0


async def is_approved_group_jid(group_jid: str) -> bool:
    """Stage E3's allowlist gate: a group is eligible for canonical
    persistence/classification if and only if it has an explicit,
    non-NULL business_label -- never a separate hardcoded JID list. This
    is the general mechanism: approving a future group is one
    set_group_business_label() call, not a code change."""
    from app.database import fetch_one as _fetch_one
    row = await _fetch_one(
        "SELECT 1 FROM wbom_group_identities WHERE group_jid = $1 AND business_label IS NOT NULL LIMIT 1",
        group_jid,
    )
    return row is not None


async def get_business_label(group_jid: str) -> Optional[str]:
    from app.database import fetch_one as _fetch_one
    row = await _fetch_one(
        "SELECT business_label FROM wbom_group_identities WHERE group_jid = $1 AND business_label IS NOT NULL LIMIT 1",
        group_jid,
    )
    return row["business_label"] if row else None


# ── E3: the approved-group allowlist, cached ─────────────────────────────
# A JID is "approved for canonical persistence" if and only if it has a
# non-NULL business_label -- the same general mechanism is_approved_
# group_jid() above already uses, just as a list instead of a single
# lookup (the poll loop needs the full set once per cycle, not one
# lookup per candidate JID). Cached with a short TTL so this doesn't add
# a Postgres round-trip to every poll iteration (bridge1/2 can poll as
# often as every ~1s under BRIDGE_POLL_MIN_S adaptive backoff) -- a newly
# -approved group takes effect within the TTL, not instantly, which is
# fine for an explicit administrative action.
import time as _time

_approved_jids_cache: dict = {"jids": [], "checked_at": 0.0}
_APPROVED_JIDS_TTL_S = 60.0


async def list_approved_group_jids(force_refresh: bool = False) -> list[str]:
    now = _time.monotonic()
    if not force_refresh and (now - _approved_jids_cache["checked_at"]) < _APPROVED_JIDS_TTL_S:
        return _approved_jids_cache["jids"]
    from app.database import fetch_all as _fetch_all
    try:
        rows = await _fetch_all(
            "SELECT DISTINCT group_jid FROM wbom_group_identities WHERE business_label IS NOT NULL"
        )
        jids = [r["group_jid"] for r in rows]
    except Exception as exc:
        log.warning("[group_identity] list_approved_group_jids failed, keeping stale cache: %s", exc)
        return _approved_jids_cache["jids"]
    _approved_jids_cache["jids"] = jids
    _approved_jids_cache["checked_at"] = now
    return jids
