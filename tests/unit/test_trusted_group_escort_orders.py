"""Unit tests -- Trusted Group escort-order correlation (2026-07-22,
Owner-approved: 8 confirmed business rules on top of the "New Trusted
Group as additional source" design).

Covers: the narrow @g.us exception in
modules.bridge_poller._fetch_outgoing_escort_completions() (bridge2-only,
one exact JID); modules.escort.handle_group_escort_order() (authority
gate reuses _is_authoritative_bridge_confirmation()'s existing
no-specific-recipient branch); modules.escort.save_escort_programs()'s
group_sourced tagging + the new cross-channel dedup helper
(_find_existing_draft_for_order); modules.escort._update_program_confirmed()'s
field-comparison no-op guard (DB-level idempotency, including the
cross-channel "DM confirms, group repeats" case); and
modules.escort_roster.db.expire_stale_drafts()'s TTL exemption for
group-sourced drafts. Structural (source-inspection) tests close the
gap for the outgoing-scan loop itself, which -- like its sibling guards
in test_bridge_poller_system_notification_guard.py -- needs a live
bridge SQLite session to drive end-to-end and is proven wired instead
of re-executed.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.unit

GROUP_JID = "120363428977781821@g.us"  # Al-Aqsa Escort Program Group (resolved 2026-07-22)
OTHER_GROUP_JID = "120363408741101070@g.us"  # Escort Release Slip -- must stay excluded
ADMIN_BRIDGE2_NUMBER = "01711999999"

# Bare new-order text (no escort_name/escort_mobile) -- matches
# is_order_complete(), does NOT match is_completed_escort_draft().
CANONICAL_ORDER_TEXT = """
$ MV TRUSTGROUP TEST
A/c.Test Importer
soyabean meal
01) MV TG LIGHTER ONE
Cap: 1,000 M.T.
O/A to N.Para
Master: 01711000111
"""

# Completed-slip text -- matches is_completed_escort_draft() (has the
# Al-Aqsa signature + mv/lv/escort_name/escort_mobile/date all present).
COMPLETED_SLIP_TEXT = (
    "Mother Vessel: MV TG LIGHTER TWO\n"
    "Lighter Vessel: LV SLIP TEST\n"
    "Master Mobile: 01722000222\n"
    "Destination: Outer to Local\n"
    "Escort Name: Karim\n"
    "Escort Mobile: 01811111111\n"
    "Start Date: 22.07.2026 (9)\n"
    "Al-Aqsa Security Service"
)


def _use_pool(test_db_pool):
    import app.database as db_module
    db_module._pool = test_db_pool


def _authorize(monkeypatch):
    monkeypatch.setenv("ADMIN_BRIDGE2_NUMBER", ADMIN_BRIDGE2_NUMBER)
    monkeypatch.setenv("ESCORT_TRUSTED_SOURCES", "bridge1,bridge2,meta")
    from app.config import get_settings
    get_settings.cache_clear()


async def _insert_group_draft(test_db_pool, *, group_sourced: bool, hours_old: float, mv: str):
    """Mirrors tests/unit/test_dispatch_2a3_stage4_expiry_unassign.py's
    _insert_draft_program helper, extended with a remarks column so the
    TTL-exemption predicate (a regex match against wbom_escort_programs.remarks)
    has something to match against."""
    remarks = json.dumps({"group_sourced": group_sourced, "group_jid": GROUP_JID if group_sourced else None})
    created_at = datetime.now(timezone.utc) - timedelta(hours=hours_old)
    async with test_db_pool.acquire() as conn:
        prog = await conn.fetchrow(
            """INSERT INTO wbom_escort_programs
                  (mother_vessel, lighter_vessel, master_mobile, program_date, shift, status,
                   escort_name, escort_mobile, remarks)
               VALUES ($1,'LT TTL TEST','01799999999',$2,'D','draft','','',$3)
               RETURNING *""",
            mv, date(2026, 6, 1), remarks,
        )
        await conn.execute(
            """INSERT INTO escort_roster_entries
                  (program_id, mother_vessel, lighter_vessel, master_mobile, escort_name, escort_mobile,
                   start_date, start_shift, roster_status, created_at)
               VALUES ($1,$2,'LT TTL TEST','01799999999','','',$3,'D','draft',$4)""",
            prog["program_id"], mv, date(2026, 6, 1), created_at,
        )
    return dict(prog)


# ===========================================================================
# 1. SQL-level @g.us exception (modules.bridge_poller._fetch_outgoing_escort_completions)
# ===========================================================================

class TestFetchOutgoingEscortCompletionsGroupException:

    def _build_messages_db(self, tmp_path, rows):
        db_path = str(tmp_path / "messages.db")
        con = sqlite3.connect(db_path)
        con.execute(
            "CREATE TABLE messages (id TEXT, chat_jid TEXT, content TEXT, timestamp TEXT, is_from_me INTEGER)"
        )
        con.executemany(
            "INSERT INTO messages (id, chat_jid, content, timestamp, is_from_me) VALUES (?, ?, ?, ?, 1)",
            rows,
        )
        con.commit()
        con.close()
        return db_path

    def test_feature_flag_off_excludes_all_groups_unchanged(self, tmp_path):
        """Default (empty trusted_group_jid) must reproduce the exact
        pre-existing 2026-04-27 'no groups' behavior."""
        from modules.bridge_poller import _fetch_outgoing_escort_completions
        since = datetime.now(timezone.utc) - timedelta(hours=1)
        after = since + timedelta(minutes=1)
        db_path = self._build_messages_db(tmp_path, [
            ("m1", GROUP_JID, "hello from trusted group", after.isoformat()),
            ("m2", "8801711000111@s.whatsapp.net", "hello DM", after.isoformat()),
        ])
        msgs, _ = _fetch_outgoing_escort_completions(db_path, since)
        chat_jids = {m["chat_jid"] for m in msgs}
        assert GROUP_JID not in chat_jids
        assert "8801711000111@s.whatsapp.net" in chat_jids

    def test_trusted_group_jid_included_when_matching(self, tmp_path):
        from modules.bridge_poller import _fetch_outgoing_escort_completions
        since = datetime.now(timezone.utc) - timedelta(hours=1)
        after = since + timedelta(minutes=1)
        db_path = self._build_messages_db(tmp_path, [
            ("m1", GROUP_JID, "hello from trusted group", after.isoformat()),
        ])
        msgs, _ = _fetch_outgoing_escort_completions(db_path, since, trusted_group_jid=GROUP_JID)
        assert any(m["chat_jid"] == GROUP_JID for m in msgs)

    def test_other_group_stays_excluded_even_with_trusted_jid_set(self, tmp_path):
        """A DIFFERENT, similarly-purposed group (e.g. 'Escort Release Slip')
        must never leak in just because some other JID is trusted."""
        from modules.bridge_poller import _fetch_outgoing_escort_completions
        since = datetime.now(timezone.utc) - timedelta(hours=1)
        after = since + timedelta(minutes=1)
        db_path = self._build_messages_db(tmp_path, [
            ("m1", OTHER_GROUP_JID, "hello from a different group", after.isoformat()),
        ])
        msgs, _ = _fetch_outgoing_escort_completions(db_path, since, trusted_group_jid=GROUP_JID)
        assert not any(m["chat_jid"] == OTHER_GROUP_JID for m in msgs)


# ===========================================================================
# 2. handle_group_escort_order() -- authority gate + draft creation
# ===========================================================================

class TestHandleGroupEscortOrder:

    async def test_authorized_admin_creates_group_sourced_draft(self, test_db_pool, monkeypatch):
        _use_pool(test_db_pool)
        _authorize(monkeypatch)
        from modules.escort import handle_group_escort_order

        msg, extra = await handle_group_escort_order(
            CANONICAL_ORDER_TEXT, ADMIN_BRIDGE2_NUMBER, "bridge2", GROUP_JID, message_id="MSG1",
        )
        assert "program_id=" in msg

        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM wbom_escort_programs WHERE master_mobile = $1", "01711000111"
            )
        assert row is not None
        assert row["status"] == "draft"
        remarks = json.loads(row["remarks"])
        assert remarks["group_sourced"] is True
        assert remarks["group_jid"] == GROUP_JID

    async def test_unauthorized_sender_rejected(self, test_db_pool, monkeypatch):
        """A phone number that is NOT a registered admin number must never
        create a draft, even with a correctly-recognized group JID and
        trusted source string."""
        _use_pool(test_db_pool)
        _authorize(monkeypatch)
        from modules.escort import handle_group_escort_order

        msg, extra = await handle_group_escort_order(
            CANONICAL_ORDER_TEXT, "01799999999", "bridge2", GROUP_JID, message_id="MSG2",
        )
        assert msg == ""
        assert extra is None
        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM wbom_escort_programs WHERE master_mobile = $1", "01711000111"
            )
        assert row is None

    async def test_untrusted_source_rejected(self, test_db_pool, monkeypatch):
        """Even the real admin number is rejected if the source string
        itself isn't in ESCORT_TRUSTED_SOURCES -- authority requires both."""
        _use_pool(test_db_pool)
        monkeypatch.setenv("ADMIN_BRIDGE2_NUMBER", ADMIN_BRIDGE2_NUMBER)
        monkeypatch.setenv("ESCORT_TRUSTED_SOURCES", "bridge1,meta")  # bridge2 deliberately absent
        from app.config import get_settings
        get_settings.cache_clear()
        from modules.escort import handle_group_escort_order

        msg, extra = await handle_group_escort_order(
            CANONICAL_ORDER_TEXT, ADMIN_BRIDGE2_NUMBER, "bridge2", GROUP_JID, message_id="MSG3",
        )
        assert msg == ""
        assert extra is None

    async def test_malformed_incomplete_text_creates_no_row(self, test_db_pool, monkeypatch):
        _use_pool(test_db_pool)
        _authorize(monkeypatch)
        from modules.escort import handle_group_escort_order

        msg, extra = await handle_group_escort_order(
            "just chatting, nothing order-shaped here", ADMIN_BRIDGE2_NUMBER, "bridge2", GROUP_JID,
            message_id="MSG4",
        )
        assert msg == ""
        assert extra is None


# ===========================================================================
# 3. Cross-channel same-vessel dedup (Rule 8) -- group order vs existing DM draft
# ===========================================================================

class TestCrossChannelDedup:

    async def test_group_order_dedups_against_existing_dm_draft(self, test_db_pool, monkeypatch):
        """Same real-world order (identical master_mobile + mother_vessel +
        date) arriving first via a client DM, then via the admin's Trusted
        Group post -- must resolve to exactly ONE draft row, not two, even
        though the two channels have entirely different sender identities
        (client contact_id vs admin's own number)."""
        _use_pool(test_db_pool)
        _authorize(monkeypatch)
        from modules.escort import save_escort_programs, parse_escort_message, handle_group_escort_order

        order = parse_escort_message(CANONICAL_ORDER_TEXT)
        dm_ids = await save_escort_programs(order, "01788888888", "bridge1", is_verified=True)
        assert len(dm_ids) == 1

        msg, _ = await handle_group_escort_order(
            CANONICAL_ORDER_TEXT, ADMIN_BRIDGE2_NUMBER, "bridge2", GROUP_JID, message_id="MSG5",
        )
        assert f"program_id={dm_ids[0]}" in msg

        async with test_db_pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT program_id FROM wbom_escort_programs WHERE master_mobile = $1", "01711000111"
            )
        assert len(rows) == 1
        assert rows[0]["program_id"] == dm_ids[0]

    async def test_group_to_group_same_order_still_dedups_normally(self, test_db_pool, monkeypatch):
        """Baseline: the SAME group-sourced order posted twice must still
        dedup (via save_escort_programs()'s own pre-existing inline check,
        since sender/contact_id is identical both times) -- the new
        cross-channel helper is additive, not a replacement."""
        _use_pool(test_db_pool)
        _authorize(monkeypatch)
        from modules.escort import handle_group_escort_order

        await handle_group_escort_order(
            CANONICAL_ORDER_TEXT, ADMIN_BRIDGE2_NUMBER, "bridge2", GROUP_JID, message_id="MSG6",
        )
        await handle_group_escort_order(
            CANONICAL_ORDER_TEXT, ADMIN_BRIDGE2_NUMBER, "bridge2", GROUP_JID, message_id="MSG7",
        )
        async with test_db_pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT program_id FROM wbom_escort_programs WHERE master_mobile = $1", "01711000111"
            )
        assert len(rows) == 1


# ===========================================================================
# 4. DB-level confirmation idempotency / no-op guard (Rule 5)
# ===========================================================================

class TestConfirmationNoOpIdempotency:

    async def test_identical_confirmation_is_noop_then_real_change_still_applies(self, test_db_pool):
        """First call: fields identical to the current row -> must be a
        no-op (destination NOT overwritten, status NOT advanced). Second
        call with a genuinely different escort_name: must proceed as a
        real UPDATE. This is exactly the cross-channel scenario in Rule 5/8
        ('same confirmation sent to DM AND group -> first updates, second
        is a no-op') collapsed to its DB-visible effect, since
        _update_program_confirmed() is the single function both channels'
        confirmation path (handle_admin_escort_completion) already shares."""
        _use_pool(test_db_pool)
        from modules.escort import _update_program_confirmed

        async with test_db_pool.acquire() as conn:
            prog = await conn.fetchrow(
                """INSERT INTO wbom_escort_programs
                      (mother_vessel, lighter_vessel, master_mobile, status,
                       escort_name, escort_mobile, destination)
                   VALUES ('MV NOOP TEST','LV NOOP TEST','01700000000','draft','Karim','01711111111','OldDest')
                   RETURNING *"""
            )
        pid = prog["program_id"]

        await _update_program_confirmed(
            pid, "Karim", "01711111111",
            lighter_vessel="LV NOOP TEST", mother_vessel="MV NOOP TEST",
            destination="NewDest",
        )
        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM wbom_escort_programs WHERE program_id=$1", pid)
        assert row["destination"] == "OldDest"
        assert row["status"] == "draft"

        await _update_program_confirmed(
            pid, "Different Escort", "01711111111",
            lighter_vessel="LV NOOP TEST", mother_vessel="MV NOOP TEST",
            destination="NewDest",
        )
        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM wbom_escort_programs WHERE program_id=$1", pid)
        assert row["destination"] == "NewDest"
        assert row["status"] == "confirmed"
        assert row["escort_name"] == "Different Escort"

    async def test_wrong_escort_phone_does_not_match_as_noop(self, test_db_pool):
        """A DIFFERENT escort's confirmation for the same vessel/lighter
        must never be silently treated as a no-op of someone else's
        confirmation -- it must apply as a real, distinct update."""
        _use_pool(test_db_pool)
        from modules.escort import _update_program_confirmed

        async with test_db_pool.acquire() as conn:
            prog = await conn.fetchrow(
                """INSERT INTO wbom_escort_programs
                      (mother_vessel, lighter_vessel, master_mobile, status,
                       escort_name, escort_mobile)
                   VALUES ('MV WRONGPHONE','LV WRONGPHONE','01700000001','draft','Karim','01711111111')
                   RETURNING *"""
            )
        pid = prog["program_id"]

        await _update_program_confirmed(
            pid, "Karim", "01799999999",  # different mobile -- not the same escort's confirmation
            lighter_vessel="LV WRONGPHONE", mother_vessel="MV WRONGPHONE",
        )
        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM wbom_escort_programs WHERE program_id=$1", pid)
        assert row["escort_mobile"] == "01799999999"
        assert row["status"] == "confirmed"


# ===========================================================================
# 5. TTL exemption for group-sourced drafts (Owner Decision B)
# ===========================================================================

class TestTTLExemption:

    async def test_group_sourced_draft_survives_expiry(self, test_db_pool):
        _use_pool(test_db_pool)
        from modules.escort_roster.db import expire_stale_drafts
        prog = await _insert_group_draft(test_db_pool, group_sourced=True, hours_old=48, mv="MV TTL SURVIVES")
        await expire_stale_drafts(hours=24, actor="test")
        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM wbom_escort_programs WHERE program_id=$1", prog["program_id"])
        assert row is not None

    async def test_non_group_sourced_draft_still_expires_normally(self, test_db_pool):
        """Regression guard: the new NOT EXISTS clause must not accidentally
        preserve ordinary (non-group) drafts past TTL."""
        _use_pool(test_db_pool)
        from modules.escort_roster.db import expire_stale_drafts
        prog = await _insert_group_draft(test_db_pool, group_sourced=False, hours_old=48, mv="MV TTL EXPIRES")
        await expire_stale_drafts(hours=24, actor="test")
        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM wbom_escort_programs WHERE program_id=$1", prog["program_id"])
        assert row is None

    async def test_group_sourced_draft_with_null_remarks_still_expires(self, test_db_pool):
        """A draft with no remarks at all (NULL, e.g. legacy/pre-tagging
        row) must not be accidentally swept into the exemption -- the
        regex match against a NULL remarks column is false, not an error,
        so this must expire exactly like any other ordinary draft."""
        _use_pool(test_db_pool)
        from modules.escort_roster.db import expire_stale_drafts
        created_at = datetime.now(timezone.utc) - timedelta(hours=48)
        async with test_db_pool.acquire() as conn:
            prog = await conn.fetchrow(
                """INSERT INTO wbom_escort_programs
                      (mother_vessel, lighter_vessel, master_mobile, program_date, shift, status,
                       escort_name, escort_mobile)
                   VALUES ('MV TTL NULLREMARKS','LT TTL TEST','01799999999',$1,'D','draft','','')
                   RETURNING *""",
                date(2026, 6, 1),
            )
            await conn.execute(
                """INSERT INTO escort_roster_entries
                      (program_id, mother_vessel, lighter_vessel, master_mobile, escort_name, escort_mobile,
                       start_date, start_shift, roster_status, created_at)
                   VALUES ($1,'MV TTL NULLREMARKS','LT TTL TEST','01799999999','','',$2,'D','draft',$3)""",
                prog["program_id"], date(2026, 6, 1), created_at,
            )
        await expire_stale_drafts(hours=24, actor="test")
        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM wbom_escort_programs WHERE program_id=$1", prog["program_id"])
        assert row is None


# ===========================================================================
# 6. New-Order-vs-Confirmation text-level split (premise for the loop wiring)
# ===========================================================================

class TestNewOrderVsConfirmationSplit:

    def test_canonical_order_text_is_order_complete_not_completed_draft(self):
        from modules.escort import is_order_complete, is_completed_escort_draft
        assert is_order_complete(CANONICAL_ORDER_TEXT) is True
        assert is_completed_escort_draft(CANONICAL_ORDER_TEXT) is False

    def test_completed_slip_text_is_completed_draft_not_bare_order(self):
        from modules.escort import is_order_complete, is_completed_escort_draft
        assert is_completed_escort_draft(COMPLETED_SLIP_TEXT) is True


# ===========================================================================
# 7. Structural proof the outgoing-scan loop is actually wired (source
#    inspection, same convention as
#    test_bridge_poller_system_notification_guard.py's guard-wiring tests --
#    the loop itself needs a live bridge SQLite session to drive directly).
# ===========================================================================

class TestOutgoingScanLoopWiring:

    def test_group_jid_only_passed_for_bridge2(self):
        import inspect
        import modules.bridge_poller as bp
        source = inspect.getsource(bp)
        assert '_settings.escort_order_group_jid if bridge_name == "bridge2" else ""' in source

    def test_is_trusted_group_msg_requires_bridge2_and_exact_jid_match(self):
        import inspect
        import modules.bridge_poller as bp
        source = inspect.getsource(bp)
        idx = source.index("is_trusted_group_msg = bool(")
        block = source[idx:idx + 200]
        assert "trusted_group_jid" in block
        assert 'bridge_name == "bridge2"' in block
        assert "chat_jid == trusted_group_jid" in block

    def test_completed_draft_branch_precedes_group_order_branch(self):
        """A message satisfying is_completed_escort_draft() must always be
        routed to the existing handle_admin_escort_completion() -- the new
        group-order branch must be structurally reachable only afterwards
        (via `continue` at the end of the completed-draft branch)."""
        import inspect
        import modules.bridge_poller as bp
        source = inspect.getsource(bp)
        completed_branch = source.index("if is_completed_escort_draft(otext):")
        group_branch = source.index("if is_trusted_group_msg and is_order_complete(otext):")
        assert completed_branch < group_branch

    def test_recipient_phone_empty_string_only_for_trusted_group_branch(self):
        import inspect
        import modules.bridge_poller as bp
        source = inspect.getsource(bp)
        assert 'recipient_phone = "" if is_trusted_group_msg else re.sub(r"\\D", "", chat_jid or "")' in source

    def test_handle_group_escort_order_call_gated_on_is_trusted_group_msg(self):
        import inspect
        import modules.bridge_poller as bp
        source = inspect.getsource(bp)
        idx = source.index("if is_trusted_group_msg and is_order_complete(otext):")
        call_idx = source.index("await handle_group_escort_order(", idx)
        assert idx < call_idx < idx + 400
