"""
tests/unit/test_phase2_identity_resolver.py
===========================================

Phase-2 inbound-identity resolver — unit + DB-backed tests for the
2026-09-01 remediation pass.

Covers (see the §26 matrix in the autonomous-session brief):
  pure helpers  — compute_identity_phone precedence, +880/01/JID/Bengali
                  normalization, unresolved-LID rejection, canonical_message_key
                  (transport-ID-only, forensic), _best_name precedence
  DB-backed     — trusted role/name resolution, inactive employee still matches,
                  wbom_contacts platform='whatsapp' filter (+ negative case),
                  push-name never authoritative, name never overrides phone,
                  ambiguous name search, secret non-exposure, fallback preserves
                  identity_phone
"""
from __future__ import annotations

import pytest
import pytest_asyncio

from modules.identity_resolver import (
    resolve_inbound_identity,
    compute_identity_phone,
    compute_canonical_message_key,
    search_identity_by_name,
    _best_name,
    _is_unresolved_lid,
    reset_column_cache,
    is_phase2_identity_enabled,
    SOURCE_EMPLOYEE,
    SOURCE_WBOM_CONTACTS,
    SOURCE_WHATSMEOW_PUSH,
    SOURCE_META_PROFILE,
    SOURCE_UNKNOWN,
)


@pytest_asyncio.fixture(autouse=True)
async def _inject_test_db_pool(test_db_pool):
    import app.database as db_module
    prev = db_module._pool
    db_module._pool = test_db_pool
    reset_column_cache()
    try:
        yield
    finally:
        db_module._pool = prev
        reset_column_cache()


# ─────────────────────────── pure helpers (no DB) ───────────────────────────

class TestComputeIdentityPhone:
    def test_bd_forms_all_normalize_to_8801(self):
        for raw in ("8801711111111", "01711111111", "1711111111",
                    "+8801711111111", "01711-111111", "০১৭১১১১১১১১"):
            assert compute_identity_phone(sender_number=raw) == "8801711111111", raw

    def test_precedence_canonical_over_resolved_over_sender(self):
        assert compute_identity_phone(
            canonical_phone="8801711111111",
            resolved_phone="8801722222222",
            sender_number="8801733333333",
        ) == "8801711111111"
        assert compute_identity_phone(
            resolved_phone="8801722222222", sender_number="8801733333333",
        ) == "8801722222222"
        assert compute_identity_phone(sender_number="8801733333333") == "8801733333333"

    def test_unresolved_lid_is_never_a_phone(self):
        assert _is_unresolved_lid("unresolved:12345")
        assert compute_identity_phone(sender_number="unresolved:155539800895531") is None
        # only a last-resort lid: handle, never a bare number, never unresolved:
        got = compute_identity_phone(sender_lid="unresolved:155539800895531")
        assert got == "lid:155539800895531"
        assert not got.startswith("unresolved:")

    def test_junk_and_empty(self):
        assert compute_identity_phone(sender_number="") is None
        assert compute_identity_phone(sender_number="@s.whatsapp.net") is None
        assert compute_identity_phone() is None


class TestCanonicalMessageKey:
    def test_meta_wamid_used_directly(self):
        k = compute_canonical_message_key(
            platform="meta", source_message_ref="wamid.ABC123",
            sender_phone="8801711111111", chat_jid=None,
        )
        assert k == "meta:wamid:wamid.abc123"

    def test_bridge_uses_transport_msgid(self):
        k = compute_canonical_message_key(
            platform="bridge1", source_message_ref="3EB0ABC",
            sender_phone="8801711111111", chat_jid="8801711111111@s.whatsapp.net",
        )
        assert k.startswith("bridge:msgid:") and "3eb0abc" in k

    def test_cross_bridge_same_msgid_same_key(self):
        a = compute_canonical_message_key(
            platform="bridge1", source_message_ref="XID", sender_phone="8801711111111",
            chat_jid="8801711111111@s.whatsapp.net")
        b = compute_canonical_message_key(
            platform="bridge2", source_message_ref="XID", sender_phone="8801711111111",
            chat_jid="8801711111111@s.whatsapp.net")
        assert a == b

    def test_no_transport_id_no_key(self):
        # the old content+60s heuristic was removed — no ref => no key
        assert compute_canonical_message_key(
            platform="bridge1", source_message_ref=None,
            sender_phone="8801711111111", chat_jid="x@s.whatsapp.net",
            content="hello", source_timestamp_iso="2026-09-01T10:00:00Z",
        ) is None

    def test_non_message_types_get_no_key(self):
        for mt in ("reaction", "receipt", "revoke", "deleted", "protocol"):
            assert compute_canonical_message_key(
                platform="bridge1", source_message_ref="R1",
                sender_phone="8801711111111", chat_jid="x", message_type=mt,
            ) is None


class TestBestNamePrecedence:
    def test_trusted_employee_beats_push_name(self):
        name, src, conf = _best_name(
            identity_phone="8801711111111",
            emp={"trusted_identity_name": "Karim Uddin"},
            bridge_push_name="xXProKillerXx",
        )
        assert (name, src) == ("Karim Uddin", SOURCE_EMPLOYEE) and conf == 100

    def test_push_name_only_when_no_trusted_row(self):
        name, src, conf = _best_name(
            identity_phone="8801711111111", bridge_push_name="Shownby",
        )
        assert (name, src, conf) == ("Shownby", SOURCE_WHATSMEOW_PUSH, 30)

    def test_meta_profile_ranks_below_trusted_above_bridge(self):
        name, src, _ = _best_name(
            identity_phone="8801711111111",
            meta_profile_name="Meta Name", bridge_push_name="Push Name",
        )
        assert (name, src) == ("Meta Name", SOURCE_META_PROFILE)

    def test_no_phone_no_name(self):
        assert _best_name(identity_phone=None, bridge_push_name="x") == (None, SOURCE_UNKNOWN, 0)


def test_flag_defaults_off(monkeypatch):
    monkeypatch.delenv("PHASE2_IDENTITY_RESOLVER_ENABLED", raising=False)
    assert is_phase2_identity_enabled() is False
    monkeypatch.setenv("PHASE2_IDENTITY_RESOLVER_ENABLED", "true")
    assert is_phase2_identity_enabled() is True
    monkeypatch.setenv("PHASE2_IDENTITY_RESOLVER_ENABLED", "false")
    assert is_phase2_identity_enabled() is False


# ─────────────────────────── DB-backed resolver ────────────────────────────

@pytest.mark.asyncio
async def test_employee_phone_resolves_role_and_name_pushname_ignored(seed_employee):
    res = await resolve_inbound_identity(
        platform="bridge1",
        sender_number=seed_employee["employee_mobile"],
        canonical_phone=seed_employee["employee_mobile"],
        bridge_push_name="TOTALLY DIFFERENT NAME",
    )
    assert res["identity_phone"] == "8801811111111"
    assert res["identity_role"] in ("employee", "security_guard")
    assert res["trusted_identity_name"] == seed_employee["employee_name"]
    # snapshot comes from the trusted employee row, NOT the push name
    assert res["sender_name_snapshot"] == seed_employee["employee_name"]
    assert res["sender_name_source"] == SOURCE_EMPLOYEE


@pytest.mark.asyncio
async def test_inactive_employee_still_matches_no_duplicate(test_db_pool):
    async with test_db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO wbom_employees (employee_mobile, employee_name, designation, status, basic_salary) "
            "VALUES ('8801799999999', 'Former Staffer', 'Security Guard', 'Inactive', 9000)"
        )
        before = await conn.fetchval("SELECT count(*) FROM wbom_employees")
    res = await resolve_inbound_identity(
        platform="bridge1", sender_number="8801799999999", canonical_phone="8801799999999",
    )
    assert res["identity_phone"] == "8801799999999"
    assert res["trusted_identity_name"] == "Former Staffer"
    assert (res.get("identity_lifecycle") or "").lower() == "inactive"
    async with test_db_pool.acquire() as conn:
        after = await conn.fetchval("SELECT count(*) FROM wbom_employees")
    assert after == before  # resolver never creates rows


@pytest.mark.asyncio
async def test_wbom_contacts_platform_filter_positive_and_negative(test_db_pool):
    async with test_db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO wbom_contacts (whatsapp_number, display_name, platform, is_active) "
            "VALUES ('8801744444444', 'WA Contact', 'whatsapp', true)"
        )
        # a same-number row on a different platform must NOT become WA identity
        await conn.execute(
            "INSERT INTO wbom_contacts (whatsapp_number, display_name, platform, is_active) "
            "VALUES ('8801755555555', 'Messenger Only', 'facebook', true)"
        )
    hit = await resolve_inbound_identity(
        platform="bridge1", sender_number="8801744444444", canonical_phone="8801744444444")
    assert hit["trusted_identity_name"] == "WA Contact"
    assert hit["contact_id"] is not None

    miss = await resolve_inbound_identity(
        platform="bridge1", sender_number="8801755555555", canonical_phone="8801755555555")
    assert miss["trusted_identity_name"] is None
    assert miss["identity_role"] == "unknown"


@pytest.mark.asyncio
async def test_unknown_sender_is_unknown(test_db_pool):
    res = await resolve_inbound_identity(
        platform="bridge1", sender_number="8801700000009", canonical_phone="8801700000009")
    assert res["identity_role"] == "unknown"
    assert res["trusted_identity_name"] is None
    assert res["sender_name_source"] == SOURCE_UNKNOWN


@pytest.mark.asyncio
async def test_unresolved_lid_null_identity_phone(test_db_pool):
    res = await resolve_inbound_identity(
        platform="bridge1", sender_number="unresolved:155539800895531",
        sender_lid="unresolved:155539800895531",
    )
    assert res["identity_phone"] == "lid:155539800895531"
    assert res["identity_role"] == "unknown"
    assert res["trusted_identity_name"] is None


@pytest.mark.asyncio
async def test_meta_profile_name_is_display_only(seed_employee, test_db_pool):
    # an UNKNOWN meta sender with a human-looking profile name -> still unknown role
    res = await resolve_inbound_identity(
        platform="meta", sender_number="8801700000008", canonical_phone="8801700000008",
        meta_profile_name="Karim Uddin",  # looks like the employee's name — must not match
    )
    assert res["identity_role"] == "unknown"
    assert res["sender_name_source"] == SOURCE_META_PROFILE
    assert res["sender_name_snapshot"] == "Karim Uddin"
    assert res["trusted_identity_name"] is None  # a name never becomes trusted identity


@pytest.mark.asyncio
async def test_protected_admin_and_secret_non_exposure(seed_admin):
    # 8801700000001 is in the test ADMIN_NUMBERS env -> protected-config tier
    res = await resolve_inbound_identity(
        platform="bridge2", sender_number=seed_admin["phone"], canonical_phone=seed_admin["phone"])
    assert res["identity_role"] == "admin"
    assert res["identity_source"] == "protected_config"
    # exact output key set — no leaked config values, no extra keys
    assert set(res) == {
        "identity_phone", "identity_role", "identity_confidence", "identity_source",
        "trusted_identity_name", "sender_name_snapshot", "sender_name_source",
        "sender_name_confidence", "employee_id", "contact_id", "identity_lifecycle",
        "canonical_message_key",
    }


@pytest.mark.asyncio
async def test_name_search_ambiguous_returns_candidates(test_db_pool):
    async with test_db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO wbom_contacts (whatsapp_number, display_name, platform, is_active) VALUES "
            "('8801766666661', 'Rahim Sharif', 'whatsapp', true),"
            "('8801766666662', 'Rahim Ali', 'whatsapp', true)"
        )
    results = await search_identity_by_name("Rahim")
    phones = {r["phone"] for r in results}
    assert {"8801766666661", "8801766666662"} <= phones


@pytest.mark.asyncio
async def test_name_search_ignores_non_whatsapp_contacts(test_db_pool):
    async with test_db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO wbom_contacts (whatsapp_number, display_name, platform, is_active) "
            "VALUES ('8801777777777', 'Zephyr Unique', 'facebook', true)"
        )
    results = await search_identity_by_name("Zephyr Unique")
    assert all(r["phone"] != "8801777777777" for r in results)


@pytest.mark.asyncio
async def test_fallback_preserves_identity_phone_on_db_error(monkeypatch):
    # force a DB error inside the tier chain; identity_phone (pure) must survive
    import modules.identity_resolver as ir

    async def _boom(*a, **k):
        raise RuntimeError("simulated pool failure")

    monkeypatch.setattr(ir, "_resolve_protected_config_role", _boom)
    res = await resolve_inbound_identity(
        platform="bridge1", sender_number="8801788888888", canonical_phone="8801788888888",
        source_message_ref="MSG-1",
    )
    assert res["identity_phone"] == "8801788888888"          # preserved
    assert res["canonical_message_key"] is not None           # pure, preserved
    assert res["identity_role"] == "unknown"                  # DB part degraded safely
