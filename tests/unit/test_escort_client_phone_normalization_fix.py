"""Unit tests — ESCORT_CLIENT_PHONES format-mismatch incident fix
(2026-07-20).

Root cause: modules.escort._escort_client_phone_list() normalized to
8801XXXXXXXXX (via to_880()) while modules.escort._is_allowed_escort_
client() and _is_authoritative_bridge_confirmation() compared against
_normalize_mobile()'s 01XXXXXXXXX output -- a format that could never
match, silently rejecting every real configured escort client and
blocking the real bridge-confirmation source-of-truth path. Confirmed
against the exact production ESCORT_CLIENT_PHONES value
(8801670535255,8801757622300,8801836743754).

Fix: _escort_client_phone_list() now normalizes to 01XXXXXXXXX (the
repo's own declared canonical storage format per modules.
fazle_payroll_engine.normalizer's Owner Directive 2026-07-01), matching
_normalize_mobile() -- itself now a thin wrapper around the canonical
normalize_bd_phone() instead of a separate, weaker reimplementation.

Section 4 finding (Phase 1B.2, certified 2026-07-17, commits 7313613/
45d23fa/24051fe/f8a1d75): a sender verified via stored fazle_contact_
roles.role or a contact-name "escort" marker is DELIBERATELY exempt from
the ESCORT_CLIENT_PHONES gate -- "verified senders no longer blocked by
the phone-only allowlist gate (a real gap this fix closes)" is the
verbatim reasoning on record. This is NOT a policy violation; it is an
already-shipped, Owner-ratified architecture (client_escort_buyer has
three independent, coequal verification sources, only one of which is
the .env phone list) -- see management_decisions.md, "Phase 1B.2
Escort Client Identity and Routing Convergence Proposal (FINALIZED per
Owner decision)", Decisions 1-2. Tests here verify that behavior
continues to work correctly, not that it should be changed.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

pytestmark = pytest.mark.unit

# Exact production ESCORT_CLIENT_PHONES value (not a stand-in) -- masked
# in any printed/logged output per the task's own instruction, used here
# only as literal test input, never logged.
PROD_ESCORT_CLIENT_PHONES = "8801670535255,8801757622300,8801836743754"
PROD_CLIENT_NUMBERS = ("8801670535255", "8801757622300", "8801836743754")


def _mask(phone: str) -> str:
    """Section 5's masking requirement for anything resembling a report/
    log -- keep first 4 + last 2 digits, blank the middle."""
    if len(phone) < 7:
        return "*" * len(phone)
    return phone[:4] + "*" * (len(phone) - 6) + phone[-2:]


@pytest.fixture
def escort_client_env(monkeypatch):
    from app.config import get_settings
    monkeypatch.setenv("ESCORT_CLIENT_PHONES", PROD_ESCORT_CLIENT_PHONES)
    get_settings.cache_clear()
    yield
    monkeypatch.delenv("ESCORT_CLIENT_PHONES", raising=False)
    get_settings.cache_clear()


# ===========================================================================
# #1 + #2: each configured client allowed, in every required input form
# ===========================================================================

class TestConfiguredClientsAllowedInEveryForm:

    def test_each_production_client_number_allowed_as_configured(self, escort_client_env):
        from modules.escort import _is_allowed_escort_client
        for number in PROD_CLIENT_NUMBERS:
            assert _is_allowed_escort_client(number) is True, f"failed for {_mask(number)}"

    def test_local_01_form_allowed(self, escort_client_env):
        from modules.escort import _is_allowed_escort_client
        for number in PROD_CLIENT_NUMBERS:
            local = "0" + number[3:]
            assert _is_allowed_escort_client(local) is True, f"failed for {_mask(local)}"

    def test_plus_880_form_allowed(self, escort_client_env):
        from modules.escort import _is_allowed_escort_client
        for number in PROD_CLIENT_NUMBERS:
            plus_form = "+" + number
            assert _is_allowed_escort_client(plus_form) is True, f"failed for {_mask(plus_form)}"

    def test_spaced_form_allowed(self, escort_client_env):
        from modules.escort import _is_allowed_escort_client
        for number in PROD_CLIENT_NUMBERS:
            spaced = number[:3] + " " + number[3:7] + " " + number[7:]
            assert _is_allowed_escort_client(spaced) is True, f"failed for {_mask(spaced)}"

    def test_bangla_digit_form_allowed(self, escort_client_env):
        from modules.escort import _is_allowed_escort_client
        bn_map = str.maketrans("0123456789", "০১২৩৪৫৬৭৮৯")
        for number in PROD_CLIENT_NUMBERS:
            bangla = number.translate(bn_map)
            assert _is_allowed_escort_client(bangla) is True, f"failed for bangla-digit form of {_mask(number)}"


# ===========================================================================
# #3: unconfigured number rejected
# ===========================================================================

def test_unconfigured_number_rejected(escort_client_env):
    from modules.escort import _is_allowed_escort_client
    assert _is_allowed_escort_client("01999999999") is False


# ===========================================================================
# #4: employee with escort-shaped text rejected as order intake
# (unverified path only -- verified path is Section 4's deliberate exemption)
# ===========================================================================

@pytest.mark.asyncio
async def test_unverified_sender_with_escort_shaped_text_rejected(escort_client_env):
    from modules.escort import handle_escort_client_message
    reply, note = await handle_escort_client_message(
        "Bushra Biva-1(2nd Trip)\n1250MT\n01999999999\nOuter To Local",
        "01999999999", "bridge2", is_verified=False,
    )
    assert note is None


# ===========================================================================
# #5: a verified (stored-role/contact-name) identity is NOT blocked by
# .env absence -- Section 4's documented, Owner-ratified exemption.
# ===========================================================================

@pytest.mark.asyncio
async def test_verified_identity_not_in_env_still_reaches_parser(monkeypatch):
    """Phase 1B.2 (certified 2026-07-17): 'verified senders no longer
    blocked by the phone-only allowlist gate' is a deliberate, already-
    shipped fix, not a bug. A sender verified via stored role/contact-
    name marker must still be able to create a draft even if their
    number isn't (or is no longer) in ESCORT_CLIENT_PHONES. Mocks
    save_escort_programs (DB write) -- this test proves the GATE lets a
    complete order through, not the full DB-backed save pipeline."""
    from app.config import get_settings
    import modules.escort as escort_module

    monkeypatch.setenv("ESCORT_CLIENT_PHONES", "")  # deliberately NOT containing this sender
    get_settings.cache_clear()

    saved = {}

    async def _fake_save(*a, **kw):
        saved["called"] = True

    monkeypatch.setattr(escort_module, "save_escort_programs", _fake_save)
    try:
        reply, note = await escort_module.handle_escort_client_message(
            "Mother Vessel: MV Horda\nBushra Biva-1(2nd Trip)\n1250MT\n01911111111\nOuter To Local",
            "01911111111", "bridge2", is_verified=True,
        )
        assert saved.get("called") is True, "a complete order from a VERIFIED sender must reach save, even with an empty ESCORT_CLIENT_PHONES"
        assert note is not None
    finally:
        monkeypatch.delenv("ESCORT_CLIENT_PHONES", raising=False)
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_verified_identity_bypasses_is_allowed_escort_client_call(monkeypatch):
    """Direct proof of the bypass: _is_allowed_escort_client() must never
    even be called on the verified path."""
    from app.config import get_settings
    import modules.escort as escort_module

    monkeypatch.setenv("ESCORT_CLIENT_PHONES", "")
    get_settings.cache_clear()

    async def _boom(*a, **kw):
        raise AssertionError("_is_allowed_escort_client must not be called for a verified sender")

    async def _fake_save(*a, **kw):
        pass

    monkeypatch.setattr(escort_module, "_is_allowed_escort_client", _boom)
    monkeypatch.setattr(escort_module, "save_escort_programs", _fake_save)
    try:
        await escort_module.handle_escort_client_message(
            "Mother Vessel: MV Horda\nBushra Biva-1(2nd Trip)\n1250MT\n01911111111\nOuter To Local",
            "01911111111", "bridge2", is_verified=True,
        )
    finally:
        monkeypatch.delenv("ESCORT_CLIENT_PHONES", raising=False)
        get_settings.cache_clear()


# ===========================================================================
# #6: authorized client still reaches the legacy escort draft path
# ===========================================================================

@pytest.mark.asyncio
async def test_authorized_client_reaches_legacy_draft_path(escort_client_env, monkeypatch):
    import modules.escort as escort_module

    saved = {}

    async def _fake_save(*a, **kw):
        saved["called"] = True

    monkeypatch.setattr(escort_module, "save_escort_programs", _fake_save)
    reply, note = await escort_module.handle_escort_client_message(
        "Mother Vessel: MV Horda\nBushra Biva-1(2nd Trip)\n1250MT\n01821375926\nOuter To Local\nAC:SST/NMT",
        "0" + PROD_CLIENT_NUMBERS[0][3:], "bridge2", is_verified=False,
    )
    # Complete order from a whitelisted-but-unverified sender must reach
    # the parser and produce an admin draft note (not silently rejected).
    assert saved.get("called") is True
    assert note is not None


# ===========================================================================
# #7 + #8: Phase B shadow observer -- accepts authorized clients now,
# still excludes unauthorized/system-generated messages
# ===========================================================================

@pytest.mark.asyncio
async def test_phase_b_observer_accepts_authorized_client_after_fix(escort_client_env, monkeypatch):
    from app.config import get_settings
    from modules.conversation_memory import thread_reconstruction as tr
    import modules.conversation_memory as cm

    monkeypatch.setenv("CONVERSATION_MEMORY_ENABLED", "true")
    monkeypatch.setenv("WHATSAPP_THREAD_RECONSTRUCTION_ENABLED", "true")
    get_settings.cache_clear()

    reached = {}

    async def _fake_get_open_thread(**kwargs):
        reached["called"] = True
        return None

    async def _fake_create_thread(**kwargs):
        reached["created"] = True
        return {"id": 1, "fields": {}, "state": "open", "conflict_flags": [], "last_message_id": None}

    async def _noop(*a, **kw):
        return None

    async def _not_processed(*a, **kw):
        return False

    monkeypatch.setattr(cm, "is_message_processed", _not_processed)
    monkeypatch.setattr(cm, "get_open_thread", _fake_get_open_thread)
    monkeypatch.setattr(cm, "get_open_threads", AsyncMock(return_value=[]))
    monkeypatch.setattr(cm, "create_thread", _fake_create_thread)
    monkeypatch.setattr(cm, "update_thread", _noop)
    monkeypatch.setattr(cm, "mark_message_processed", _noop)
    monkeypatch.setattr(cm, "record_shadow_comparison", _noop)

    # shadow_mode_scope wraps the DB transaction; mock it as a no-op so
    # the test never needs a real DB connection.
    from contextlib import asynccontextmanager
    @asynccontextmanager
    async def _noop_scope(): yield
    monkeypatch.setattr("app.database.shadow_mode_scope", _noop_scope)

    # check_existing_draft_relation makes a DB read; replace with stub.
    monkeypatch.setattr(
        "modules.conversation_memory.grouping.check_existing_draft_relation",
        AsyncMock(return_value=("no_candidate", {"reason": "test_stub"})),
    )

    try:
        local_form = "0" + PROD_CLIENT_NUMBERS[0][3:]
        await tr.shadow_observe_escort_client_message(
            text="Bushra Biva-1(2nd Trip)\n1250MT\n01821375926\nOuter To Local",
            sender_phone=local_form, source="bridge2", message_id="FIX-VERIFY-1",
            sender_role="client_escort_buyer",
        )
        assert reached.get("called") or reached.get("created"), "authorized client must now reach thread lookup/creation"
    finally:
        monkeypatch.delenv("CONVERSATION_MEMORY_ENABLED", raising=False)
        monkeypatch.delenv("WHATSAPP_THREAD_RECONSTRUCTION_ENABLED", raising=False)
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_phase_b_observer_still_excludes_unauthorized(escort_client_env, monkeypatch):
    from app.config import get_settings
    from modules.conversation_memory import thread_reconstruction as tr
    import modules.conversation_memory as cm

    monkeypatch.setenv("CONVERSATION_MEMORY_ENABLED", "true")
    monkeypatch.setenv("WHATSAPP_THREAD_RECONSTRUCTION_ENABLED", "true")
    get_settings.cache_clear()

    async def _boom(*a, **kw):
        raise AssertionError("must never be reached for an unauthorized sender")

    monkeypatch.setattr(cm, "get_open_thread", _boom)
    monkeypatch.setattr(cm, "create_thread", _boom)

    try:
        await tr.shadow_observe_escort_client_message(
            text="Bushra Biva-1(2nd Trip)\n1250MT\n01821375926\nOuter To Local",
            sender_phone="01999999999", source="bridge2", message_id="FIX-VERIFY-2",
            sender_role="employee",
        )
    finally:
        monkeypatch.delenv("CONVERSATION_MEMORY_ENABLED", raising=False)
        monkeypatch.delenv("WHATSAPP_THREAD_RECONSTRUCTION_ENABLED", raising=False)
        get_settings.cache_clear()


# ===========================================================================
# #9: whitelist update invalidates cache immediately (existing behavior)
# ===========================================================================

def test_whitelist_update_invalidates_cache_immediately(monkeypatch):
    from app.config import get_settings
    from modules.escort import _is_allowed_escort_client

    monkeypatch.setenv("ESCORT_CLIENT_PHONES", "01900000777")
    get_settings.cache_clear()
    assert _is_allowed_escort_client("01900000777") is True
    assert _is_allowed_escort_client("01900000888") is False

    monkeypatch.setenv("ESCORT_CLIENT_PHONES", "01900000888")
    get_settings.cache_clear()
    assert _is_allowed_escort_client("01900000888") is True
    assert _is_allowed_escort_client("01900000777") is False

    monkeypatch.delenv("ESCORT_CLIENT_PHONES", raising=False)
    get_settings.cache_clear()


# ===========================================================================
# #10: duplicate whitelist representations normalize to one value
# ===========================================================================

def test_duplicate_whitelist_representations_dedupe(monkeypatch):
    from app.config import get_settings
    from modules.escort import _escort_client_phone_list

    monkeypatch.setenv("ESCORT_CLIENT_PHONES", "01670535255,8801670535255,+8801670535255")
    get_settings.cache_clear()
    try:
        result = _escort_client_phone_list()
        assert result == ["01670535255"], f"expected one deduped canonical entry, got {result}"
    finally:
        monkeypatch.delenv("ESCORT_CLIENT_PHONES", raising=False)
        get_settings.cache_clear()


# ===========================================================================
# #11: invalid configured numbers rejected/silently dropped (existing policy)
# ===========================================================================

def test_invalid_configured_number_dropped_not_crashed(monkeypatch):
    from app.config import get_settings
    from modules.escort import _escort_client_phone_list

    monkeypatch.setenv("ESCORT_CLIENT_PHONES", "not-a-phone,01670535255,12345")
    get_settings.cache_clear()
    try:
        result = _escort_client_phone_list()
        assert result == ["01670535255"]
    finally:
        monkeypatch.delenv("ESCORT_CLIENT_PHONES", raising=False)
        get_settings.cache_clear()


# ===========================================================================
# #12: bridge confirmation / source-of-truth direction rule not weakened --
# still rejects a non-admin, non-mapped bridge number
# ===========================================================================

def test_confirmation_direction_rule_not_weakened(escort_client_env, monkeypatch):
    from modules.escort import _is_authoritative_bridge_confirmation

    ctx = {
        "recipient_phone": PROD_CLIENT_NUMBERS[0],
        "bridge_number": "01900000999",  # not a real admin/bridge number
        "source": "bridge2",
    }
    assert _is_authoritative_bridge_confirmation(ctx) is False


def test_confirmation_recognized_for_real_admin_bridge_number(escort_client_env, monkeypatch):
    from app.config import get_settings
    from modules.escort import _is_authoritative_bridge_confirmation

    monkeypatch.setenv("BRIDGE1_NUMBER", "8801880446111")
    monkeypatch.setenv("ADMIN_BRIDGE1_NUMBER", "8801880446111")
    get_settings.cache_clear()
    try:
        ctx = {
            "recipient_phone": PROD_CLIENT_NUMBERS[0],
            "bridge_number": "8801880446111",
            "source": "bridge1",
        }
        assert _is_authoritative_bridge_confirmation(ctx) is True
    finally:
        monkeypatch.delenv("BRIDGE1_NUMBER", raising=False)
        monkeypatch.delenv("ADMIN_BRIDGE1_NUMBER", raising=False)
        get_settings.cache_clear()
