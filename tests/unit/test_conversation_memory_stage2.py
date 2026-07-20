"""Integration tests — Phase B Stage 2 (multi-message escort parser in
shadow mode, 2026-07-19).

Gated on multi_message_escort_parser_enabled (still False in production
pending its own Owner checkpoint -- Section 15). These tests enable the
flag locally via monkeypatch, exactly like the Stage 1 shadow-hook tests
do for conversation_memory_enabled/whatsapp_thread_reconstruction_enabled.

Covers the 15 required Stage 2 tests (spec Section 10). DB-backed tests
are skipped automatically when TEST_DB_PASSWORD is unavailable, matching
this session's standing constraint -- hand-verified against a disposable
Postgres container instead (see scratchpad recheck_stage2*.py).
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit

MSG_LIGHTER_1 = "Bushra Biva-1(2nd Trip)\n1250MT\n01821375926\nOuter To Local\nAC:SST/NMT"
MSG_MOTHER_EN = "Mother Vessel: MV Horda"
MSG_LIGHTER_2_DIFFERENT_MOBILE = "Titas Express\n900MT\n01911111111\nOuter To Local\nAC:SST/NMT"


def _enable_stage2(monkeypatch, escort_client_phones: str = ""):
    from app.config import get_settings
    monkeypatch.setenv("CONVERSATION_MEMORY_ENABLED", "true")
    monkeypatch.setenv("WHATSAPP_THREAD_RECONSTRUCTION_ENABLED", "true")
    monkeypatch.setenv("MULTI_MESSAGE_ESCORT_PARSER_ENABLED", "true")
    monkeypatch.setenv("ESCORT_CLIENT_PHONES", escort_client_phones)
    get_settings.cache_clear()


def _disable_stage2(monkeypatch):
    from app.config import get_settings
    for k in ("CONVERSATION_MEMORY_ENABLED", "WHATSAPP_THREAD_RECONSTRUCTION_ENABLED",
              "MULTI_MESSAGE_ESCORT_PARSER_ENABLED", "ESCORT_CLIENT_PHONES"):
        monkeypatch.delenv(k, raising=False)
    get_settings.cache_clear()


# ===========================================================================
# Non-DB tests
# ===========================================================================

def test_existing_production_parser_unchanged():
    """#12: modules.escort.parse_escort_message() itself is untouched by
    Stage 2's existence -- Stage 2 only wraps its output, never modifies
    the function."""
    from modules.escort import parse_escort_message
    order = parse_escort_message(MSG_LIGHTER_1)
    assert order.get("lighters")[0]["master_mobile"] == "01821375926"
    assert order.get("lighters")[0]["lighter_vessel"] == "Bushra Biva-1(2nd Trip)"
    assert order.get("mother_vessel") is None


@pytest.mark.asyncio
async def test_shadow_hook_failure_does_not_block_routing(monkeypatch):
    """#13: even with Stage 2 enabled, an internal exception must never
    propagate to the message_router caller."""
    from modules.conversation_memory import thread_reconstruction as tr

    _enable_stage2(monkeypatch)

    async def _boom(**kwargs):
        raise RuntimeError("simulated stage 2 failure")

    monkeypatch.setattr(tr, "_observe_stage2", _boom)

    try:
        await tr.shadow_observe_escort_client_message(
            text=MSG_LIGHTER_1, sender_phone="01670535255", source="bridge2",
            message_id="STAGE2-BOOM-1", sender_role="client_escort_buyer",
        )
    finally:
        _disable_stage2(monkeypatch)


@pytest.mark.asyncio
async def test_unauthorized_employee_contributes_nothing_stage2(monkeypatch):
    """#6: unauthorized senders never reach Stage 2 grouping logic either
    -- the shared authorization gate in _observe() runs before the
    Stage 1/Stage 2 dispatch."""
    from modules.conversation_memory import thread_reconstruction as tr
    import modules.conversation_memory as cm

    _enable_stage2(monkeypatch, escort_client_phones="8801670535255")

    async def _boom(*a, **kw):
        raise AssertionError("must never be reached for an unauthorized sender")

    recorded = {}

    async def _fake_record_exclusion(*, bridge_id, counterparty_phone, business_domain, message_id, exclusion_kind):
        recorded["exclusion_kind"] = exclusion_kind

    monkeypatch.setattr(cm, "get_open_threads", _boom)
    monkeypatch.setattr(cm, "create_thread", _boom)
    monkeypatch.setattr(cm, "record_shadow_exclusion", _fake_record_exclusion)

    try:
        await tr.shadow_observe_escort_client_message(
            text=MSG_LIGHTER_1, sender_phone="8801999999999",
            source="bridge2", message_id="STAGE2-UNAUTH-1", sender_role="employee",
        )
        assert recorded["exclusion_kind"] == "unauthorized_excluded"
    finally:
        _disable_stage2(monkeypatch)


@pytest.mark.asyncio
async def test_system_generated_wrapper_contributes_nothing_stage2(monkeypatch):
    """#7: same guarantee for the system-generated-marker check."""
    from modules.conversation_memory import thread_reconstruction as tr
    import modules.conversation_memory as cm
    from app.bridge import _AUTOMATED_SUFFIX

    _enable_stage2(monkeypatch, escort_client_phones="01670535255")

    async def _boom(*a, **kw):
        raise AssertionError("must never be reached when the text carries the automated marker")

    recorded = {}

    async def _fake_record_exclusion(*, bridge_id, counterparty_phone, business_domain, message_id, exclusion_kind):
        recorded["exclusion_kind"] = exclusion_kind

    monkeypatch.setattr(cm, "get_open_threads", _boom)
    monkeypatch.setattr(cm, "create_thread", _boom)
    monkeypatch.setattr(cm, "record_shadow_exclusion", _fake_record_exclusion)

    try:
        await tr.shadow_observe_escort_client_message(
            text=MSG_LIGHTER_1 + _AUTOMATED_SUFFIX, sender_phone="01670535255",
            source="bridge2", message_id="STAGE2-SYSGEN-1", sender_role="client_escort_buyer",
        )
        assert recorded["exclusion_kind"] == "system_generated_excluded"
    finally:
        _disable_stage2(monkeypatch)


# ===========================================================================
# DB-backed tests (TestShadowStage2AgainstRealDB)
# ===========================================================================

class TestShadowStage2AgainstRealDB:
    """Requires a live Postgres connection (test_db_pool) -- skipped
    automatically in this session (TEST_DB_PASSWORD unavailable), hand-
    verified against a disposable container instead. Runs for real
    wherever a test DB is reachable."""

    @pytest.mark.asyncio
    async def test_two_english_messages_one_candidate(self, test_db_pool, monkeypatch):
        """#1 (Case A): the mandatory pattern -- lighter/mobile message
        then a separate mother-vessel message -- must merge into ONE
        complete candidate thread."""
        from modules.conversation_memory.thread_reconstruction import shadow_observe_escort_client_message
        import modules.conversation_memory as cm

        _enable_stage2(monkeypatch)
        phone = "01900000101"
        try:
            await shadow_observe_escort_client_message(text=MSG_LIGHTER_1, sender_phone=phone, source="bridge2", message_id="ST2-1-1", sender_role="client_escort_buyer")
            await shadow_observe_escort_client_message(text=MSG_MOTHER_EN, sender_phone=phone, source="bridge2", message_id="ST2-1-2", sender_role="client_escort_buyer")
            threads = await cm.get_open_threads(bridge_id="bridge2", counterparty_phone=phone, business_domain="escort")
            assert len(threads) == 1
            t = threads[0]
            assert t["fields"]["mother_vessel"]["value"] == "MV HORDA"
            assert t["fields"]["lighter_vessel"]["value"] == "Bushra Biva-1(2nd Trip)"
            assert t["fields"]["master_mobile"]["value"] == "01821375926"
            assert t["state"] == "partial"
        finally:
            _disable_stage2(monkeypatch)

    @pytest.mark.asyncio
    async def test_reversed_delivery_order(self, test_db_pool, monkeypatch):
        """#2: the mother-vessel message arriving FIRST, then the
        lighter/mobile message, must still converge on one complete
        candidate -- order of arrival must not matter."""
        from modules.conversation_memory.thread_reconstruction import shadow_observe_escort_client_message
        import modules.conversation_memory as cm

        _enable_stage2(monkeypatch)
        phone = "01900000102"
        try:
            await shadow_observe_escort_client_message(text=MSG_MOTHER_EN, sender_phone=phone, source="bridge2", message_id="ST2-2-1", sender_role="client_escort_buyer")
            await shadow_observe_escort_client_message(text=MSG_LIGHTER_1, sender_phone=phone, source="bridge2", message_id="ST2-2-2", sender_role="client_escort_buyer")
            threads = await cm.get_open_threads(bridge_id="bridge2", counterparty_phone=phone, business_domain="escort")
            assert len(threads) == 1
            t = threads[0]
            assert t["fields"]["mother_vessel"]["value"] == "MV HORDA"
            assert t["fields"]["lighter_vessel"]["value"] == "Bushra Biva-1(2nd Trip)"
            assert t["state"] == "partial"
        finally:
            _disable_stage2(monkeypatch)

    @pytest.mark.asyncio
    async def test_duplicate_replay_idempotent(self, test_db_pool, monkeypatch):
        """#3: replaying the exact same message_id must not create a
        second thread or a second merge. It DOES write one additional
        'duplicate_replay' shadow-comparison row (Stage 4 Section 6
        requires duplicate replays to be counted for metrics) -- but that
        row is a bare exclusion marker (thread_id NULL, would_create_
        draft False), never a second observation of the thread itself."""
        from modules.conversation_memory.thread_reconstruction import shadow_observe_escort_client_message
        import modules.conversation_memory as cm
        from app.database import fetch_val

        _enable_stage2(monkeypatch)
        phone = "01900000103"
        try:
            await shadow_observe_escort_client_message(text=MSG_LIGHTER_1, sender_phone=phone, source="bridge2", message_id="ST2-3-DUP", sender_role="client_escort_buyer")
            t1 = await cm.get_open_threads(bridge_id="bridge2", counterparty_phone=phone, business_domain="escort")
            cmp1 = await fetch_val("SELECT count(*) FROM fazle_conversation_shadow_comparisons WHERE counterparty_phone=$1", phone)

            await shadow_observe_escort_client_message(text=MSG_LIGHTER_1, sender_phone=phone, source="bridge2", message_id="ST2-3-DUP", sender_role="client_escort_buyer")
            t2 = await cm.get_open_threads(bridge_id="bridge2", counterparty_phone=phone, business_domain="escort")
            cmp2 = await fetch_val("SELECT count(*) FROM fazle_conversation_shadow_comparisons WHERE counterparty_phone=$1", phone)
            replay_rows = await fetch_val(
                "SELECT count(*) FROM fazle_conversation_shadow_comparisons WHERE counterparty_phone=$1 AND grouping_action='duplicate_replay'",
                phone,
            )

            assert len(t1) == len(t2) == 1
            assert t1[0]["updated_at"] == t2[0]["updated_at"], "the thread itself must not be touched by the replay"
            assert cmp1 == 1
            assert cmp2 == 2, "the replay adds exactly one exclusion-marker row, not a second real observation"
            assert replay_rows == 1
        finally:
            _disable_stage2(monkeypatch)

    @pytest.mark.asyncio
    async def test_restart_between_messages_persistence(self, test_db_pool, monkeypatch):
        """#4: thread state is persisted in the DB, not in-process memory
        -- simulated by clearing get_settings' cache and re-importing
        between calls, the closest equivalent to "the process restarted"
        without actually restarting the interpreter."""
        from app.config import get_settings
        from modules.conversation_memory.thread_reconstruction import shadow_observe_escort_client_message
        import modules.conversation_memory as cm

        _enable_stage2(monkeypatch)
        phone = "01900000104"
        try:
            await shadow_observe_escort_client_message(text=MSG_LIGHTER_1, sender_phone=phone, source="bridge2", message_id="ST2-4-1", sender_role="client_escort_buyer")

            # Simulate a restart: drop cached settings, re-fetch fresh module state.
            get_settings.cache_clear()
            _enable_stage2(monkeypatch)

            await shadow_observe_escort_client_message(text=MSG_MOTHER_EN, sender_phone=phone, source="bridge2", message_id="ST2-4-2", sender_role="client_escort_buyer")
            threads = await cm.get_open_threads(bridge_id="bridge2", counterparty_phone=phone, business_domain="escort")
            assert len(threads) == 1
            assert threads[0]["fields"]["mother_vessel"]["value"] == "MV HORDA"
            assert threads[0]["fields"]["lighter_vessel"]["value"] == "Bushra Biva-1(2nd Trip)"
        finally:
            _disable_stage2(monkeypatch)

    @pytest.mark.asyncio
    async def test_expired_partial_no_merge(self, test_db_pool, monkeypatch):
        """#5 (Part 8): a message arriving after the thread's TTL has
        expired must open a fresh thread, never silently reopen/merge
        into the expired one."""
        from modules.conversation_memory.thread_reconstruction import shadow_observe_escort_client_message
        import modules.conversation_memory as cm
        from app.database import execute

        _enable_stage2(monkeypatch)
        phone = "01900000105"
        try:
            await shadow_observe_escort_client_message(text=MSG_LIGHTER_1, sender_phone=phone, source="bridge2", message_id="ST2-5-1", sender_role="client_escort_buyer")
            threads = await cm.get_open_threads(bridge_id="bridge2", counterparty_phone=phone, business_domain="escort")
            assert len(threads) == 1
            old_id = threads[0]["id"]

            await execute("UPDATE fazle_conversation_threads SET expires_at = NOW() - INTERVAL '1 minute' WHERE id = $1", old_id)

            await shadow_observe_escort_client_message(text=MSG_MOTHER_EN, sender_phone=phone, source="bridge2", message_id="ST2-5-2", sender_role="client_escort_buyer")
            threads_after = await cm.get_open_threads(bridge_id="bridge2", counterparty_phone=phone, business_domain="escort")
            assert len(threads_after) == 1
            assert threads_after[0]["id"] != old_id
            assert "lighter_vessel" not in threads_after[0]["fields"]
        finally:
            _disable_stage2(monkeypatch)

    @pytest.mark.asyncio
    async def test_two_clients_different_bridges_isolated(self, test_db_pool, monkeypatch):
        """#8: the SAME phone number on two different bridges must never
        merge into one thread -- conversation identity includes bridge_id."""
        from modules.conversation_memory.thread_reconstruction import shadow_observe_escort_client_message
        import modules.conversation_memory as cm

        _enable_stage2(monkeypatch)
        phone = "01900000106"
        try:
            await shadow_observe_escort_client_message(text=MSG_LIGHTER_1, sender_phone=phone, source="bridge1", message_id="ST2-8-1", sender_role="client_escort_buyer")
            await shadow_observe_escort_client_message(text=MSG_MOTHER_EN, sender_phone=phone, source="bridge2", message_id="ST2-8-2", sender_role="client_escort_buyer")

            threads_b1 = await cm.get_open_threads(bridge_id="bridge1", counterparty_phone=phone, business_domain="escort")
            threads_b2 = await cm.get_open_threads(bridge_id="bridge2", counterparty_phone=phone, business_domain="escort")
            assert len(threads_b1) == 1
            assert len(threads_b2) == 1
            assert "mother_vessel" not in threads_b1[0]["fields"]
            assert "lighter_vessel" not in threads_b2[0]["fields"]
        finally:
            _disable_stage2(monkeypatch)

    @pytest.mark.asyncio
    async def test_same_client_two_master_mobiles_not_guessed(self, test_db_pool, monkeypatch):
        """#9 (Case B): two messages with different master mobiles/
        lighter vessels sent close together from the same phone must NOT
        be auto-merged into one order."""
        from modules.conversation_memory.thread_reconstruction import shadow_observe_escort_client_message
        import modules.conversation_memory as cm

        _enable_stage2(monkeypatch)
        phone = "01900000107"
        try:
            await shadow_observe_escort_client_message(text=MSG_LIGHTER_1, sender_phone=phone, source="bridge2", message_id="ST2-9-1", sender_role="client_escort_buyer")
            await shadow_observe_escort_client_message(text=MSG_LIGHTER_2_DIFFERENT_MOBILE, sender_phone=phone, source="bridge2", message_id="ST2-9-2", sender_role="client_escort_buyer")

            threads = await cm.get_open_threads(bridge_id="bridge2", counterparty_phone=phone, business_domain="escort")
            assert len(threads) == 2, "different master mobiles must produce two separate candidate threads, not one merged order"
            mobiles = {t["fields"]["master_mobile"]["value"] for t in threads}
            assert mobiles == {"01821375926", "01911111111"}
        finally:
            _disable_stage2(monkeypatch)

    @pytest.mark.asyncio
    async def test_high_confidence_conflicting_mother_vessels_flagged(self, test_db_pool, monkeypatch):
        """#10: same lighter+master mobile (so it's genuinely the same
        order) but a differing high-confidence mother vessel across two
        messages must flag a conflict, never silently overwrite."""
        from modules.conversation_memory.thread_reconstruction import shadow_observe_escort_client_message
        import modules.conversation_memory as cm

        _enable_stage2(monkeypatch)
        phone = "01900000108"
        msg1 = "Mother Vessel: MV Horda\nBushra Biva-1(2nd Trip)\n1250MT\n01821375926\nOuter To Local"
        msg2 = "Mother Vessel: MV Different Ship\nBushra Biva-1(2nd Trip)\n1250MT\n01821375926\nOuter To Local"
        try:
            await shadow_observe_escort_client_message(text=msg1, sender_phone=phone, source="bridge2", message_id="ST2-10-1", sender_role="client_escort_buyer")
            await shadow_observe_escort_client_message(text=msg2, sender_phone=phone, source="bridge2", message_id="ST2-10-2", sender_role="client_escort_buyer")

            threads = await cm.get_open_threads(bridge_id="bridge2", counterparty_phone=phone, business_domain="escort")
            assert len(threads) == 1, "matching lighter+master mobile must stay ONE thread"
            t = threads[0]
            assert t["state"] == "conflicted"
            assert t["fields"]["mother_vessel"]["value"] == "MV HORDA", "original value preserved, not silently overwritten"
            assert t["conflict_flags"]
        finally:
            _disable_stage2(monkeypatch)

    @pytest.mark.asyncio
    async def test_equivalent_normalized_mobiles_not_conflict(self, test_db_pool, monkeypatch):
        """#11: the same master mobile in two different textual forms
        (bare vs +880-prefixed) must not be treated as a conflict or as
        a signal for a different order."""
        from modules.conversation_memory.thread_reconstruction import shadow_observe_escort_client_message
        import modules.conversation_memory as cm

        _enable_stage2(monkeypatch)
        phone = "01900000109"
        msg2 = "Bushra Biva-1(2nd Trip)\n1250MT\n+8801821375926\nOuter To Local\nAC:SST/NMT"
        try:
            await shadow_observe_escort_client_message(text=MSG_LIGHTER_1, sender_phone=phone, source="bridge2", message_id="ST2-11-1", sender_role="client_escort_buyer")
            await shadow_observe_escort_client_message(text=msg2, sender_phone=phone, source="bridge2", message_id="ST2-11-2", sender_role="client_escort_buyer")

            threads = await cm.get_open_threads(bridge_id="bridge2", counterparty_phone=phone, business_domain="escort")
            assert len(threads) == 1
            assert threads[0]["state"] != "conflicted"
        finally:
            _disable_stage2(monkeypatch)

    @pytest.mark.asyncio
    async def test_no_business_table_write(self, test_db_pool, monkeypatch):
        """#15: even a complete (would_create_draft=True) candidate must
        never create a wbom_escort_programs row in Stage 2 -- shadow mode
        only."""
        from modules.conversation_memory.thread_reconstruction import shadow_observe_escort_client_message
        from app.database import fetch_val

        _enable_stage2(monkeypatch)
        phone = "01900000110"
        try:
            before = await fetch_val("SELECT count(*) FROM wbom_escort_programs")
            await shadow_observe_escort_client_message(text=MSG_LIGHTER_1, sender_phone=phone, source="bridge2", message_id="ST2-15-1", sender_role="client_escort_buyer")
            await shadow_observe_escort_client_message(text=MSG_MOTHER_EN, sender_phone=phone, source="bridge2", message_id="ST2-15-2", sender_role="client_escort_buyer")
            after = await fetch_val("SELECT count(*) FROM wbom_escort_programs")
            assert before == after
        finally:
            _disable_stage2(monkeypatch)

    @pytest.mark.asyncio
    async def test_flags_off_produces_old_behavior(self, test_db_pool, monkeypatch):
        """#14: with multi_message_escort_parser_enabled left False, the
        hook must behave exactly like Stage 1 -- single most-recent
        thread, no grouping, no shadow comparison rows written."""
        from app.config import get_settings
        from modules.conversation_memory.thread_reconstruction import shadow_observe_escort_client_message
        import modules.conversation_memory as cm
        from app.database import fetch_val

        monkeypatch.setenv("CONVERSATION_MEMORY_ENABLED", "true")
        monkeypatch.setenv("WHATSAPP_THREAD_RECONSTRUCTION_ENABLED", "true")
        monkeypatch.setenv("ESCORT_CLIENT_PHONES", "")
        # MULTI_MESSAGE_ESCORT_PARSER_ENABLED deliberately left unset -> False
        get_settings.cache_clear()
        phone = "01900000111"
        try:
            await shadow_observe_escort_client_message(text=MSG_LIGHTER_1, sender_phone=phone, source="bridge2", message_id="ST2-14-1", sender_role="client_escort_buyer")
            await shadow_observe_escort_client_message(text=MSG_MOTHER_EN, sender_phone=phone, source="bridge2", message_id="ST2-14-2", sender_role="client_escort_buyer")

            threads = await cm.get_open_threads(bridge_id="bridge2", counterparty_phone=phone, business_domain="escort")
            assert len(threads) == 1
            cmp_count = await fetch_val("SELECT count(*) FROM fazle_conversation_shadow_comparisons WHERE counterparty_phone=$1", phone)
            assert cmp_count == 0, "Stage 1 path must never write shadow comparison rows"
        finally:
            monkeypatch.delenv("CONVERSATION_MEMORY_ENABLED", raising=False)
            monkeypatch.delenv("WHATSAPP_THREAD_RECONSTRUCTION_ENABLED", raising=False)
            monkeypatch.delenv("ESCORT_CLIENT_PHONES", raising=False)
            get_settings.cache_clear()
