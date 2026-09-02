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


# ── F3: explicit pairing-state machine — BRIDGE3 FAILS CLOSED ───────────────
#
# Owner directive 2026-09-02: for bridge3, ANY ambiguity holds outbound. Only
# the exact literal "OUTBOUND_OWNER_APPROVED" releases. bridge1/bridge2 keep
# their existing behaviour (unset = normal).

@pytest.fixture
def fake_settings(monkeypatch):
    store: dict[str, str] = {}

    async def fake_get(key, default=""):
        return store.get(key, default)

    monkeypatch.setattr("modules.wa_chat_frontend.get_runtime_setting", fake_get)
    return store


# --- the ONLY release ---

@pytest.mark.asyncio
async def test_bridge3_only_exact_outbound_owner_approved_releases(fake_settings):
    fake_settings["bridge3.pairing_state"] = PAIRING_OUTBOUND_OWNER_APPROVED
    held, _ = await _bridge_outbound_held("bridge3")
    assert held is False


# --- every ambiguous / non-approved value HOLDS bridge3 ---

@pytest.mark.asyncio
async def test_bridge3_key_absent_holds(fake_settings):
    # store is empty → get_runtime_setting returns "" → HELD
    assert "bridge3.pairing_state" not in fake_settings
    held, state = await _bridge_outbound_held("bridge3")
    assert held is True


@pytest.mark.asyncio
async def test_bridge3_key_empty_holds(fake_settings):
    fake_settings["bridge3.pairing_state"] = ""
    held, _ = await _bridge_outbound_held("bridge3")
    assert held is True


@pytest.mark.asyncio
async def test_bridge3_whitespace_only_holds(fake_settings):
    fake_settings["bridge3.pairing_state"] = "   "
    held, _ = await _bridge_outbound_held("bridge3")
    assert held is True


@pytest.mark.asyncio
async def test_bridge3_unknown_value_holds(fake_settings):
    for v in ("SOMETHING_ELSE", "approved", "outbound_owner_approved",
              "OUTBOUND-OWNER-APPROVED", "OUTBOUND_OWNER_APPROVEDX", "OK", "true", "1"):
        fake_settings["bridge3.pairing_state"] = v
        held, _ = await _bridge_outbound_held("bridge3")
        assert held is True, repr(v)


@pytest.mark.asyncio
async def test_bridge3_exact_keyword_only_case_and_content_sensitive(fake_settings):
    # surrounding whitespace is trimmed (the API trims on write too) — that is
    # still the *exact* keyword and releases:
    fake_settings["bridge3.pairing_state"] = "  OUTBOUND_OWNER_APPROVED  "
    assert (await _bridge_outbound_held("bridge3"))[0] is False
    # any other case / content does NOT:
    fake_settings["bridge3.pairing_state"] = "outbound_owner_approved"
    assert (await _bridge_outbound_held("bridge3"))[0] is True


@pytest.mark.asyncio
async def test_bridge3_iso_timestamp_value_holds(fake_settings):
    """A legacy/blind timestamp must NEVER authorise sending."""
    for v in ("2020-01-01T00:00:00+00:00", "2099-12-31T23:59:59Z",
              str(__import__("time").time())):
        fake_settings["bridge3.pairing_state"] = v
        held, _ = await _bridge_outbound_held("bridge3")
        assert held is True, v


@pytest.mark.asyncio
async def test_bridge3_intermediate_states_hold(fake_settings):
    for v in (PAIRING_UNPAIRED, PAIRING_HISTORY_SYNC_QUARANTINE, PAIRING_LIVE_INBOUND_VERIFIED):
        fake_settings["bridge3.pairing_state"] = v
        held, state = await _bridge_outbound_held("bridge3")
        assert held is True, v
    # LIVE_INBOUND_VERIFIED specifically (owner: remains non-sending)
    fake_settings["bridge3.pairing_state"] = PAIRING_LIVE_INBOUND_VERIFIED
    assert (await _bridge_outbound_held("bridge3"))[0] is True


@pytest.mark.asyncio
async def test_bridge3_settings_store_unavailable_holds(monkeypatch):
    """get_runtime_setting itself swallows errors and returns default '' —
    which holds bridge3 anyway."""
    async def down(key, default=""):
        return default
    monkeypatch.setattr("modules.wa_chat_frontend.get_runtime_setting", down)
    held, _ = await _bridge_outbound_held("bridge3")
    assert held is True


@pytest.mark.asyncio
async def test_bridge3_settings_read_raises_holds(monkeypatch):
    async def boom(key, default=""):
        raise RuntimeError("db down")
    monkeypatch.setattr("modules.wa_chat_frontend.get_runtime_setting", boom)
    held, state = await _bridge_outbound_held("bridge3")
    assert held is True
    assert "read-failed" in state or "HELD" in state


@pytest.mark.asyncio
async def test_bridge3_import_of_settings_module_broken_holds(monkeypatch):
    """Even if the settings module cannot be imported, bridge3 holds."""
    import builtins
    real_import = builtins.__import__

    def guard(name, *a, **k):
        if name == "modules.wa_chat_frontend":
            raise ImportError("simulated")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", guard)
    held, _ = await _bridge_outbound_held("bridge3")
    assert held is True


@pytest.mark.asyncio
async def test_process_restart_with_missing_state_holds(fake_settings):
    """A fresh process reads the setting live from the DB every call (no
    in-process cache for runtime settings — get_runtime_setting does a
    fetch_one each time). So 'after a restart with the key still missing' is
    identical to 'key absent' → HELD."""
    # no cache to prime; just prove a fresh read with an absent key holds
    held, _ = await _bridge_outbound_held("bridge3")
    assert held is True
    # and that flipping the DB value takes effect on the very next call
    fake_settings["bridge3.pairing_state"] = PAIRING_OUTBOUND_OWNER_APPROVED
    assert (await _bridge_outbound_held("bridge3"))[0] is False
    del fake_settings["bridge3.pairing_state"]
    assert (await _bridge_outbound_held("bridge3"))[0] is True  # back to HELD, no stale cache


@pytest.mark.asyncio
async def test_no_runtime_setting_cache_exists_for_pairing_state():
    """Guard: get_runtime_setting must not memoise — the poller/dispatcher/
    sweep all rely on reading the authoritative DB value every time."""
    import inspect
    from modules.wa_chat_frontend import get_runtime_setting
    src = inspect.getsource(get_runtime_setting)
    assert "fetch_one" in src
    assert "cache" not in src.lower()
    assert "lru_cache" not in src


# --- bridge1 / bridge2 UNAFFECTED ---

@pytest.mark.asyncio
async def test_bridge1_bridge2_unset_is_normal(fake_settings):
    for b in ("bridge1", "bridge2"):
        held, state = await _bridge_outbound_held(b)
        assert held is False and state == ""


@pytest.mark.asyncio
async def test_bridge1_bridge2_unaffected_by_bridge3_state(fake_settings):
    fake_settings["bridge3.pairing_state"] = PAIRING_HISTORY_SYNC_QUARANTINE
    for b in ("bridge1", "bridge2"):
        held, state = await _bridge_outbound_held(b)
        assert held is False and state == ""


@pytest.mark.asyncio
async def test_bridge1_read_error_does_NOT_hold(monkeypatch):
    """bridge1/bridge2 keep fail-OPEN on a read error (unchanged behaviour)."""
    async def boom(key, default=""):
        raise RuntimeError("db down")
    monkeypatch.setattr("modules.wa_chat_frontend.get_runtime_setting", boom)
    for b in ("bridge1", "bridge2"):
        held, _ = await _bridge_outbound_held(b)
        assert held is False


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
    # F6 is applied to the WhatsApp bridges (bridge1 became the recruitment
    # entry number 2026-09-02); Meta is deliberately NOT in the tuple.
    assert 'bridge_name in ("bridge1", "bridge2", "bridge3")' in block
    assert "is_automated_system_message(text)" in block
    assert '"meta"' not in block.split("is_automated_system_message")[0].split("bridge_name in")[-1]


# ── the sanctioned pairing-state API ───────────────────────────────────────

@pytest.mark.asyncio
async def test_pairing_state_endpoint_validates_and_gates_release(monkeypatch):
    import modules.wa_chat_frontend as wf

    saved: dict[str, str] = {}

    async def fake_set(key, value):
        saved[key] = value

    async def fake_get(key, default=""):
        return saved.get(key, default)

    monkeypatch.setattr(wf, "set_runtime_setting", fake_set)
    monkeypatch.setattr(wf, "get_runtime_setting", fake_get)

    from fastapi import HTTPException

    # unknown bridge -> 400
    with pytest.raises(HTTPException) as e:
        await wf.set_bridge_pairing_state("bridgeX", wf.BridgePairingStatePatch(state="UNPAIRED"))
    assert e.value.status_code == 400

    # invalid state -> 400
    with pytest.raises(HTTPException) as e:
        await wf.set_bridge_pairing_state("bridge3", wf.BridgePairingStatePatch(state="LOL"))
    assert e.value.status_code == 400

    # HISTORY_SYNC_QUARANTINE -> ok, no confirm needed
    r = await wf.set_bridge_pairing_state(
        "bridge3", wf.BridgePairingStatePatch(state="HISTORY_SYNC_QUARANTINE"))
    assert r["pairing_state"] == "HISTORY_SYNC_QUARANTINE" and r["outbound_allowed"] is False
    assert saved["bridge3.pairing_state"] == "HISTORY_SYNC_QUARANTINE"

    # OUTBOUND_OWNER_APPROVED WITHOUT the confirm phrase -> 400, DB unchanged
    with pytest.raises(HTTPException) as e:
        await wf.set_bridge_pairing_state(
            "bridge3", wf.BridgePairingStatePatch(state="OUTBOUND_OWNER_APPROVED"))
    assert e.value.status_code == 400
    assert saved["bridge3.pairing_state"] == "HISTORY_SYNC_QUARANTINE"  # not released

    # wrong confirm phrase -> 400
    with pytest.raises(HTTPException):
        await wf.set_bridge_pairing_state(
            "bridge3", wf.BridgePairingStatePatch(state="OUTBOUND_OWNER_APPROVED",
                                                  confirm="RELEASE OUTBOUND bridge2"))
    assert saved["bridge3.pairing_state"] == "HISTORY_SYNC_QUARANTINE"

    # correct confirm phrase -> released
    r = await wf.set_bridge_pairing_state(
        "bridge3", wf.BridgePairingStatePatch(state="OUTBOUND_OWNER_APPROVED",
                                              confirm="RELEASE OUTBOUND bridge3"))
    assert r["outbound_allowed"] is True
    assert saved["bridge3.pairing_state"] == "OUTBOUND_OWNER_APPROVED"


@pytest.mark.asyncio
async def test_api_cannot_set_state_leaves_bridge3_held(monkeypatch):
    """If the write endpoint fails (storage error), the state does not change;
    an unset/quarantine state keeps bridge3 HELD."""
    import modules.wa_chat_frontend as wf

    async def failing_set(key, value):
        raise RuntimeError("settings write failed")

    async def get_none(key, default=""):
        return default  # nothing was persisted

    monkeypatch.setattr(wf, "set_runtime_setting", failing_set)
    monkeypatch.setattr(wf, "get_runtime_setting", get_none)
    monkeypatch.setattr("modules.wa_chat_frontend.get_runtime_setting", get_none)

    with pytest.raises(RuntimeError):
        await wf.set_bridge_pairing_state(
            "bridge3", wf.BridgePairingStatePatch(state="OUTBOUND_OWNER_APPROVED",
                                                  confirm="RELEASE OUTBOUND bridge3"))
    # bridge3 is still HELD (fail-closed) because nothing was persisted
    held, _ = await _bridge_outbound_held("bridge3")
    assert held is True
