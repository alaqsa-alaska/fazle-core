"""History-sync replay guard + explicit pairing-state machine + F6 system-msg
suppression for the bridge poller.

Incident: docs/BRIDGE3_FIRST_REPAIR_INCIDENT_2026-09-02.md — a Bridge3 re-pair
triggered a WhatsApp history sync that wrote ~2,444 old messages into
messages.db with their original timestamps; 3 were treated as new inbound and
one automated recruitment reply was delivered from the freshly paired number.

Owner amendment 2026-09-02: replace the time-based quarantine with an EXPLICIT
state machine — a timestamp must never auto-authorise outbound.

Fix under test:
  F1 (bridge Go, tested there): `messages.from_history_sync` column.
  F2: `_fetch_new_messages` excludes `from_history_sync = 1` rows (schema-tolerant).
  F3: `_bridge_pairing_state` / `_bridge_outbound_held` — held for every set
      `<bridge>.pairing_state` except OUTBOUND_OWNER_APPROVED; no timestamp.
  F6: `shared.system_message.is_automated_system_message` — OTP / login-code /
      account-link notices are suppressed (persisted for forensics, no
      identity / session / candidate / reply).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from modules.bridge_poller import (
    _fetch_new_messages,
    _bridge_pairing_state,
    _bridge_outbound_held,
    PAIRING_HISTORY_SYNC_QUARANTINE,
    PAIRING_LIVE_INBOUND_VERIFIED,
    PAIRING_OUTBOUND_OWNER_APPROVED,
    PAIRING_UNPAIRED,
)
from shared.system_message import is_automated_system_message

_SINCE = datetime(2026, 8, 31, 10, 0, 0, tzinfo=timezone.utc)

_STOCK = """
CREATE TABLE messages (
    id TEXT PRIMARY KEY, chat_jid TEXT, sender TEXT, content TEXT,
    timestamp TIMESTAMP, is_from_me BOOLEAN, media_type TEXT, filename TEXT, url TEXT,
    media_key BLOB, file_sha256 BLOB, file_enc_sha256 BLOB, file_length INTEGER
);
CREATE TABLE chats (jid TEXT PRIMARY KEY, name TEXT);
"""
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


def _row(i, ts, *, hist=0, from_me=0, content=None):
    return {"id": i, "chat_jid": "8801711111111@s.whatsapp.net",
            "sender": "8801711111111@s.whatsapp.net",
            "content": content if content is not None else f"msg {i}",
            "timestamp": ts, "is_from_me": from_me, "media_type": None,
            "filename": None, "url": None, "from_history_sync": hist}


# ── F2: history-sync exclusion (owner test: mixed history + live) ────────────

def test_mixed_history_and_live_only_live_routes(tmp_path, wadb):
    rows = [
        _row("LIVE1", "2026-08-31 12:00:00", hist=0),
        _row("HIST1", "2026-08-31 13:00:00", hist=1),
        _row("HIST2", "2026-09-01 09:00:00", hist=1),
        _row("LIVE2", "2026-09-01 10:00:00", hist=0),
    ]
    db = _messages_db(tmp_path / "m.db", _WITH_FLAG, rows)
    msgs, max_ts = _fetch_new_messages(db, wadb, _SINCE)
    assert {m["id"] for m in msgs} == {"LIVE1", "LIVE2"}
    assert max_ts == datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)


def test_repeated_history_sync_still_excluded(tmp_path, wadb):
    """A second history sync re-inserts the same rows (flag 1) — still 0 routable."""
    rows = [_row(f"H{i}", f"2026-08-31 1{i}:00:00", hist=1) for i in range(1, 6)]
    db = _messages_db(tmp_path / "m.db", _WITH_FLAG, rows)
    for _ in range(3):  # simulate the poller running across repeated syncs
        msgs, max_ts = _fetch_new_messages(db, wadb, _SINCE)
        assert msgs == []
        assert max_ts == _SINCE


def test_restart_reconnect_replay_the_2444_row_shape(tmp_path, wadb):
    """The exact incident: a big history batch newer than a stale cursor."""
    rows = [_row(f"H{i}", f"2026-08-31 {10 + i // 6}:{i % 60:02d}:00", hist=1)
            for i in range(60)]
    rows.append(_row("REAL_NEW", "2026-09-02 08:00:00", hist=0))
    db = _messages_db(tmp_path / "m.db", _WITH_FLAG, rows)
    msgs, max_ts = _fetch_new_messages(db, wadb, _SINCE)
    assert {m["id"] for m in msgs} == {"REAL_NEW"}
    assert max_ts == datetime(2026, 9, 2, 8, 0, 0, tzinfo=timezone.utc)


def test_new_core_old_bridge_no_flag_column_unchanged(tmp_path, wadb):
    """New core, old bridge store (no column) → filter omitted, rows still route."""
    rows = [_row("A", "2026-08-31 12:00:00"), _row("B", "2026-09-01 09:00:00")]
    db = _messages_db(tmp_path / "m.db", _STOCK, rows)
    msgs, _ = _fetch_new_messages(db, wadb, _SINCE)
    assert {m["id"] for m in msgs} == {"A", "B"}


def test_new_bridge_old_core_column_is_just_ignored(tmp_path, wadb):
    """New bridge store has the column; an old core's SELECT lists explicit
    columns and never references from_history_sync — proven here by querying
    with the pre-fix SELECT shape against the new schema (no error, all rows)."""
    rows = [_row("A", "2026-08-31 12:00:00", hist=0),
            _row("B", "2026-09-01 09:00:00", hist=1)]
    db = _messages_db(tmp_path / "m.db", _WITH_FLAG, rows)
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    got = con.execute(
        "SELECT id, chat_jid, sender, content, timestamp, media_type, filename, url "
        "FROM messages WHERE is_from_me = 0 AND datetime(timestamp) > datetime('2026-08-31 10:00:00')"
    ).fetchall()
    con.close()
    assert {r[0] for r in got} == {"A", "B"}  # old core sees both; new core filters B


# ── F3: explicit pairing-state machine ──────────────────────────────────────

@pytest.fixture
def fake_settings(monkeypatch):
    store: dict[str, str] = {}

    async def fake_get(key, default=""):
        return store.get(key, default)

    monkeypatch.setattr("modules.wa_chat_frontend.get_runtime_setting", fake_get)
    return store


@pytest.mark.asyncio
async def test_unset_state_is_normal(fake_settings):
    assert await _bridge_pairing_state("bridge3") == ""
    held, state = await _bridge_outbound_held("bridge3")
    assert held is False and state == ""


@pytest.mark.asyncio
async def test_history_sync_quarantine_holds(fake_settings):
    fake_settings["bridge3.pairing_state"] = PAIRING_HISTORY_SYNC_QUARANTINE
    held, state = await _bridge_outbound_held("bridge3")
    assert held is True and state == PAIRING_HISTORY_SYNC_QUARANTINE


@pytest.mark.asyncio
async def test_live_inbound_verified_still_holds_outbound(fake_settings):
    """Owner requirement: LIVE_INBOUND_VERIFIED remains non-sending."""
    fake_settings["bridge3.pairing_state"] = PAIRING_LIVE_INBOUND_VERIFIED
    held, state = await _bridge_outbound_held("bridge3")
    assert held is True and state == PAIRING_LIVE_INBOUND_VERIFIED


@pytest.mark.asyncio
async def test_only_outbound_owner_approved_releases(fake_settings):
    fake_settings["bridge3.pairing_state"] = PAIRING_OUTBOUND_OWNER_APPROVED
    held, _ = await _bridge_outbound_held("bridge3")
    assert held is False


@pytest.mark.asyncio
async def test_unpaired_and_unknown_values_hold(fake_settings):
    for v in (PAIRING_UNPAIRED, "SOMETHING_ELSE", "outbound_owner_approved"):  # wrong-case too
        fake_settings["bridge3.pairing_state"] = v
        held, state = await _bridge_outbound_held("bridge3")
        assert held is True, v


@pytest.mark.asyncio
async def test_no_timestamp_anywhere_in_release_logic(fake_settings):
    """A timestamp must never auto-authorise sending: setting an ISO timestamp
    as the state is an unrecognised value → HELD, not released."""
    fake_settings["bridge3.pairing_state"] = "2020-01-01T00:00:00+00:00"
    held, _ = await _bridge_outbound_held("bridge3")
    assert held is True


@pytest.mark.asyncio
async def test_other_bridges_unaffected_by_bridge3_state(fake_settings):
    fake_settings["bridge3.pairing_state"] = PAIRING_HISTORY_SYNC_QUARANTINE
    for b in ("bridge1", "bridge2"):
        held, state = await _bridge_outbound_held(b)
        assert held is False and state == ""


@pytest.mark.asyncio
async def test_read_error_fails_open(monkeypatch):
    async def boom(key, default=""):
        raise RuntimeError("db down")
    monkeypatch.setattr("modules.wa_chat_frontend.get_runtime_setting", boom)
    held, state = await _bridge_outbound_held("bridge3")
    assert held is False and state == ""  # never freeze a healthy bridge on a read error


# ── F6: automated OTP / system-message suppression ──────────────────────────

@pytest.mark.parametrize("text", [
    "99015 is your Facebook confirmation code",
    "81335 is your Instagram code. Don't share it.",
    "Your WhatsApp account is successfully connected to your Instagram profile and ads",
    "G-728193 is your Google verification code",
    "Your verification code is 553201",
    "৯৯০১৫ is your Facebook confirmation code",   # Bengali digits
    "482913 is your one-time password",
    "We noticed a new login to your account",
])
def test_f6_flags_automated_system_messages(text):
    assert is_automated_system_message(text) is True


@pytest.mark.parametrize("text", [
    "আসসালামু আলাইকুম, আমি চাকরি করতে চাই",
    "আমার বয়স ২৫",
    "25",
    "1998",
    "Security Guard পদে আবেদন করতে চাই",
    "আমার নাম MD Mannan, ঠিকানা চট্টগ্রাম",
    "কোড কত টাকা বেতন?",           # contains 'কোড' but not a code pattern
    "আমি code করতে পারি (programming)",
    "",
    None,
])
def test_f6_does_not_flag_real_people(text):
    assert is_automated_system_message(text) is False


# ── structural: hold + F6 branches stop before routing ──────────────────────

def test_hold_and_f6_branches_continue_before_routing():
    """The PAIRING_HOLD and F6 branches must return control to the for-loop
    (via `continue`) before process_bridge_inbound / identity — proven by
    ordering (they sit between the @g.us guard and the media block) and by
    the branch bodies containing no routing call."""
    import inspect
    from modules import bridge_poller as bp

    src = inspect.getsource(bp)
    start = src.index('if _held:')
    end = src.index("# Unreadable media: a bare")
    block = src[start:end]
    assert "process_bridge_inbound" not in block
    assert "detect_identity" not in block
    assert "_save_draft" not in block
    assert "outbound.enqueue" not in block
    # both branches finalise + dedup + continue
    assert block.count("_mark_processed(") >= 2
    assert block.count("continue") >= 2
    # F6 is applied only for bridge3 (recruitment channel)
    assert 'bridge_name == "bridge3" and is_automated_system_message(text)' in block
