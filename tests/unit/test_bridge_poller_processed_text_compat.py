"""Schema-tolerance for bridge message stores that lack `processed_text`.

RCA (2026-08-29 maintenance session): `processed_text` is a legacy in-house
OCR/STT column that is NOT part of the stock whatsmeow/whatsapp-mcp bridge
schema. bridge1's store, re-created by the current stock binary after its
2026-08-24 re-pair, has no such column, while the older bridge2/bridge3
stores still do. `modules.bridge_poller._fetch_new_messages` (and the FPE
historical-sync / gap-scan readers) named the column unconditionally in
their SELECTs, so every poll against the new bridge1 store raised
`sqlite3.OperationalError: no such column: processed_text` — freezing
bridge1 ingestion at the 2026-08-24T15:19:43Z cursor.

Fix: `shared.sqlite_compat.processed_text_column()` probes the open
read-only connection with `PRAGMA table_info` and substitutes
`NULL AS processed_text` when the column is absent, keeping the row shape
identical. These tests pin that contract.
"""
from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone

import pytest

from modules.bridge_poller import _fetch_new_messages
from shared.sqlite_compat import has_column, processed_text_column

# Stock bridge `messages` schema — exactly the 13 columns the current binary
# creates (verified against /home/azim/bridges/bridge1/store/messages.db).
_STOCK_SCHEMA = """
CREATE TABLE messages (
    id TEXT PRIMARY KEY,
    chat_jid TEXT,
    sender TEXT,
    content TEXT,
    timestamp TIMESTAMP,
    is_from_me BOOLEAN,
    media_type TEXT,
    filename TEXT,
    url TEXT,
    media_key BLOB,
    file_sha256 BLOB,
    file_enc_sha256 BLOB,
    file_length INTEGER
);
CREATE TABLE chats (jid TEXT PRIMARY KEY, name TEXT);
"""

# Legacy schema = stock + the in-house additive column (bridge2/bridge3 today).
_LEGACY_SCHEMA = _STOCK_SCHEMA.replace(
    "file_length INTEGER\n);",
    "file_length INTEGER,\n    processed_text TEXT\n);",
)

_SINCE = datetime(2026, 8, 25, 9, 0, 0, tzinfo=timezone.utc)


def _make_whatsapp_db(path) -> str:
    con = sqlite3.connect(path)
    con.executescript(
        "CREATE TABLE whatsmeow_lid_map (lid TEXT, pn TEXT);"
    )
    con.commit()
    con.close()
    return str(path)


def _make_messages_db(path, schema: str, rows: list[dict]) -> str:
    con = sqlite3.connect(path)
    con.executescript(schema)
    cols = [
        "id", "chat_jid", "sender", "content", "timestamp",
        "is_from_me", "media_type", "filename", "url",
    ]
    has_pt = "processed_text" in schema
    if has_pt:
        cols.append("processed_text")
    placeholders = ", ".join("?" for _ in cols)
    for r in rows:
        con.execute(
            f"INSERT INTO messages ({', '.join(cols)}) VALUES ({placeholders})",
            tuple(r.get(c) for c in cols),
        )
    con.commit()
    con.close()
    return str(path)


def _digest(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


@pytest.fixture
def whatsapp_db(tmp_path):
    return _make_whatsapp_db(tmp_path / "whatsapp.db")


# ── helper contract ──────────────────────────────────────────────────────────

def test_helper_detects_column_presence(tmp_path):
    legacy = _make_messages_db(tmp_path / "legacy.db", _LEGACY_SCHEMA, [])
    stock = _make_messages_db(tmp_path / "stock.db", _STOCK_SCHEMA, [])

    con_legacy = sqlite3.connect(f"file:{legacy}?mode=ro", uri=True)
    con_stock = sqlite3.connect(f"file:{stock}?mode=ro", uri=True)
    try:
        assert has_column(con_legacy, "messages", "processed_text") is True
        assert has_column(con_stock, "messages", "processed_text") is False
        assert processed_text_column(con_legacy) == "processed_text"
        assert processed_text_column(con_stock) == "NULL AS processed_text"
        # missing table → False, never raises
        assert has_column(con_stock, "no_such_table", "x") is False
    finally:
        con_legacy.close()
        con_stock.close()


# ── A. legacy schema: real value is returned ─────────────────────────────────

def test_legacy_schema_returns_processed_text_value(tmp_path, whatsapp_db):
    db = _make_messages_db(
        tmp_path / "m.db", _LEGACY_SCHEMA,
        [{
            "id": "L1", "chat_jid": "8801700000001@s.whatsapp.net",
            "sender": "8801700000001@s.whatsapp.net", "content": None,
            "timestamp": "2026-08-25 10:00:00", "is_from_me": 0,
            "media_type": "image", "filename": "receipt.jpg", "url": "http://x/receipt.jpg",
            "processed_text": "OCR: paid 5000 BDT",
        }],
    )
    msgs, _ = _fetch_new_messages(db, whatsapp_db, _SINCE)
    assert len(msgs) == 1
    assert msgs[0]["processed_text"] == "OCR: paid 5000 BDT"
    # downstream text preference: content -> processed_text
    assert msgs[0]["text"] == "OCR: paid 5000 BDT"


# ── B. stock schema: query succeeds, processed_text is NULL ──────────────────

def test_stock_schema_query_succeeds_with_null_processed_text(tmp_path, whatsapp_db):
    db = _make_messages_db(
        tmp_path / "m.db", _STOCK_SCHEMA,
        [{
            "id": "S1", "chat_jid": "8801700000002@s.whatsapp.net",
            "sender": "8801700000002@s.whatsapp.net", "content": "plain hello",
            "timestamp": "2026-08-25 10:00:00", "is_from_me": 0,
            "media_type": None, "filename": None, "url": None,
        }],
    )
    msgs, _ = _fetch_new_messages(db, whatsapp_db, _SINCE)
    assert len(msgs) == 1
    assert "processed_text" in msgs[0]  # row shape unchanged
    assert msgs[0]["processed_text"] is None


# ── C. ordinary text ingestion still works (stock schema) ────────────────────

def test_stock_schema_ordinary_text_message(tmp_path, whatsapp_db):
    db = _make_messages_db(
        tmp_path / "m.db", _STOCK_SCHEMA,
        [{
            "id": "S2", "chat_jid": "8801700000003@s.whatsapp.net",
            "sender": "8801700000003@s.whatsapp.net", "content": "attendance: 12 present",
            "timestamp": "2026-08-25 11:00:00", "is_from_me": 0,
            "media_type": None, "filename": None, "url": None,
        }],
    )
    msgs, _ = _fetch_new_messages(db, whatsapp_db, _SINCE)
    assert [m["text"] for m in msgs] == ["attendance: 12 present"]
    assert msgs[0]["phone"] == "8801700000003"


# ── D. media message with filename/url but no processed_text stays ingestible ─

def test_stock_schema_media_only_message_is_ingestible(tmp_path, whatsapp_db):
    db = _make_messages_db(
        tmp_path / "m.db", _STOCK_SCHEMA,
        [{
            "id": "S3", "chat_jid": "8801700000004@s.whatsapp.net",
            "sender": "8801700000004@s.whatsapp.net", "content": None,
            "timestamp": "2026-08-25 12:00:00", "is_from_me": 0,
            "media_type": "image", "filename": "img_1.jpg", "url": "http://x/img_1.jpg",
        }],
    )
    msgs, _ = _fetch_new_messages(db, whatsapp_db, _SINCE)
    assert len(msgs) == 1
    assert msgs[0]["text"] == "[media:image]"


# ── E. poll cursor advances normally ────────────────────────────────────────

def test_cursor_advances_on_stock_schema(tmp_path, whatsapp_db):
    db = _make_messages_db(
        tmp_path / "m.db", _STOCK_SCHEMA,
        [
            {
                "id": "S4", "chat_jid": "8801700000005@s.whatsapp.net",
                "sender": "8801700000005@s.whatsapp.net", "content": "first",
                "timestamp": "2026-08-25 10:00:00", "is_from_me": 0,
                "media_type": None, "filename": None, "url": None,
            },
            {
                "id": "S5", "chat_jid": "8801700000005@s.whatsapp.net",
                "sender": "8801700000005@s.whatsapp.net", "content": "second",
                "timestamp": "2026-08-25 13:30:00", "is_from_me": 0,
                "media_type": None, "filename": None, "url": None,
            },
        ],
    )
    msgs, max_ts = _fetch_new_messages(db, whatsapp_db, _SINCE)
    assert len(msgs) == 2
    assert max_ts > _SINCE
    assert max_ts == datetime(2026, 8, 25, 13, 30, 0, tzinfo=timezone.utc)


# ── F. no duplicate rows from the NULL fallback ─────────────────────────────

def test_no_duplicate_rows_from_fallback(tmp_path, whatsapp_db):
    db = _make_messages_db(
        tmp_path / "m.db", _STOCK_SCHEMA,
        [{
            "id": "S6", "chat_jid": "8801700000006@s.whatsapp.net",
            "sender": "8801700000006@s.whatsapp.net", "content": "only once",
            "timestamp": "2026-08-25 10:00:00", "is_from_me": 0,
            "media_type": None, "filename": None, "url": None,
        }],
    )
    msgs, _ = _fetch_new_messages(db, whatsapp_db, _SINCE)
    assert [m["id"] for m in msgs] == ["S6"]


# ── G. legacy schema still fully compatible (bridge2/bridge3 case) ───────────

def test_legacy_schema_text_and_media_filtering_unchanged(tmp_path, whatsapp_db):
    db = _make_messages_db(
        tmp_path / "m.db", _LEGACY_SCHEMA,
        [
            {
                "id": "L2", "chat_jid": "8801700000007@s.whatsapp.net",
                "sender": "8801700000007@s.whatsapp.net", "content": "text row",
                "timestamp": "2026-08-25 10:00:00", "is_from_me": 0,
                "media_type": None, "filename": None, "url": None,
                "processed_text": None,
            },
            {
                "id": "L3", "chat_jid": "8801700000007@s.whatsapp.net",
                "sender": "8801700000007@s.whatsapp.net", "content": None,
                "timestamp": "2026-08-25 10:05:00", "is_from_me": 0,
                "media_type": "reaction", "filename": None, "url": None,
                "processed_text": None,
            },
        ],
    )
    msgs, _ = _fetch_new_messages(db, whatsapp_db, _SINCE)
    # reaction row filtered out by media_type NOT IN (...), text row kept
    assert [m["id"] for m in msgs] == ["L2"]


# ── H. the schema probe performs no writes ──────────────────────────────────

def test_schema_probe_does_not_write_to_store(tmp_path, whatsapp_db):
    db = _make_messages_db(
        tmp_path / "m.db", _STOCK_SCHEMA,
        [{
            "id": "S7", "chat_jid": "8801700000008@s.whatsapp.net",
            "sender": "8801700000008@s.whatsapp.net", "content": "hello",
            "timestamp": "2026-08-25 10:00:00", "is_from_me": 0,
            "media_type": None, "filename": None, "url": None,
        }],
    )
    before = _digest(db)
    _fetch_new_messages(db, whatsapp_db, _SINCE)
    _fetch_new_messages(db, whatsapp_db, _SINCE)
    assert _digest(db) == before
    # column still absent — nothing added it
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        assert has_column(con, "messages", "processed_text") is False
    finally:
        con.close()
