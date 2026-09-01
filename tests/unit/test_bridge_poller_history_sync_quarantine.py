"""History-sync replay guard + pairing quarantine for the bridge poller.

Incident: docs/BRIDGE3_FIRST_REPAIR_INCIDENT_2026-09-02.md — a Bridge3 re-pair
triggered a WhatsApp history sync that wrote ~2,444 old messages into
messages.db with their original timestamps. The poller's timestamp cursor was
stale, so 3 of those synced rows were treated as new inbound and one was
auto-answered from the freshly paired number.

Fix under test:
  F1 (bridge Go, not here): `messages.from_history_sync` column, 1 for
     history-sync backfill rows, 0 for live deliveries.
  F2: `_fetch_new_messages` excludes `from_history_sync = 1` rows from routing
     (schema-tolerant: no column → no filter, prior behaviour).
  F3: `_bridge_quarantine_until` + the poller's PAIRING_QUARANTINE branch —
     while a bridge is inside its post-pairing window every inbound is
     persisted but never routed.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from modules.bridge_poller import _fetch_new_messages, _bridge_quarantine_until

_SINCE = datetime(2026, 8, 31, 10, 0, 0, tzinfo=timezone.utc)

_STOCK = """
CREATE TABLE messages (
    id TEXT PRIMARY KEY, chat_jid TEXT, sender TEXT, content TEXT,
    timestamp TIMESTAMP, is_from_me BOOLEAN, media_type TEXT, filename TEXT, url TEXT,
    media_key BLOB, file_sha256 BLOB, file_enc_sha256 BLOB, file_length INTEGER
);
CREATE TABLE chats (jid TEXT PRIMARY KEY, name TEXT);
"""
# post-2026-09-02 bridge binary
_WITH_FLAG = _STOCK.replace(
    "file_length INTEGER\n);",
    "file_length INTEGER,\n    direct_path TEXT,\n    from_history_sync INTEGER NOT NULL DEFAULT 0\n);",
)


def _whatsapp_db(path) -> str:
    con = sqlite3.connect(path)
    con.executescript("CREATE TABLE whatsmeow_lid_map (lid TEXT, pn TEXT);")
    con.commit()
    con.close()
    return str(path)


def _messages_db(path, schema: str, rows: list[dict]) -> str:
    con = sqlite3.connect(path)
    con.executescript(schema)
    has_flag = "from_history_sync" in schema
    base = ["id", "chat_jid", "sender", "content", "timestamp", "is_from_me",
            "media_type", "filename", "url"]
    for r in rows:
        cols = list(base)
        vals = [r.get(c) for c in base]
        if has_flag:
            cols.append("from_history_sync")
            vals.append(r.get("from_history_sync", 0))
        con.execute(
            f"INSERT INTO messages ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
            tuple(vals),
        )
    con.commit()
    con.close()
    return str(path)


@pytest.fixture
def wadb(tmp_path):
    return _whatsapp_db(tmp_path / "whatsapp.db")


def _live(i, ts):
    return {"id": i, "chat_jid": "8801711111111@s.whatsapp.net",
            "sender": "8801711111111@s.whatsapp.net", "content": f"msg {i}",
            "timestamp": ts, "is_from_me": 0, "media_type": None,
            "filename": None, "url": None}


# ── F2: history-sync rows excluded from routing ──────────────────────────────

def test_history_sync_rows_never_returned_for_routing(tmp_path, wadb):
    rows = [
        {**_live("LIVE1", "2026-08-31 12:00:00"), "from_history_sync": 0},
        {**_live("HIST1", "2026-08-31 13:00:00"), "from_history_sync": 1},
        {**_live("HIST2", "2026-09-01 09:00:00"), "from_history_sync": 1},
        {**_live("LIVE2", "2026-09-01 10:00:00"), "from_history_sync": 0},
    ]
    db = _messages_db(tmp_path / "m.db", _WITH_FLAG, rows)
    msgs, max_ts = _fetch_new_messages(db, wadb, _SINCE)
    ids = {m["id"] for m in msgs}
    assert ids == {"LIVE1", "LIVE2"}, ids
    # cursor advances only over non-history rows
    assert max_ts == datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)


def test_all_history_batch_yields_nothing_and_holds_cursor(tmp_path, wadb):
    """The exact incident shape: a big history-sync batch above a stale cursor."""
    rows = [
        {**_live(f"H{i}", f"2026-08-31 1{i}:00:00"), "from_history_sync": 1}
        for i in range(1, 6)
    ]
    db = _messages_db(tmp_path / "m.db", _WITH_FLAG, rows)
    msgs, max_ts = _fetch_new_messages(db, wadb, _SINCE)
    assert msgs == []
    assert max_ts == _SINCE  # cursor does not move — nothing routable seen


def test_schema_without_flag_is_unchanged_behaviour(tmp_path, wadb):
    """Older bridge binary (no column) → filter omitted, rows still routed."""
    rows = [_live("A", "2026-08-31 12:00:00"), _live("B", "2026-09-01 09:00:00")]
    db = _messages_db(tmp_path / "m.db", _STOCK, rows)
    msgs, _ = _fetch_new_messages(db, wadb, _SINCE)
    assert {m["id"] for m in msgs} == {"A", "B"}


def test_is_from_me_history_row_also_excluded(tmp_path, wadb):
    rows = [
        {**_live("H1", "2026-09-01 09:00:00"), "from_history_sync": 1, "is_from_me": 1},
        {**_live("L1", "2026-09-01 10:00:00"), "from_history_sync": 0},
    ]
    db = _messages_db(tmp_path / "m.db", _WITH_FLAG, rows)
    msgs, _ = _fetch_new_messages(db, wadb, _SINCE)
    assert {m["id"] for m in msgs} == {"L1"}


# ── F3: quarantine window helper ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_quarantine_helper_active_and_expired(monkeypatch):
    store: dict[str, str] = {}

    async def fake_get(key, default=""):
        return store.get(key, default)

    monkeypatch.setattr("modules.wa_chat_frontend.get_runtime_setting", fake_get)

    # not set → not quarantined
    assert await _bridge_quarantine_until("bridge3") is None

    # future → quarantined, returns the datetime
    future = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
    store["bridge3.quarantine_until"] = future
    got = await _bridge_quarantine_until("bridge3")
    assert got is not None and got.tzinfo is not None

    # past → expired, not quarantined
    store["bridge3.quarantine_until"] = (
        datetime.now(timezone.utc) - timedelta(minutes=1)
    ).isoformat()
    assert await _bridge_quarantine_until("bridge3") is None

    # 'Z' suffix accepted
    store["bridge3.quarantine_until"] = (
        datetime.now(timezone.utc) + timedelta(hours=1)
    ).strftime("%Y-%m-%dT%H:%M:%S") + "Z"
    assert await _bridge_quarantine_until("bridge3") is not None

    # garbage → fail-open (not quarantined), never raises
    store["bridge3.quarantine_until"] = "not-a-date"
    assert await _bridge_quarantine_until("bridge3") is None

    # other bridges unaffected
    assert await _bridge_quarantine_until("bridge1") is None
