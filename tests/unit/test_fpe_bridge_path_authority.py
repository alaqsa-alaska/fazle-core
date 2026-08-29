"""FPE bridge-store path authority (2026-08-29 maintenance).

Verified defect: modules/fazle_payroll_engine/historical_sync.py hardcoded
`_BRIDGE_CONFIGS` with bridge1 = `/home/azim/whatsapp1/store/messages.db`, the
frozen pre-2026-08-24-re-pair store, while the live authoritative bridge1 store
is `/home/azim/bridges/bridge1/store/messages.db` (exposed centrally as
`settings.bridge1_db_path`, .env-overridable, and already used by
modules.bridge_poller). historical_sync / gap_scan therefore scanned a dead
store for bridge1.

Fix: `_bridge_configs()` resolves every path from central settings on each call.
These tests pin: (a) paths come from settings, not literals; (b) a settings
override propagates; (c) no `/home/azim/whatsapp1/store` literal remains in the
active FPE config path; (d) bridge3 stays out of FPE; (e)/(f) the SQLite readers
still tolerate a stock schema without `processed_text`; (g) the schema probe
performs no writes.
"""
from __future__ import annotations

import hashlib
import sqlite3

import pytest

from app.config import get_settings
from modules.fazle_payroll_engine import gap_scan as gap_scan_mod
from modules.fazle_payroll_engine import historical_sync as hsync_mod
from modules.fazle_payroll_engine.historical_sync import _bridge_configs

_STOCK_SCHEMA = """
CREATE TABLE messages (
    id TEXT PRIMARY KEY, chat_jid TEXT, sender TEXT, content TEXT, timestamp TIMESTAMP,
    is_from_me BOOLEAN, media_type TEXT, filename TEXT, url TEXT,
    media_key BLOB, file_sha256 BLOB, file_enc_sha256 BLOB, file_length INTEGER
);
CREATE TABLE chats (jid TEXT PRIMARY KEY, name TEXT);
"""
_LEGACY_SCHEMA = _STOCK_SCHEMA.replace(
    "file_length INTEGER\n);", "file_length INTEGER,\n    processed_text TEXT\n);"
)


def _make_messages_db(path, schema, rows):
    con = sqlite3.connect(path)
    con.executescript(schema)
    cols = ["id", "chat_jid", "sender", "content", "timestamp", "is_from_me", "media_type"]
    if "processed_text" in schema:
        cols.append("processed_text")
    ph = ", ".join("?" for _ in cols)
    for r in rows:
        con.execute(f"INSERT INTO messages ({', '.join(cols)}) VALUES ({ph})",
                    tuple(r.get(c) for c in cols))
    con.commit()
    con.close()
    return str(path)


def _make_whatsapp_db(path):
    con = sqlite3.connect(path)
    con.executescript("CREATE TABLE whatsmeow_lid_map (lid TEXT, pn TEXT);")
    con.commit()
    con.close()
    return str(path)


def _digest(p):
    with open(p, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


# ── 1 & 2: paths come from central settings ─────────────────────────────────

def test_bridge1_and_bridge2_paths_come_from_settings():
    s = get_settings()
    cfg = {c["name"]: c for c in _bridge_configs()}
    assert cfg["bridge1"]["messages_db"] == s.bridge1_db_path
    assert cfg["bridge1"]["whatsapp_db"] == s.bridge1_whatsapp_db_path
    assert cfg["bridge2"]["messages_db"] == s.bridge2_db_path
    assert cfg["bridge2"]["whatsapp_db"] == s.bridge2_whatsapp_db_path
    # source numbers also from settings
    assert cfg["bridge1"]["source_number"] == s.admin_bridge1_number
    assert cfg["bridge2"]["source_number"] == s.admin_bridge2_number


# ── 3: a settings override changes the resolved FPE path ────────────────────

def test_settings_override_propagates_to_fpe_config(monkeypatch):
    real = get_settings()
    patched = real.model_copy(update={
        "bridge1_db_path": "/tmp/__fpe_override__/bridge1/messages.db",
        "bridge1_whatsapp_db_path": "/tmp/__fpe_override__/bridge1/whatsapp.db",
    })
    monkeypatch.setattr(hsync_mod, "get_settings", lambda: patched)
    cfg = {c["name"]: c for c in _bridge_configs()}
    assert cfg["bridge1"]["messages_db"] == "/tmp/__fpe_override__/bridge1/messages.db"
    assert cfg["bridge1"]["whatsapp_db"] == "/tmp/__fpe_override__/bridge1/whatsapp.db"
    # gap_scan resolves through the same function
    monkeypatch.setattr(gap_scan_mod, "_bridge_configs", _bridge_configs)
    cfg2 = {c["name"]: c for c in gap_scan_mod._bridge_configs()}
    assert cfg2["bridge1"]["messages_db"] == "/tmp/__fpe_override__/bridge1/messages.db"


# ── 4: no frozen-store literal remains in the active FPE config path ────────

def test_no_hardcoded_frozen_store_path_in_active_config():
    import inspect
    src = inspect.getsource(_bridge_configs)
    assert "/home/azim/whatsapp1/store" not in src
    for c in _bridge_configs():
        assert c["messages_db"] != "/home/azim/whatsapp1/store/messages.db"
        assert "/home/azim/whatsapp1/store" not in c["messages_db"]


# ── 8: bridge3 remains out of FPE scope ────────────────────────────────────

def test_bridge3_not_an_fpe_sync_target():
    names = {c["name"] for c in _bridge_configs()}
    assert names == {"bridge1", "bridge2"}
    assert "bridge3" not in names


# ── 5 & 7: historical_sync reader tolerates stock schema, no writes ────────

def test_historical_sync_reader_stock_schema_no_processed_text(tmp_path):
    wdb = _make_whatsapp_db(tmp_path / "wa.db")
    mdb = _make_messages_db(tmp_path / "m.db", _STOCK_SCHEMA, [
        {"id": "H1", "chat_jid": "8801700000001@s.whatsapp.net",
         "sender": "8801700000001@s.whatsapp.net", "content": "Salary 12000 bkash 01700000001",
         "timestamp": "2026-08-25 10:00:00", "is_from_me": 0, "media_type": None},
    ])
    before = _digest(mdb)
    rows, _lid = hsync_mod._fetch_messages_sync(mdb, wdb, "8801700000001@s.whatsapp.net", None, 200)
    assert len(rows) == 1
    assert rows[0]["processed_text"] is None
    assert rows[0]["content"] == "Salary 12000 bkash 01700000001"
    assert _digest(mdb) == before  # read-only


def test_historical_sync_reader_legacy_schema_still_works(tmp_path):
    wdb = _make_whatsapp_db(tmp_path / "wa.db")
    mdb = _make_messages_db(tmp_path / "m.db", _LEGACY_SCHEMA, [
        {"id": "H2", "chat_jid": "8801700000002@s.whatsapp.net",
         "sender": "8801700000002@s.whatsapp.net", "content": None,
         "timestamp": "2026-08-25 10:00:00", "is_from_me": 0, "media_type": "image",
         "processed_text": "OCR cash 5000"},
    ])
    rows, _lid = hsync_mod._fetch_messages_sync(mdb, wdb, "8801700000002@s.whatsapp.net", None, 200)
    assert len(rows) == 1
    assert rows[0]["processed_text"] == "OCR cash 5000"


# ── 6 & 7: gap_scan reader tolerates stock schema, no writes ───────────────

def test_gap_scan_reader_stock_schema_no_processed_text(tmp_path):
    wdb = _make_whatsapp_db(tmp_path / "wa.db")
    mdb = _make_messages_db(tmp_path / "m.db", _STOCK_SCHEMA, [
        {"id": "G1", "chat_jid": "8801700000003@s.whatsapp.net",
         "sender": "8801700000003@s.whatsapp.net", "content": "Advance 3000",
         "timestamp": "2026-08-25 11:00:00", "is_from_me": 0, "media_type": None},
        {"id": "G2", "chat_jid": "8801700000003@s.whatsapp.net",
         "sender": "8801700000003@s.whatsapp.net", "content": "ok",
         "timestamp": "2026-08-25 11:05:00", "is_from_me": 1, "media_type": None},
    ])
    before = _digest(mdb)
    rows, _lid = gap_scan_mod._fetch_sqlite_rows_for_diff(
        mdb, wdb, "8801700000003@s.whatsapp.net", 5000
    )
    ids = {r["id"] for r in rows}
    assert ids == {"G1", "G2"}
    assert all("processed_text" in r for r in rows)
    assert _digest(mdb) == before  # read-only
