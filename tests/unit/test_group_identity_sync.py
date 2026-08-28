"""Stage D1 (2026-08-28) -- modules.group_identity.

Uses a real temporary SQLite file (matching the actual bridge chats-table
shape) rather than mocking sqlite3, so the read path is genuinely
exercised, and a real (mocked) fetch_one to avoid needing live Postgres.
"""
from __future__ import annotations

import sqlite3
import tempfile
import os
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.asyncio


def _make_chats_db(rows: list[tuple[str, str, str]]) -> str:
    """rows: list of (jid, name, last_message_time)."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE chats (jid TEXT PRIMARY KEY, name TEXT, last_message_time TIMESTAMP)")
    conn.executemany("INSERT INTO chats VALUES (?, ?, ?)", rows)
    conn.commit()
    conn.close()
    return path


class TestGroupChatDiscovery:
    async def test_only_group_jids_are_read_individual_chats_excluded(self):
        db_path = _make_chats_db([
            ("120363426895117693@g.us", "Office group", "2026-08-28 06:51:04+02:00"),
            ("8801700000000@s.whatsapp.net", "Some Individual", "2026-08-28 06:00:00+02:00"),
        ])
        try:
            from modules.group_identity import _read_bridge_group_chats
            rows = _read_bridge_group_chats(db_path)
            assert len(rows) == 1
            assert rows[0]["jid"] == "120363426895117693@g.us"
        finally:
            os.remove(db_path)

    async def test_sync_upserts_authoritative_bridge_metadata_and_parses_timestamp(self):
        db_path = _make_chats_db([
            ("120363426895117693@g.us", "''Al-Rifa'i Integrated Services\" Office group", "2026-08-28 06:51:04+02:00"),
        ])
        captured = {}

        async def _fake_fetch_one(sql, *args):
            captured["sql"] = sql
            captured["args"] = args
            return {"group_jid": args[0]}

        try:
            with patch("modules.group_identity._bridge_db_path", return_value=db_path), \
                 patch("modules.group_identity.fetch_one", new=_fake_fetch_one):
                from modules.group_identity import sync_group_identities
                synced = await sync_group_identities("bridge1")
        finally:
            os.remove(db_path)

        assert synced == 1
        from datetime import datetime
        assert isinstance(captured["args"][3], datetime), "last_seen_at must be a real datetime, not a raw str"
        assert captured["args"][0] == "120363426895117693@g.us"
        assert captured["args"][1] == "bridge1"

    async def test_business_label_is_never_touched_by_sync(self):
        """The upsert SQL's ON CONFLICT clause must never assign
        business_label -- it is only ever set by an explicit, separate
        action, never inferred here."""
        from modules.group_identity import sync_group_identities
        db_path = _make_chats_db([("120363426895117693@g.us", "Office group", "2026-08-28 06:51:04+02:00")])
        captured = {}

        async def _fake_fetch_one(sql, *args):
            captured["sql"] = sql
            return {"group_jid": args[0]}

        try:
            with patch("modules.group_identity._bridge_db_path", return_value=db_path), \
                 patch("modules.group_identity.fetch_one", new=_fake_fetch_one):
                await sync_group_identities("bridge1")
        finally:
            os.remove(db_path)
        assert "business_label" not in captured["sql"].split("ON CONFLICT")[1]

    async def test_group_rename_does_not_change_identity_key(self):
        """A group's display_name changing on WhatsApp must update the
        display_name column but never require a new identity row -- the
        primary key is (group_jid, source_bridge), not display_name."""
        db_path_v1 = _make_chats_db([("120363426895117693@g.us", "Old Name", "2026-08-01 00:00:00+02:00")])
        db_path_v2 = _make_chats_db([("120363426895117693@g.us", "New Name", "2026-08-28 00:00:00+02:00")])
        calls = []

        async def _fake_fetch_one(sql, *args):
            calls.append(args)
            return {"group_jid": args[0]}

        try:
            from modules.group_identity import sync_group_identities
            with patch("modules.group_identity._bridge_db_path", return_value=db_path_v1), \
                 patch("modules.group_identity.fetch_one", new=_fake_fetch_one):
                await sync_group_identities("bridge1")
            with patch("modules.group_identity._bridge_db_path", return_value=db_path_v2), \
                 patch("modules.group_identity.fetch_one", new=_fake_fetch_one):
                await sync_group_identities("bridge1")
        finally:
            os.remove(db_path_v1)
            os.remove(db_path_v2)

        # Same (jid, bridge) key both times -- an UPSERT, not a new row.
        assert calls[0][0] == calls[1][0] == "120363426895117693@g.us"
        assert calls[0][1] == calls[1][1] == "bridge1"
        assert calls[0][2] == "Old Name"
        assert calls[1][2] == "New Name"

    async def test_bridge3_zero_groups_is_not_an_error(self):
        db_path = _make_chats_db([])
        try:
            from modules.group_identity import sync_group_identities
            with patch("modules.group_identity._bridge_db_path", return_value=db_path):
                synced = await sync_group_identities("bridge3")
        finally:
            os.remove(db_path)
        assert synced == 0

    async def test_no_configured_path_returns_zero_not_a_crash(self):
        from modules.group_identity import sync_group_identities
        with patch("modules.group_identity._bridge_db_path", return_value=None):
            synced = await sync_group_identities("bridge1")
        assert synced == 0

    async def test_sync_all_bridges_covers_all_three(self):
        from modules.group_identity import sync_all_bridges
        with patch("modules.group_identity.sync_group_identities", new=AsyncMock(return_value=5)):
            result = await sync_all_bridges()
        assert set(result) == {"bridge1", "bridge2", "bridge3"}


class TestE1BusinessLabelAssignment:
    async def test_set_business_label_updates_all_rows_for_the_jid(self):
        captured = {}

        async def _fake_execute(sql, *args):
            captured["sql"] = sql
            captured["args"] = args
            return "UPDATE 2"

        from modules.group_identity import set_group_business_label
        with patch("app.database.execute", new=_fake_execute):
            n = await set_group_business_label("120363426895117693@g.us", "office_internal")
        assert n == 2
        assert captured["args"] == ("office_internal", "120363426895117693@g.us")
        assert "WHERE group_jid = $2" in captured["sql"]

    async def test_is_approved_group_jid_true_only_with_label(self):
        from modules.group_identity import is_approved_group_jid

        async def _labeled(sql, *args):
            return {"1": 1}

        async def _unlabeled(sql, *args):
            return None

        with patch("app.database.fetch_one", new=_labeled):
            assert await is_approved_group_jid("x@g.us") is True
        with patch("app.database.fetch_one", new=_unlabeled):
            assert await is_approved_group_jid("y@g.us") is False

    async def test_list_approved_group_jids_caches_and_can_be_force_refreshed(self):
        from modules import group_identity as gi
        gi._approved_jids_cache["jids"] = []
        gi._approved_jids_cache["checked_at"] = 0.0

        calls = {"n": 0}

        async def _fake_fetch_all(sql, *args):
            calls["n"] += 1
            return [{"group_jid": "a@g.us"}]

        with patch("app.database.fetch_all", new=_fake_fetch_all):
            r1 = await gi.list_approved_group_jids()
            r2 = await gi.list_approved_group_jids()  # cached, no second DB call
        assert r1 == r2 == ["a@g.us"]
        assert calls["n"] == 1

        with patch("app.database.fetch_all", new=_fake_fetch_all):
            await gi.list_approved_group_jids(force_refresh=True)
        assert calls["n"] == 2

    async def test_list_approved_group_jids_keeps_stale_cache_on_db_error(self):
        from modules import group_identity as gi
        gi._approved_jids_cache["jids"] = ["stale@g.us"]
        gi._approved_jids_cache["checked_at"] = 0.0

        async def _boom(sql, *args):
            raise RuntimeError("db down")

        with patch("app.database.fetch_all", new=_boom):
            result = await gi.list_approved_group_jids(force_refresh=True)
        assert result == ["stale@g.us"]


class TestNoHardcodedGroupGuessing:
    def test_source_never_hardcodes_the_office_group_jid_or_name(self):
        """Stage D1's explicit rule: identity must come ONLY from synced
        bridge metadata, never a manually typed JID or name-substring
        match. Structural proof: the module source contains no literal
        JID and no 'Al-Rifa' / 'office' substring match logic."""
        import inspect
        import modules.group_identity as gi
        src = inspect.getsource(gi)
        assert "120363426895117693" not in src
        assert "al-rifa" not in src.lower()
        assert "office group" not in src.lower()

    def test_conversation_state_source_never_infers_from_role_labels(self):
        """Stage D6/D7's explicit rule: state comes only from the
        authoritative business record columns (post_intake_status,
        wbom_escort_programs.status), never from internal_coordinator/
        supervisor-style role labels or message content."""
        import inspect
        import modules.conversation_state as cs
        src = inspect.getsource(cs)
        assert "internal_coordinator" not in src
        assert "supervisor" not in src.lower()
