"""Media placeholder hardening (2026-08-29 maintenance).

Verified defect: modules.bridge_poller._fetch_new_messages fills a synthetic
`text = "[media:<type>]"` for an inbound media message with no caption/content
and no bridge-side `processed_text`. That placeholder is truthy, so downstream
it was routed to the intent classifier / RAG / semantic gate / Hermes AS IF the
sender wrote it (live: `RAG_SEARCH q='[media:audio]'`), and it defeated the
`not text` OCR/STT eligibility guards.

Fix (fazle-core only — NOT OCR/STT restoration; the bridge still delivers no
media bytes): `shared.media_text.is_media_placeholder` / `has_meaningful_text`;
`_poll_bridge` intercepts a bare placeholder right after the group guard —
marks the canonical record + queue row terminal (`media_unprocessed`), logs
`MEDIA_UNPROCESSED`, and `continue`s (no extraction attempt, no routing);
`process_bridge_inbound` has the same guard for the webhook path; the four
media-eligibility gates now use `has_meaningful_text(text)`.
"""
from __future__ import annotations

import inspect
import sqlite3
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from shared.media_text import has_meaningful_text, is_media_placeholder

pytestmark = pytest.mark.unit


# ── helper semantics ───────────────────────────────────────────────────────

@pytest.mark.parametrize("s", ["[media:audio]", "[media:image]", "[media:document]",
                                "[media:video]", "[media:ptt]", "  [media:audio]  ",
                                "[MEDIA:AUDIO]"])
def test_is_media_placeholder_true(s):
    assert is_media_placeholder(s) is True
    assert has_meaningful_text(s) is False


@pytest.mark.parametrize("s", ["", None, "   ", "এই রসিদটা দেখেন", "check [media:audio]",
                                "[media:audio] please listen", "[audio: unclear]",
                                "salary 12000", "[media]", "media:audio"])
def test_is_media_placeholder_false(s):
    assert is_media_placeholder(s) is False


@pytest.mark.parametrize("s,expected", [
    ("এই রসিদটা দেখেন", True),
    ("OCR: paid 5000 BDT", True),
    ("[media:audio]", False),
    ("   ", False),
    (None, False),
    ("", False),
])
def test_has_meaningful_text(s, expected):
    assert has_meaningful_text(s) is expected


# ── _fetch_new_messages: placeholder vs real caption ───────────────────────

_STOCK_SCHEMA = """
CREATE TABLE messages (
    id TEXT PRIMARY KEY, chat_jid TEXT, sender TEXT, content TEXT, timestamp TIMESTAMP,
    is_from_me BOOLEAN, media_type TEXT, filename TEXT, url TEXT,
    media_key BLOB, file_sha256 BLOB, file_enc_sha256 BLOB, file_length INTEGER
);
CREATE TABLE chats (jid TEXT PRIMARY KEY, name TEXT);
"""
_SINCE = datetime(2026, 8, 25, 9, 0, 0, tzinfo=timezone.utc)


def _mk(tmp_path, rows):
    con = sqlite3.connect(tmp_path / "m.db")
    con.executescript(_STOCK_SCHEMA)
    cols = ["id", "chat_jid", "sender", "content", "timestamp", "is_from_me",
            "media_type", "filename", "url"]
    for r in rows:
        con.execute(
            f"INSERT INTO messages ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
            tuple(r.get(c) for c in cols),
        )
    con.commit()
    con.close()
    w = sqlite3.connect(tmp_path / "wa.db")
    w.executescript("CREATE TABLE whatsmeow_lid_map (lid TEXT, pn TEXT);")
    w.commit()
    w.close()
    return str(tmp_path / "m.db"), str(tmp_path / "wa.db")


def test_media_without_caption_becomes_placeholder(tmp_path):
    from modules.bridge_poller import _fetch_new_messages
    mdb, wdb = _mk(tmp_path, [{
        "id": "V1", "chat_jid": "8801700000001@s.whatsapp.net",
        "sender": "8801700000001@s.whatsapp.net", "content": None,
        "timestamp": "2026-08-25 10:00:00", "is_from_me": 0,
        "media_type": "audio", "filename": "a.ogg", "url": "http://x/a.ogg",
    }])
    msgs, _ = _fetch_new_messages(mdb, wdb, _SINCE)
    assert len(msgs) == 1
    assert msgs[0]["text"] == "[media:audio]"
    assert is_media_placeholder(msgs[0]["text"]) is True


def test_media_with_real_caption_keeps_caption(tmp_path):
    from modules.bridge_poller import _fetch_new_messages
    mdb, wdb = _mk(tmp_path, [{
        "id": "V2", "chat_jid": "8801700000002@s.whatsapp.net",
        "sender": "8801700000002@s.whatsapp.net", "content": "এই রসিদটা দেখেন",
        "timestamp": "2026-08-25 10:00:00", "is_from_me": 0,
        "media_type": "image", "filename": "i.jpg", "url": "http://x/i.jpg",
    }])
    msgs, _ = _fetch_new_messages(mdb, wdb, _SINCE)
    assert msgs[0]["text"] == "এই রসিদটা দেখেন"
    assert has_meaningful_text(msgs[0]["text"]) is True


# ── process_bridge_inbound guard (webhook path + defense in depth) ─────────

async def _call_pbi(monkeypatch, text, *, is_extracted_media=False):
    """Call process_bridge_inbound with the classifier + recruitment eligibility
    spied. Returns (classify_calls, elig_calls)."""
    import modules.bridge_poller as bp
    classify_spy = MagicMock(return_value="unknown")
    elig_spy = AsyncMock(return_value={"eligible": False})
    monkeypatch.setattr(bp, "classify_intent", classify_spy)
    monkeypatch.setattr(bp, "recruitment_eligibility", elig_spy)
    # Anything past the guard would need more DB stubs; the recruitment
    # eligibility call is the first awaited step after classify_intent, so if
    # both spies are hit we have proven the message got past the guard. Raise
    # out of the eligibility spy to stop before real DB work.
    elig_spy.side_effect = RuntimeError("__past_guard__")
    try:
        await bp.process_bridge_inbound(
            "bridge1", "8801700000009", text,
            id_role="unknown", id_conf=0, id_name="",
            message_id="M1", chat_jid="8801700000009@s.whatsapp.net",
            is_extracted_media=is_extracted_media, core_message_id=None,
        )
    except RuntimeError as e:
        if "__past_guard__" not in str(e):
            raise
    return classify_spy, elig_spy


async def test_placeholder_never_reaches_classifier_or_rag(monkeypatch):
    for ph in ("[media:audio]", "[media:image]", "[media:document]", "[media:video]"):
        classify_spy, elig_spy = await _call_pbi(monkeypatch, ph)
        assert classify_spy.call_count == 0, f"classifier ran on {ph}"
        assert elig_spy.call_count == 0, f"recruitment_eligibility ran on {ph}"


async def test_real_text_still_routes(monkeypatch):
    classify_spy, elig_spy = await _call_pbi(monkeypatch, "আমি চাকরি করতে চাই")
    assert classify_spy.call_count == 1
    assert elig_spy.call_count == 1  # got past the guard


async def test_extracted_media_text_routes(monkeypatch):
    # is_extracted_media=True means `text` is genuine OCR/STT output — even if
    # it somehow looked placeholder-shaped, extraction succeeded, so route it.
    classify_spy, _ = await _call_pbi(monkeypatch, "[media:audio]", is_extracted_media=True)
    assert classify_spy.call_count == 1


# ── _poll_bridge structural guarantees (monolithic loop — source inspection,
#    same convention as test_group_ingestion_stage_e3) ─────────────────────

def _poll_bridge_src():
    import modules.bridge_poller as bp
    return inspect.getsource(bp._poll_bridge)


def test_placeholder_intercept_continues_before_routing_and_extraction():
    src = _poll_bridge_src()
    assert 'is_media_placeholder(text)' in src
    i_intercept = src.index('is_media_placeholder(text)')
    i_route = src.index('await process_bridge_inbound(')
    i_img_ocr = src.index('media_type") == "image" and not has_meaningful_text(text)')
    i_audio = src.index('("audio", "ptt") and not has_meaningful_text(text)')
    assert i_intercept < i_img_ocr < i_route
    assert i_intercept < i_audio < i_route
    # the intercept block marks the record terminal and continues, all BEFORE
    # any extraction attempt or routing
    block = src[i_intercept:i_img_ocr]
    assert 'media_unprocessed' in block
    assert '_mark_core_queue_status(bridge_name, msg, "skipped", "media_unprocessed")' in block
    assert '_finalize_core_copy(' in block
    assert 'MEDIA_UNPROCESSED' in block
    i_continue = src.index('continue', i_intercept)
    assert i_continue < i_img_ocr  # continue fires before the OCR gate
    # nothing routes or extracts inside the intercept block
    assert 'process_bridge_inbound' not in block
    assert 'process_voice' not in block
    assert 'process_document' not in block


def test_media_eligibility_gates_use_meaningful_text():
    src = _poll_bridge_src()
    # no bare `and not text and msg.get("filename")` truthiness gate remains
    assert 'and not text and msg.get("filename")' not in src
    assert src.count('not has_meaningful_text(text) and msg.get("filename")') == 3
    assert 'media_type") == "image" and has_meaningful_text(text)' in src
