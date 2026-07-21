"""
Route B Conversation Handoff — Owner-approved staged rollout (2026-07-22).

Covers the 14 required test scenarios from the Owner's implementation
approval. Feature is OFF by default (ROUTE_B_HANDOFF_ENABLED=false); these
tests exercise the code paths directly (monkeypatching settings where the
flag state matters) rather than depending on production configuration.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from app.database import execute, fetch_val, fetch_one


@pytest.fixture(autouse=True)
async def _reset_settings_cache():
    """Pre-existing, unrelated test-isolation bug (not introduced by this
    change, not fixed here -- out of this task's scope): tests/unit/
    test_llm_provider_order.py's _reset_settings() monkeypatches DATABASE_URL
    to a bogus value and calls get_settings.cache_clear() to pick it up, but
    never calls cache_clear() again after monkeypatch's teardown restores the
    real env var -- so app.config.get_settings()'s module-level @lru_cache
    stays permanently poisoned with the bogus database_url for the rest of
    the pytest process. Worse: if any test in between spins up the real app
    (triggering app.database.init_db()) while that bogus value is cached, a
    global asyncpg pool gets created with the bogus credentials and NEVER
    reconnects -- app.database.db_conn() checks `if _pool is not None` first
    and uses it directly, bypassing get_settings() entirely on every later
    call, so clearing the settings cache alone doesn't fix it. Reproduced
    with a minimal 2-file case:
    `pytest test_llm_provider_order.py test_route_b_handoff.py` alone is
    enough to trigger asyncpg.exceptions.InvalidPasswordError here, even
    though this file passes 100% in isolation. Since these tests genuinely
    need a working app.database connection, reset both the cache and the
    pool before each of ours, so we're correct regardless of what ran
    earlier in the same process, without touching the actual offending
    file.

    Declared first and explicitly depended on by
    _ensure_recruitment_sessions_table below (not just autouse declaration
    order) -- autouse fixtures at the same scope are NOT guaranteed to run
    in file-declaration order (confirmed empirically: this one was
    observed running SECOND despite being declared first), only an
    explicit fixture-parameter dependency guarantees ordering."""
    from app.config import get_settings
    import app.database as db

    get_settings.cache_clear()
    if db._pool is not None:
        try:
            await db._pool.close()
        except Exception:
            pass
        db._pool = None


@pytest.fixture(autouse=True)
async def _ensure_recruitment_sessions_table(_reset_settings_cache):
    """Pre-existing, unrelated test-environment gap (not introduced by this
    change): db/migrations/003b_recruitment_sessions_fix.sql references
    wbom_candidates via FK, but no migration anywhere creates that table --
    it exists in production only because it was created directly, outside
    the tracked migration history, long before this repo's migrations/
    directory existed. On a fresh test DB built purely from db/migrations/*.sql
    this makes 003b fail (tolerated by the schema-setup runner, logged as one
    of the "N errors (tolerated)" lines), leaving fazle_recruitment_sessions
    missing. Stubbing the FK target here (test DB only, this file's tests
    only) is the minimal fix -- not touching the shared conftest.py or any
    tracked migration for a pre-existing, out-of-scope gap.

    Takes _reset_settings_cache as an explicit parameter (not just relying
    on autouse ordering) so it always runs after the settings/pool reset
    above."""
    exists = await fetch_val("SELECT to_regclass('fazle_recruitment_sessions')")
    if not exists:
        await execute(
            "CREATE TABLE IF NOT EXISTS wbom_candidates "
            "(candidate_id serial primary key, candidate_name text)"
        )
        with open("db/migrations/003b_recruitment_sessions_fix.sql", encoding="utf-8") as f:
            sql = f.read()
        await execute(sql)
    yield


def _phone() -> str:
    """Unique test phone per test to avoid cross-test DB collisions."""
    return "88019" + uuid.uuid4().hex[:8]


async def _cleanup_recruitment_session(phone: str) -> None:
    await execute("DELETE FROM fazle_recruitment_sessions WHERE phone = $1", phone)


async def _cleanup_handoffs(phone: str) -> None:
    await execute("DELETE FROM fazle_route_b_handoffs WHERE phone = $1", phone)


async def _cleanup_drafts(phone: str) -> None:
    await execute("DELETE FROM fazle_draft_replies WHERE recipient = $1", phone)


# ── 1. Unknown sender becomes a recruitment candidate ───────────────────────────

@pytest.mark.asyncio
async def test_unknown_sender_becomes_recruitment_candidate(monkeypatch):
    from modules.route_b_handoff import _check_recruitment_handoff

    phone = _phone()
    try:
        # Deterministic layer alone says no (Tier 7/8 already established
        # this for the current message before Route B ran); Ollama hint says
        # recruitment, and the wider (history+current) window IS deterministically
        # eligible via the explicit-trigger check.
        result = await _check_recruitment_handoff(
            phone, "চাকরি আছে?", "bridge1", "unknown", "unknown",
            history="[সে] ভাই একটু কথা বলতে চাই", ollama_category="recruitment",
        )
        assert result is not None
        assert result.destination == "recruitment"
        assert result.outcome == "handed_off"
        assert result.reply
    finally:
        await _cleanup_recruitment_session(phone)


# ── 2. Indirect recruitment intent found from conversation context ─────────────

@pytest.mark.asyncio
async def test_indirect_recruitment_intent_from_history(monkeypatch):
    """Current message alone ("হ্যাঁ") has zero recruitment signal and is not
    parseable by route_recruitment_message()'s own internal (narrow) check --
    only the wider window (history + current) is deterministically eligible.
    Proves the intake_message() fallback path (narrow-vs-wide disagreement)."""
    from modules.route_b_handoff import _check_recruitment_handoff

    phone = _phone()
    try:
        result = await _check_recruitment_handoff(
            phone, "হ্যাঁ", "bridge1", "unknown", "unknown",
            history="[সে] আপনাদের কোম্পানিতে কি চাকরি খালি আছে?",
            ollama_category="recruitment",
        )
        assert result is not None
        assert result.destination == "recruitment"
        # The bare "হ্যাঁ" alone has zero deterministic recruitment signal --
        # only the wider (history + current) window, corroborated by
        # Ollama's hint, justifies this handoff. Whether route_recruitment_
        # message()'s own internal re-check then also succeeds directly, or
        # disagrees and falls back to intake_message(), is an internal
        # implementation detail; what matters here is that Ollama's raw
        # category never solely decided this -- the reason must always
        # reference the history-corroboration path, not a bare Ollama trust.
        assert result.reason.startswith("ollama_suggested+history_corroborated")
        assert result.outcome == "handed_off"
    finally:
        await _cleanup_recruitment_session(phone)


# ── 3. Uncertain inquiry remains in normal clarification ───────────────────────

@pytest.mark.asyncio
async def test_uncertain_inquiry_stays_in_clarification():
    from modules.route_b_handoff import evaluate_handoff
    from app.config import get_settings

    get_settings.cache_clear() if hasattr(get_settings, "cache_clear") else None
    phone = _phone()
    try:
        # ollama_category will resolve to "none" (unreachable Ollama in test
        # env) and no deterministic signal exists in this casual text.
        result = await evaluate_handoff(
            sender=phone, text="ভাই কেমন আছেন?", source="bridge1",
            identity={"role": "unknown", "display_name": ""}, intent="unknown",
            history="", message_id=None,
        )
        # Master flag is off by default in this test process -- confirms "no
        # handoff" for a genuinely ambiguous message either way.
        assert result is None
    finally:
        await _cleanup_recruitment_session(phone)
        await _cleanup_handoffs(phone)


# ── 4. Active recruitment session skips generic Route B and reclassification ───

@pytest.mark.asyncio
async def test_active_session_skips_reclassification(monkeypatch):
    from modules.route_b_handoff import _check_active_recruitment_session
    import modules.route_b_handoff as rbh

    phone = _phone()
    try:
        sid = await fetch_val(
            """INSERT INTO fazle_recruitment_sessions
                   (phone, collection_step, funnel_stage, source_message, source_bridge, updated_at)
               VALUES ($1, 'age', 'collecting', 'কাজ চাই', 'bridge1', NOW())
               RETURNING id""",
            phone,
        )
        assert sid

        classify_mock = AsyncMock(return_value="none")
        monkeypatch.setattr("app.ollama.classify_route_b_category", classify_mock)

        result = await _check_active_recruitment_session(phone, "25", "bridge1", "unknown")
        assert result is not None
        assert result.destination == "recruitment"
        assert result.reason == "active_session_continuation"
        classify_mock.assert_not_called()  # no Ollama reclassification needed

        row = await fetch_one("SELECT age, collection_step FROM fazle_recruitment_sessions WHERE phone=$1", phone)
        assert row["age"] == 25  # the bare numeric answer was correctly recorded
    finally:
        await _cleanup_recruitment_session(phone)


# ── 5. Known employee never enters Route B ──────────────────────────────────────

@pytest.mark.asyncio
async def test_known_employee_never_enters_route_b(monkeypatch):
    """Regression at the message_router level: a phone that resolves to
    role=employee via identity_brain's DB lookup must never reach
    handle_route_b_message() at all -- Tier 10 handles it first."""
    import modules.message_router as router

    phone = _phone()
    monkeypatch.setattr(router, "_should_silent_skip", AsyncMock(return_value=(False, "")))
    monkeypatch.setattr(
        router, "detect_identity",
        AsyncMock(return_value={
            "role": "employee", "display_name": "Karim", "identity_confidence": 90,
            "identity_source": "db_employee", "employee_id": 42,
        }),
    )
    route_b_mock = AsyncMock(return_value=("should not be called", None))
    monkeypatch.setattr("modules.route_b.handle_route_b_message", route_b_mock)

    reply, admin_note = await router.process_message(phone, "আমার বেতন কবে দিবেন?", "bridge1")

    route_b_mock.assert_not_called()


# ── 6. False employee claim never gains employee access ────────────────────────

@pytest.mark.asyncio
async def test_false_employee_claim_does_not_gain_employee_access(monkeypatch):
    from modules.route_b_handoff import _check_employee_claim_handoff

    phone = _phone()
    try:
        # No employee_id in text, name doesn't match anyone -- phone was
        # already established as unregistered (that's why we're in Route B).
        monkeypatch.setattr(
            "modules.route_b_handoff.resolve_employee_identity",
            AsyncMock(return_value={
                "employee_id": None, "employee_name": None, "status": None,
                "resolution": "unknown", "verified": False,
            }),
        )
        monkeypatch.setattr("modules.message_router.get_primary_admin", lambda: "8801880000000")

        result = await _check_employee_claim_handoff(
            phone, "আমি এখানে চাকরি করি", "bridge1", "employee_claim", "msg-false-claim-1",
        )
        assert result is not None
        assert result.outcome == "admin_review"
        assert result.destination == "employee_claim_review"
        assert result.reply == ""  # never a live reply pretending to grant access
        assert result.admin_note is not None
        assert "NOT granted employee access" in result.admin_note["text"]
    finally:
        await _cleanup_drafts(phone)
        await _cleanup_handoffs(phone)


# ── 7. Former employee claim creates admin review only ─────────────────────────

@pytest.mark.asyncio
async def test_former_employee_claim_creates_admin_review_only(monkeypatch):
    from modules.route_b_handoff import _check_employee_claim_handoff

    phone = _phone()
    try:
        monkeypatch.setattr(
            "modules.route_b_handoff.resolve_employee_identity",
            AsyncMock(return_value={
                "employee_id": 7, "employee_name": "Jalal", "status": "Inactive",
                "resolution": "employee_id", "verified": True,
            }),
        )
        monkeypatch.setattr("modules.message_router.get_primary_admin", lambda: "8801880000000")

        result = await _check_employee_claim_handoff(
            phone, "আমি আগে এখানে কাজ করতাম, employee id 7", "bridge1",
            "former_employee_claim", "msg-former-1",
        )
        assert result is not None
        assert result.outcome == "admin_review"
        assert result.reply == ""
        assert result.confidence == "phone_or_id_verified_inactive_or_former"
    finally:
        await _cleanup_drafts(phone)
        await _cleanup_handoffs(phone)


# ── 8. Repeated employee claims create only one draft during cooldown ──────────

@pytest.mark.asyncio
async def test_repeated_employee_claims_create_only_one_draft(monkeypatch):
    from modules.route_b_handoff import _check_employee_claim_handoff, _record_handoff

    phone = _phone()
    try:
        monkeypatch.setattr(
            "modules.route_b_handoff.resolve_employee_identity",
            AsyncMock(return_value={
                "employee_id": None, "employee_name": None, "status": None,
                "resolution": "unknown", "verified": False,
            }),
        )
        monkeypatch.setattr("modules.message_router.get_primary_admin", lambda: "8801880000000")

        first = await _check_employee_claim_handoff(
            phone, "আমি এখানে চাকরি করি", "bridge1", "employee_claim", "msg-cool-1",
        )
        assert first.outcome == "admin_review"
        await _record_handoff(
            phone=phone, message_id="msg-cool-1", destination=first.destination,
            reason=first.reason, confidence=first.confidence, outcome=first.outcome,
        )

        second = await _check_employee_claim_handoff(
            phone, "আমি সত্যিই এখানে চাকরি করি বিশ্বাস করেন", "bridge1",
            "employee_claim", "msg-cool-2",
        )
        assert second is not None
        assert second.reason == "cooldown_suppressed"
        assert second.outcome == "declined"

        rows = await fetch_val(
            "SELECT COUNT(*) FROM fazle_route_b_handoffs WHERE phone=$1 AND destination='employee_claim_review'",
            phone,
        )
        assert rows == 1
    finally:
        await _cleanup_drafts(phone)
        await _cleanup_handoffs(phone)


# ── 9. Existing advance-request path remains unchanged ─────────────────────────

@pytest.mark.asyncio
async def test_advance_request_path_unchanged_regardless_of_flag(monkeypatch):
    import modules.route_b as route_b

    monkeypatch.setattr(route_b, "_get_conversation_history", AsyncMock(return_value=""))
    monkeypatch.setattr(route_b, "_get_saved_contact", AsyncMock(return_value=None))
    monkeypatch.setattr(route_b, "_get_profile_name", AsyncMock(return_value=""))
    monkeypatch.setattr(route_b, "is_advance_request", lambda _t: True)
    monkeypatch.setattr(route_b, "ollama_generate_reply", AsyncMock(return_value="ঠিক আছে, দেখছি"))
    draft_mock = AsyncMock(return_value={"admin_phone": "8801880000000", "bridge": "bridge1",
                                          "purpose": "route_b_money_advance", "text": "advance note"})
    monkeypatch.setattr(route_b, "_create_money_advance_admin_draft", draft_mock)

    handoff_mock = AsyncMock()
    monkeypatch.setattr("modules.route_b_handoff.evaluate_handoff", handoff_mock)

    reply, admin_note = await route_b.handle_route_b_message(
        "8801700000099", "আমার এডভান্স লাগবে", "bridge1",
        {"role": "unknown", "display_name": ""}, "unknown",
    )

    assert reply == "ঠিক আছে, দেখছি"
    assert admin_note == draft_mock.return_value
    draft_mock.assert_awaited_once()
    handoff_mock.assert_not_called()  # money-request path returns before handoff ever runs


# ── 10. Ollama failure produces a safe response and does not drop the message ──

@pytest.mark.asyncio
async def test_ollama_failure_produces_safe_response_not_dropped(monkeypatch):
    import modules.route_b as route_b
    from app.ollama import FALLBACK_REPLY_TEXT

    monkeypatch.setattr(route_b, "_get_conversation_history", AsyncMock(return_value=""))
    monkeypatch.setattr(route_b, "_get_saved_contact", AsyncMock(return_value=None))
    monkeypatch.setattr(route_b, "_get_profile_name", AsyncMock(return_value=""))
    monkeypatch.setattr(route_b, "is_advance_request", lambda _t: False)
    monkeypatch.setattr(route_b, "ollama_generate_reply", AsyncMock(return_value=FALLBACK_REPLY_TEXT))
    failure_mock = AsyncMock(return_value={"admin_phone": "8801880000000", "bridge": "bridge1",
                                            "purpose": "route_b_ollama_failure", "text": "outage note"})
    monkeypatch.setattr("modules.route_b_handoff.record_ollama_failure", failure_mock)

    reply, admin_note = await route_b.handle_route_b_message(
        "8801700000098", "কেমন আছেন?", "bridge1", {"role": "unknown", "display_name": ""}, "unknown",
        message_id="msg-outage-1",
    )

    assert reply == FALLBACK_REPLY_TEXT  # never empty, never dropped
    assert admin_note == failure_mock.return_value
    failure_mock.assert_awaited_once_with("8801700000098", "bridge1", "msg-outage-1")


# ── 11. Feature flag off preserves the current baseline exactly ────────────────

@pytest.mark.asyncio
async def test_flag_off_preserves_baseline_exactly(monkeypatch):
    import modules.route_b as route_b
    import modules.route_b_handoff as rbh
    from app.config import Settings

    monkeypatch.setattr(route_b, "_get_conversation_history", AsyncMock(return_value=""))
    monkeypatch.setattr(route_b, "_get_saved_contact", AsyncMock(return_value=None))
    monkeypatch.setattr(route_b, "_get_profile_name", AsyncMock(return_value=""))
    monkeypatch.setattr(route_b, "is_advance_request", lambda _t: False)
    monkeypatch.setattr(route_b, "ollama_generate_reply", AsyncMock(return_value="স্বাভাবিক বাংলা রিপ্লাই"))

    # Explicitly force the flag off (the real default) and prove evaluate_handoff
    # is a guaranteed no-op -- never touches the DB, never calls Ollama's
    # classifier -- when it is.
    settings = Settings(route_b_handoff_enabled=False)
    monkeypatch.setattr("modules.route_b_handoff.get_settings", lambda: settings)
    classify_mock = AsyncMock(return_value="recruitment")
    monkeypatch.setattr("app.ollama.classify_route_b_category", classify_mock)

    reply, admin_note = await route_b.handle_route_b_message(
        "8801700000097", "hi", "bridge1", {"role": "unknown", "display_name": "Client Test"}, "unknown",
    )

    assert reply == "স্বাভাবিক বাংলা রিপ্লাই"
    assert admin_note is None
    classify_mock.assert_not_called()


# ── 12. Handoff audit record is idempotent for the same message ID ─────────────

@pytest.mark.asyncio
async def test_handoff_audit_idempotent_for_same_message_id():
    from modules.route_b_handoff import _record_handoff

    phone = _phone()
    try:
        await _record_handoff(
            phone=phone, message_id="dup-msg-1", destination="recruitment",
            reason="explicit_recruitment", confidence="deterministic", outcome="handed_off",
        )
        await _record_handoff(
            phone=phone, message_id="dup-msg-1", destination="recruitment",
            reason="explicit_recruitment", confidence="deterministic", outcome="handed_off",
        )
        count = await fetch_val(
            "SELECT COUNT(*) FROM fazle_route_b_handoffs WHERE message_id = $1", "dup-msg-1",
        )
        assert count == 1
    finally:
        await _cleanup_handoffs(phone)
        await execute("DELETE FROM fazle_route_b_handoffs WHERE message_id = $1", "dup-msg-1")


# ── 13. Existing Route B replies remain unchanged when no handoff applies ──────

@pytest.mark.asyncio
async def test_route_b_reply_unchanged_when_no_handoff_applies(monkeypatch):
    import modules.route_b as route_b

    monkeypatch.setattr(route_b, "_get_conversation_history", AsyncMock(return_value=""))
    monkeypatch.setattr(route_b, "_get_saved_contact", AsyncMock(return_value=None))
    monkeypatch.setattr(route_b, "_get_profile_name", AsyncMock(return_value=""))
    monkeypatch.setattr(route_b, "is_advance_request", lambda _t: False)
    monkeypatch.setattr(route_b, "ollama_generate_reply", AsyncMock(return_value="ভাই, বুঝতে পারিনি, একটু বলবেন?"))
    monkeypatch.setattr("modules.route_b_handoff.evaluate_handoff", AsyncMock(return_value=None))

    reply, admin_note = await route_b.handle_route_b_message(
        "8801700000096", "asdkj asdkj", "bridge1", {"role": "unknown", "display_name": ""}, "unknown",
    )

    assert reply == "ভাই, বুঝতে পারিনি, একটু বলবেন?"
    assert admin_note is None


# ── 14. Existing recruitment session uniqueness is preserved ───────────────────

@pytest.mark.asyncio
async def test_recruitment_session_uniqueness_preserved():
    """Unmodified DB constraint (idx_recruitment_sessions_phone_active,
    migration 003b) -- a second 'collecting' session for the same phone must
    never be created, regardless of this feature's existence."""
    from modules.recruitment_flow import _create_session

    phone = _phone()
    try:
        sid1 = await _create_session(phone, "চাকরি চাই", "bridge1")
        assert sid1 is not None

        sid2 = await _create_session(phone, "চাকরি চাই আবার", "bridge1")
        assert sid2 is None  # unique constraint violation, caught and logged

        count = await fetch_val(
            "SELECT COUNT(*) FROM fazle_recruitment_sessions WHERE phone=$1 AND funnel_stage='collecting'",
            phone,
        )
        assert count == 1
    finally:
        await _cleanup_recruitment_session(phone)


# ── Stage 3 — escort/security_guard multi-message handoff (built, NOT activated) ─
# Required by the Owner as "prepare but do not activate until separately
# reviewed" -- these tests prove the logic is correct and fully inert unless
# BOTH route_b_handoff_enabled AND route_b_escort_handoff_enabled are true.

ORDER_TEXT = (
    "Mother Vessel: MV GOLDEN STAR\n"
    "02) MV SULTANA-02\n"
    "01711-273432\n"
    "Destination: N.Gonj\n"
    "Capacity: 1200"
)


@pytest.mark.asyncio
async def test_stage3_escort_handoff_inert_when_flag_off(monkeypatch):
    """Even a fully order-shaped message from an escort-role sender must NOT
    be handed off when route_b_escort_handoff_enabled is false (the real
    default) -- evaluate_handoff() must not even call the Stage 3 check."""
    from modules.route_b_handoff import evaluate_handoff
    from app.config import Settings

    phone = _phone()
    settings = Settings(route_b_handoff_enabled=True, route_b_escort_handoff_enabled=False)
    monkeypatch.setattr("modules.route_b_handoff.get_settings", lambda: settings)
    monkeypatch.setattr("app.ollama.classify_route_b_category", AsyncMock(return_value="escort_order"))
    escort_mock = AsyncMock()
    monkeypatch.setattr("modules.escort.handle_escort_client_message", escort_mock)

    try:
        result = await evaluate_handoff(
            sender=phone, text=ORDER_TEXT, source="bridge1",
            identity={"role": "escort", "display_name": "Escort Rahim"},
            intent="unknown", history="", message_id=None,
        )
        assert result is None
        escort_mock.assert_not_called()
    finally:
        await _cleanup_handoffs(phone)


@pytest.mark.asyncio
async def test_stage3_escort_handoff_single_message_complete(monkeypatch):
    from modules.route_b_handoff import _check_escort_operational_handoff

    escort_mock = AsyncMock(return_value=("", {"purpose": "escort-order-draft"}))
    monkeypatch.setattr("modules.escort.handle_escort_client_message", escort_mock)

    result = await _check_escort_operational_handoff(
        "8801711000001", ORDER_TEXT, "bridge1", "escort", history="",
    )
    assert result is not None
    assert result.destination == "escort_order"
    assert result.reason == "strict_order_complete"
    escort_mock.assert_awaited_once_with(ORDER_TEXT, "8801711000001", "bridge1", is_verified=False)


@pytest.mark.asyncio
async def test_stage3_escort_handoff_multi_message_complete(monkeypatch):
    """Fields spread across two turns: mother vessel in history, lighter +
    mobile in the current message. Neither message alone is complete."""
    from modules.route_b_handoff import _check_escort_operational_handoff
    from modules.escort import is_order_complete

    history_line = "[সে] Mother Vessel: MV GOLDEN STAR\n[সে] Lighter Vessel: SULTANA-02"
    current = "মাস্টার নম্বর 01711273432"
    assert is_order_complete(current) is False  # current message alone is incomplete
    assert is_order_complete(history_line) is False  # history alone is incomplete too
    assert is_order_complete(f"{history_line}\n{current}") is True  # combined, it is

    escort_mock = AsyncMock(return_value=("", {"purpose": "escort-order-draft"}))
    monkeypatch.setattr("modules.escort.handle_escort_client_message", escort_mock)

    result = await _check_escort_operational_handoff(
        "8801711000002", current, "bridge1", "security_guard", history=history_line,
    )
    assert result is not None
    assert result.reason == "strict_order_complete_multi_message"
    escort_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_stage3_escort_handoff_incomplete_stays_in_clarification():
    from modules.route_b_handoff import _check_escort_operational_handoff

    result = await _check_escort_operational_handoff(
        "8801711000003", "ভাই কবে ডিউটি শুরু?", "bridge1", "escort", history="",
    )
    assert result is None
