"""Stage E2/E3/E5 (2026-08-28) -- controlled Office-group ingestion.

modules.bridge_poller._fetch_new_messages()'s new approved_group_jids
parameter, and structural/source-inspection proof that the group-message
short-circuit (persistence only, never automation) is positioned before
every automation-triggering step in _poll_bridge()'s loop body -- the
same source-inspection convention this repo already established for
proving deep pipeline wiring (see e.g. TestHermesRelayReplyOriginPreserved
/ router-guard source checks in test_wbom_actor_attribution_stage_b.py)
when a full integration mock of _poll_bridge() itself isn't practical.
"""
from __future__ import annotations

import inspect
import os
import sqlite3
import tempfile
from datetime import datetime, timezone

import pytest

import modules.bridge_poller as bp


def _make_messages_db(rows: list[tuple]) -> str:
    """rows: (id, chat_jid, sender, content, timestamp)."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE messages (id TEXT PRIMARY KEY, chat_jid TEXT, sender TEXT, "
        "content TEXT, timestamp TEXT, media_type TEXT, processed_text TEXT, "
        "filename TEXT, url TEXT, is_from_me INTEGER)"
    )
    conn.executemany(
        "INSERT INTO messages (id, chat_jid, sender, content, timestamp, is_from_me) VALUES (?,?,?,?,?,0)",
        rows,
    )
    conn.commit()
    conn.close()
    return path


OFFICE_JID = "120363426895117693@g.us"
OTHER_GROUP_JID = "120363405830697426@g.us"


class TestApprovedGroupSqlFilter:
    def test_default_empty_tuple_reproduces_prior_behavior_no_group_ever_included(self):
        """Regression: the byte-for-byte pre-Stage-E behavior for every
        existing caller that doesn't pass approved_group_jids."""
        db = _make_messages_db([
            ("m1", OFFICE_JID, "8801912345678@s.whatsapp.net", "office chat", "2026-08-28 10:00:00"),
            ("m2", "8801700000000@s.whatsapp.net", "8801700000000@s.whatsapp.net", "dm", "2026-08-28 10:00:00"),
        ])
        try:
            since = datetime(2026, 8, 28, 9, 0, 0, tzinfo=timezone.utc)
            msgs, _ = bp._fetch_new_messages(db, db, since)
            jids = [m["chat_jid"] for m in msgs]
        finally:
            os.remove(db)
        assert OFFICE_JID not in jids
        assert "8801700000000@s.whatsapp.net" in jids

    def test_approved_jid_is_included_unapproved_group_stays_excluded(self):
        db = _make_messages_db([
            ("m1", OFFICE_JID, "8801912345678@s.whatsapp.net", "office chat", "2026-08-28 10:00:00"),
            ("m2", OTHER_GROUP_JID, "8801912345678@s.whatsapp.net", "other group chat", "2026-08-28 10:00:00"),
            ("m3", "8801700000000@s.whatsapp.net", "8801700000000@s.whatsapp.net", "dm", "2026-08-28 10:00:00"),
        ])
        try:
            since = datetime(2026, 8, 28, 9, 0, 0, tzinfo=timezone.utc)
            msgs, _ = bp._fetch_new_messages(db, db, since, approved_group_jids=(OFFICE_JID,))
            jids = sorted(m["chat_jid"] for m in msgs)
        finally:
            os.remove(db)
        assert jids == sorted([OFFICE_JID, "8801700000000@s.whatsapp.net"])
        assert OTHER_GROUP_JID not in jids

    def test_newsletter_and_status_broadcast_never_included_even_if_somehow_passed(self):
        """Defense in depth: approved_group_jids only ever widens the
        @g.us exception, never the separate newsletter/status@broadcast
        exclusions -- even a malformed/mistaken value there can't leak
        those categories through."""
        db = _make_messages_db([
            ("m1", "123@newsletter", "x@s.whatsapp.net", "newsletter", "2026-08-28 10:00:00"),
            ("m2", "status@broadcast", "x@s.whatsapp.net", "status", "2026-08-28 10:00:00"),
        ])
        try:
            since = datetime(2026, 8, 28, 9, 0, 0, tzinfo=timezone.utc)
            msgs, _ = bp._fetch_new_messages(db, db, since, approved_group_jids=("123@newsletter", "status@broadcast"))
        finally:
            os.remove(db)
        assert msgs == []


class TestGroupShortCircuitOrdering:
    """Source-inspection proof: the group-message continue happens
    strictly BEFORE OCR, identity detection, and process_bridge_inbound()
    -- the actual automation-triggering steps -- and strictly AFTER
    _save_raw_core_copy() (so persistence always happens first)."""

    def test_group_shortcircuit_is_after_persistence_and_before_ocr_and_routing(self):
        src = inspect.getsource(bp)
        persist_idx = src.index("core_message_id = await _save_raw_core_copy(bridge_name, msg, phone, text)")
        shortcircuit_idx = src.index('_group_chat_jid.endswith("@g.us")')
        ocr_idx = src.index('if msg.get("media_type") == "image" and has_meaningful_text(text):')
        identity_idx = src.index("from modules.identity_brain import detect_identity")
        routing_idx = src.index("await process_bridge_inbound(")

        assert persist_idx < shortcircuit_idx < ocr_idx
        assert shortcircuit_idx < identity_idx
        assert shortcircuit_idx < routing_idx

    def test_group_shortcircuit_finalizes_the_queue_row_not_left_stuck(self):
        """Regression class this must not repeat: a stuck fazle_message_queue
        row from an unfinalized 'processing' status (2026-08-04 incident,
        see this module's own comment)."""
        src = inspect.getsource(bp)
        shortcircuit_block = src[src.index('_group_chat_jid.endswith("@g.us")'):src.index('_group_chat_jid.endswith("@g.us")') + 400]
        assert "_mark_core_queue_status" in shortcircuit_block
        assert "continue" in shortcircuit_block

    def test_approved_jids_sourced_from_group_identity_module_not_hardcoded(self):
        src = inspect.getsource(bp)
        assert "list_approved_group_jids" in src
        assert OFFICE_JID not in src  # never a literal JID in bridge_poller itself


class TestE5CrossBridgeGroupDedup:
    """Stage E5: the SAME physical WhatsApp group message can genuinely be
    observed independently by two different bridges (both bridge1 and
    bridge2 are members of the Office group, confirmed in Stage D1/E1's
    live sync). _core_message_hash(source, msg_id) includes `source`
    (the bridge name) -- proven here to mean two bridges' observations of
    the identical provider msg_id are deliberately NOT collapsed into one
    row (a different hash each), while preserving full provenance
    (source/platform + source_message_ref) on each -- rather than
    guessing which one to keep or silently merging on text+timestamp."""

    def test_same_provider_msg_id_different_bridges_produce_different_hashes(self):
        h1 = bp._core_message_hash("bridge1", "SAME_PROVIDER_MSG_ID")
        h2 = bp._core_message_hash("bridge2", "SAME_PROVIDER_MSG_ID")
        assert h1 != h2, "cross-bridge observations of the same message must not collide/collapse"

    def test_same_bridge_same_msg_id_is_the_real_intra_bridge_dedup_case(self):
        """Within ONE bridge, the same provider msg_id must hash
        identically (this is the genuine, already-relied-upon dedup case
        -- e.g. a bridge restart re-delivering the same row)."""
        h1 = bp._core_message_hash("bridge1", "SAME_ID")
        h2 = bp._core_message_hash("bridge1", "SAME_ID")
        assert h1 == h2

    def test_dedup_is_never_based_on_body_text_or_timestamp_alone(self):
        """Two structurally different provider message refs must never
        collide just because the eventual message body/timestamp might
        coincide -- the hash function's only inputs are source and the
        provider's own message ref, proven by inspecting its signature."""
        sig = inspect.signature(bp._core_message_hash)
        assert list(sig.parameters) == ["source", "source_message_ref"]

    def test_provenance_is_preserved_per_row_source_message_ref_and_platform(self):
        """Each row's own source_context/source_message_ref (set inside
        _save_raw_core_copy, unchanged by Stage E) plus its `platform`
        column together let a caller identify 'these two rows are the
        same physical group message, observed by two different bridges'
        without this stage forcing a merge/delete of either."""
        src = inspect.getsource(bp._save_raw_core_copy)
        assert "source_message_ref" in src
        assert '"chat_jid": msg.get("chat_jid")' in src


class TestNoAutomationForGroupMessages:
    def test_short_circuit_never_calls_process_bridge_inbound_in_same_branch(self):
        """The continue statement in the group branch must return control
        to the for-loop before process_bridge_inbound() -- structurally
        proven above by ordering; this additionally proves the group
        branch itself contains no call to it."""
        src = inspect.getsource(bp)
        start = src.index('_group_chat_jid.endswith("@g.us")')
        end = src.index("# Bridge-side OCR may already populate processed_text")
        block = src[start:end]
        assert "process_bridge_inbound" not in block
        assert "detect_identity" not in block
