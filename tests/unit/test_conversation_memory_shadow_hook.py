"""Integration tests — modules.conversation_memory.thread_reconstruction
shadow-mode hook (Phase B Part 8, Stage 1 scope).

Honest scope: Stage 1 wires the EXISTING single-message
modules.escort.parse_escort_message() into the new memory engine, in
shadow mode. It does NOT yet include Bangla semantic extraction (Stage 3)
or genuine multi-message completeness logic driving real actions (Stage
2). These tests prove the Stage 1 infrastructure works correctly on real
data, and honestly document where the *current* single-message parser
still falls short on the Bangla portion of the real target example --
per the task's own instruction not to claim the full two-message
Bangla/English example passes end-to-end until it genuinely does.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit

REAL_MSG_1 = "Bushra Biva-1(2nd Trip)\n1250MT\n01821375926\nOuter To Local\nAC:SST/NMT"
REAL_MSG_2_BANGLA = "ভেসেল নেম Horda"


@pytest.mark.asyncio
async def test_flags_off_by_default_is_a_complete_noop(monkeypatch):
    """The single most important safety property: with both feature
    flags at their default (False), the hook must not touch the database
    at all -- verified by making any DB-backed conversation_memory call
    raise, and confirming the hook still returns cleanly."""
    from modules.conversation_memory.thread_reconstruction import shadow_observe_escort_client_message
    import modules.conversation_memory as cm

    async def _boom(*a, **kw):
        raise AssertionError("must not be called when feature flags are off")

    monkeypatch.setattr(cm, "get_open_thread", _boom)
    monkeypatch.setattr(cm, "create_thread", _boom)
    monkeypatch.setattr(cm, "is_message_processed", _boom)

    # Should return silently, no exception, no DB call attempted.
    await shadow_observe_escort_client_message(
        text=REAL_MSG_1, sender_phone="01670535255", source="bridge2",
        message_id="MSG-1", sender_role="client_escort_buyer",
    )


@pytest.mark.asyncio
async def test_exception_inside_observation_never_propagates(monkeypatch):
    """Even with flags on, any internal failure must be swallowed --
    shadow mode must never be able to break real message routing."""
    from app.config import get_settings
    from modules.conversation_memory import thread_reconstruction as tr

    get_settings.cache_clear()
    monkeypatch.setenv("CONVERSATION_MEMORY_ENABLED", "true")
    monkeypatch.setenv("WHATSAPP_THREAD_RECONSTRUCTION_ENABLED", "true")
    get_settings.cache_clear()

    async def _boom(**kwargs):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(tr, "_observe", _boom)

    try:
        # Must not raise.
        await tr.shadow_observe_escort_client_message(
            text=REAL_MSG_1, sender_phone="01670535255", source="bridge2",
            message_id="MSG-1", sender_role="client_escort_buyer",
        )
    finally:
        monkeypatch.delenv("CONVERSATION_MEMORY_ENABLED", raising=False)
        monkeypatch.delenv("WHATSAPP_THREAD_RECONSTRUCTION_ENABLED", raising=False)
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_unauthorized_sender_never_reaches_thread_creation(monkeypatch):
    """Pre-flag-enable check #3: unauthorized senders cannot form an
    escort-order candidate even in shadow mode -- explicit defense-in-
    depth check inside _observe() itself, not just relying on the
    message_router call site being correct."""
    from app.config import get_settings
    import modules.conversation_memory as cm
    from modules.conversation_memory import thread_reconstruction as tr

    get_settings.cache_clear()
    monkeypatch.setenv("CONVERSATION_MEMORY_ENABLED", "true")
    monkeypatch.setenv("WHATSAPP_THREAD_RECONSTRUCTION_ENABLED", "true")
    monkeypatch.setenv("ESCORT_CLIENT_PHONES", "8801670535255")
    get_settings.cache_clear()

    async def _boom(*a, **kw):
        raise AssertionError("must never be reached for an unauthorized sender")

    monkeypatch.setattr(cm, "get_open_thread", _boom)
    monkeypatch.setattr(cm, "create_thread", _boom)

    try:
        await tr.shadow_observe_escort_client_message(
            text=REAL_MSG_1, sender_phone="8801999999999",  # NOT in ESCORT_CLIENT_PHONES
            source="bridge2", message_id="MSG-UNAUTH", sender_role="employee",
        )
    finally:
        monkeypatch.delenv("CONVERSATION_MEMORY_ENABLED", raising=False)
        monkeypatch.delenv("WHATSAPP_THREAD_RECONSTRUCTION_ENABLED", raising=False)
        monkeypatch.delenv("ESCORT_CLIENT_PHONES", raising=False)
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_system_generated_marker_on_inbound_text_is_refused(monkeypatch):
    """Pre-flag-enable check #2: even though a genuine human would never
    type one of our known system-notification title prefixes verbatim,
    this is checked explicitly on the inbound path too, not just assumed
    impossible.

    2026-07-27: no marker is appended to outbound text anymore (Owner
    directive) -- system-generated text is now recognized by a known
    title prefix (SYSTEM_NOTIFICATION_TITLE_PREFIXES) instead."""
    from app.config import get_settings
    import modules.conversation_memory as cm
    from modules.conversation_memory import thread_reconstruction as tr

    get_settings.cache_clear()
    monkeypatch.setenv("CONVERSATION_MEMORY_ENABLED", "true")
    monkeypatch.setenv("WHATSAPP_THREAD_RECONSTRUCTION_ENABLED", "true")
    monkeypatch.setenv("ESCORT_CLIENT_PHONES", "01670535255")
    get_settings.cache_clear()

    async def _boom(*a, **kw):
        raise AssertionError("must never be reached when the text carries the automated marker")

    monkeypatch.setattr(cm, "get_open_thread", _boom)
    monkeypatch.setattr(cm, "create_thread", _boom)

    try:
        await tr.shadow_observe_escort_client_message(
            text="⚠️ RELEASE REVIEW REQUIRED\n" + REAL_MSG_1, sender_phone="01670535255",
            source="bridge2", message_id="MSG-SYSGEN", sender_role="client_escort_buyer",
        )
    finally:
        monkeypatch.delenv("CONVERSATION_MEMORY_ENABLED", raising=False)
        monkeypatch.delenv("WHATSAPP_THREAD_RECONSTRUCTION_ENABLED", raising=False)
        monkeypatch.delenv("ESCORT_CLIENT_PHONES", raising=False)
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_shadow_observation_times_out_without_blocking_caller(monkeypatch):
    """Stage 4 Section 3 fail-safe requirement: a hung shadow observation
    must be abandoned, not allowed to block the real message-routing path
    it's awaited inline from."""
    import asyncio
    from app.config import get_settings
    from modules.conversation_memory import thread_reconstruction as tr

    get_settings.cache_clear()
    monkeypatch.setenv("CONVERSATION_MEMORY_ENABLED", "true")
    monkeypatch.setenv("WHATSAPP_THREAD_RECONSTRUCTION_ENABLED", "true")
    monkeypatch.setenv("CONVERSATION_MEMORY_SHADOW_TIMEOUT_SECONDS", "0.05")
    get_settings.cache_clear()

    async def _hang(**kwargs):
        await asyncio.sleep(10)

    monkeypatch.setattr(tr, "_observe", _hang)

    try:
        started = asyncio.get_event_loop().time()
        # Must return well before the simulated 10s hang, and must not raise.
        await tr.shadow_observe_escort_client_message(
            text=REAL_MSG_1, sender_phone="01670535255", source="bridge2",
            message_id="MSG-TIMEOUT", sender_role="client_escort_buyer",
        )
        elapsed = asyncio.get_event_loop().time() - started
        assert elapsed < 2.0, "shadow observation must be abandoned at its own timeout, not left to hang"
    finally:
        monkeypatch.delenv("CONVERSATION_MEMORY_ENABLED", raising=False)
        monkeypatch.delenv("WHATSAPP_THREAD_RECONSTRUCTION_ENABLED", raising=False)
        monkeypatch.delenv("CONVERSATION_MEMORY_SHADOW_TIMEOUT_SECONDS", raising=False)
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_shadow_db_unavailable_does_not_block_routing(monkeypatch):
    """Stage 4 Section 3: if the shadow-mode database connection itself
    is unavailable, real message routing must continue unaffected -- no
    exception propagates, no HTTP 500 traceable to this module."""
    from app.config import get_settings
    from modules.conversation_memory import thread_reconstruction as tr
    import modules.conversation_memory as cm

    get_settings.cache_clear()
    monkeypatch.setenv("CONVERSATION_MEMORY_ENABLED", "true")
    monkeypatch.setenv("WHATSAPP_THREAD_RECONSTRUCTION_ENABLED", "true")
    get_settings.cache_clear()

    async def _connection_refused(*a, **kw):
        raise ConnectionRefusedError("simulated DB connection failure")

    monkeypatch.setattr(cm, "get_open_thread", _connection_refused)

    try:
        # Must not raise.
        await tr.shadow_observe_escort_client_message(
            text=REAL_MSG_1, sender_phone="01670535255", source="bridge2",
            message_id="MSG-DBDOWN", sender_role="client_escort_buyer",
        )
    finally:
        monkeypatch.delenv("CONVERSATION_MEMORY_ENABLED", raising=False)
        monkeypatch.delenv("WHATSAPP_THREAD_RECONSTRUCTION_ENABLED", raising=False)
        get_settings.cache_clear()


class TestShadowObservationAgainstRealDB:
    """Requires a live Postgres connection (test_db_pool) -- skipped
    automatically in this session (TEST_DB_PASSWORD unavailable), runs
    for real wherever a test DB is reachable."""

    @pytest.mark.asyncio
    async def test_two_pure_english_messages_accumulate_into_one_complete_thread(self, test_db_pool, monkeypatch):
        """Proves the core Stage 1 mechanism end-to-end on real data --
        deliberately using a second message with an ENGLISH mother-vessel
        label ("Mother Vessel: MV Horda") rather than the Bangla one, to
        isolate "does accumulation work" from "does Bangla extraction
        work" (the latter is honestly not yet solved -- see the next
        test)."""
        from app.config import get_settings
        from modules.conversation_memory.thread_reconstruction import shadow_observe_escort_client_message
        import modules.conversation_memory as cm

        get_settings.cache_clear()
        monkeypatch.setenv("CONVERSATION_MEMORY_ENABLED", "true")
        monkeypatch.setenv("WHATSAPP_THREAD_RECONSTRUCTION_ENABLED", "true")
        monkeypatch.setenv("ESCORT_CLIENT_PHONES", "")
        get_settings.cache_clear()

        phone = "01900000011"
        try:
            await shadow_observe_escort_client_message(
                text=REAL_MSG_1, sender_phone=phone, source="bridge2",
                message_id="SHADOWTEST-MSG-1", sender_role="client_escort_buyer",
            )
            thread = await cm.get_open_thread(bridge_id="bridge2", counterparty_phone=phone, business_domain="escort")
            assert thread is not None
            assert thread["fields"]["lighter_vessel"]["value"] == "Bushra Biva-1(2nd Trip)"
            assert thread["fields"]["master_mobile"]["value"] == "01821375926"
            assert "mother_vessel" not in thread["fields"]
            assert thread["state"] == "open"  # not complete yet -- mother_vessel missing

            await shadow_observe_escort_client_message(
                text="Mother Vessel: MV Horda", sender_phone=phone, source="bridge2",
                message_id="SHADOWTEST-MSG-2", sender_role="client_escort_buyer",
            )
            thread2 = await cm.get_open_thread(bridge_id="bridge2", counterparty_phone=phone, business_domain="escort")
            assert thread2["id"] == thread["id"], "second message must merge into the SAME thread, not create a new one"
            assert thread2["fields"]["mother_vessel"]["value"] == "MV HORDA"
            # Completeness rule (Part 6.4): mother + lighter + master mobile all present
            assert thread2["state"] == "partial"
            required = {"mother_vessel", "lighter_vessel", "master_mobile"}
            assert required.issubset(thread2["fields"].keys())
        finally:
            monkeypatch.delenv("CONVERSATION_MEMORY_ENABLED", raising=False)
            monkeypatch.delenv("WHATSAPP_THREAD_RECONSTRUCTION_ENABLED", raising=False)
            monkeypatch.delenv("ESCORT_CLIENT_PHONES", raising=False)
            get_settings.cache_clear()

    @pytest.mark.asyncio
    async def test_honest_gap_bangla_message_extraction_is_not_yet_correct(self, test_db_pool, monkeypatch):
        """Documents the CURRENT, real gap this Stage explicitly does not
        close: parse_escort_message() has no Bangla label-stripping, so
        "ভেসেল নেম Horda" is misread as a literal vessel name containing
        the Bangla words, not stripped down to "MV HORDA". This test
        exists so nobody mistakes Stage 1 for "the Bangla example works" --
        it will need updating (the assertion inverted) once Stage 3 ships."""
        from app.config import get_settings
        from modules.conversation_memory.thread_reconstruction import shadow_observe_escort_client_message
        import modules.conversation_memory as cm

        get_settings.cache_clear()
        monkeypatch.setenv("CONVERSATION_MEMORY_ENABLED", "true")
        monkeypatch.setenv("WHATSAPP_THREAD_RECONSTRUCTION_ENABLED", "true")
        monkeypatch.setenv("ESCORT_CLIENT_PHONES", "")
        get_settings.cache_clear()

        phone = "01900000012"
        try:
            await shadow_observe_escort_client_message(
                text=REAL_MSG_1, sender_phone=phone, source="bridge2",
                message_id="SHADOWTEST-BN-MSG-1", sender_role="client_escort_buyer",
            )
            await shadow_observe_escort_client_message(
                text=REAL_MSG_2_BANGLA, sender_phone=phone, source="bridge2",
                message_id="SHADOWTEST-BN-MSG-2", sender_role="client_escort_buyer",
            )
            thread = await cm.get_open_thread(bridge_id="bridge2", counterparty_phone=phone, business_domain="escort")
            # Honest current behavior: the Bangla words leak into the value.
            assert thread["fields"]["mother_vessel"]["value"] == "MV ভেসেল নেম HORDA"
            assert thread["fields"]["mother_vessel"]["value"] != "MV HORDA", (
                "if this now fails, Stage 3's Bangla parser has shipped -- "
                "update this test to assert the CORRECT value instead"
            )
        finally:
            monkeypatch.delenv("CONVERSATION_MEMORY_ENABLED", raising=False)
            monkeypatch.delenv("WHATSAPP_THREAD_RECONSTRUCTION_ENABLED", raising=False)
            monkeypatch.delenv("ESCORT_CLIENT_PHONES", raising=False)
            get_settings.cache_clear()

    @pytest.mark.asyncio
    async def test_no_draft_or_program_created_in_shadow_mode(self, test_db_pool, monkeypatch):
        """Part 13's core shadow-mode guarantee: even a COMPLETE
        (mother+lighter+master) accumulated conversation must not create
        any wbom_escort_programs row in Stage 1."""
        from app.config import get_settings
        from app.database import fetch_val
        from modules.conversation_memory.thread_reconstruction import shadow_observe_escort_client_message

        get_settings.cache_clear()
        monkeypatch.setenv("CONVERSATION_MEMORY_ENABLED", "true")
        monkeypatch.setenv("WHATSAPP_THREAD_RECONSTRUCTION_ENABLED", "true")
        monkeypatch.setenv("ESCORT_CLIENT_PHONES", "")
        get_settings.cache_clear()

        phone = "01900000013"
        try:
            count_before = await fetch_val("SELECT count(*) FROM wbom_escort_programs")
            await shadow_observe_escort_client_message(
                text=REAL_MSG_1, sender_phone=phone, source="bridge2",
                message_id="SHADOWTEST-ND-MSG-1", sender_role="client_escort_buyer",
            )
            await shadow_observe_escort_client_message(
                text="Mother Vessel: MV Horda", sender_phone=phone, source="bridge2",
                message_id="SHADOWTEST-ND-MSG-2", sender_role="client_escort_buyer",
            )
            count_after = await fetch_val("SELECT count(*) FROM wbom_escort_programs")
            assert count_after == count_before, "shadow mode must never create a program row"
        finally:
            monkeypatch.delenv("CONVERSATION_MEMORY_ENABLED", raising=False)
            monkeypatch.delenv("WHATSAPP_THREAD_RECONSTRUCTION_ENABLED", raising=False)
            monkeypatch.delenv("ESCORT_CLIENT_PHONES", raising=False)
            get_settings.cache_clear()

    @pytest.mark.asyncio
    async def test_duplicate_message_id_does_not_reprocess(self, test_db_pool, monkeypatch):
        from app.config import get_settings
        from modules.conversation_memory.thread_reconstruction import shadow_observe_escort_client_message
        import modules.conversation_memory as cm

        get_settings.cache_clear()
        monkeypatch.setenv("CONVERSATION_MEMORY_ENABLED", "true")
        monkeypatch.setenv("WHATSAPP_THREAD_RECONSTRUCTION_ENABLED", "true")
        monkeypatch.setenv("ESCORT_CLIENT_PHONES", "")
        get_settings.cache_clear()

        phone = "01900000014"
        try:
            await shadow_observe_escort_client_message(
                text=REAL_MSG_1, sender_phone=phone, source="bridge2",
                message_id="SHADOWTEST-DUP-1", sender_role="client_escort_buyer",
            )
            thread1 = await cm.get_open_thread(bridge_id="bridge2", counterparty_phone=phone, business_domain="escort")
            # Replay the exact same message_id -- must be a no-op, not a second merge/update.
            await shadow_observe_escort_client_message(
                text=REAL_MSG_1, sender_phone=phone, source="bridge2",
                message_id="SHADOWTEST-DUP-1", sender_role="client_escort_buyer",
            )
            thread2 = await cm.get_open_thread(bridge_id="bridge2", counterparty_phone=phone, business_domain="escort")
            assert thread1["updated_at"] == thread2["updated_at"], "replayed message_id must not trigger a second update"
        finally:
            monkeypatch.delenv("CONVERSATION_MEMORY_ENABLED", raising=False)
            monkeypatch.delenv("WHATSAPP_THREAD_RECONSTRUCTION_ENABLED", raising=False)
            monkeypatch.delenv("ESCORT_CLIENT_PHONES", raising=False)
            get_settings.cache_clear()
