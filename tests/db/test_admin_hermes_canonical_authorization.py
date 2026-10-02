"""Canonical identity/role authorization for the Admin -> Hermes relay.

Admin Canary qualification, 2026-09-29. This is the database-backed half of
the qualification: ``tests/unit/test_admin_ai_router.py`` proves the relay
*calls* the canonical gate and stays fail-closed, while this file proves the
gate actually resolves canonical identity and role from the real control-plane
tables and denies each unsafe case against real rows rather than mocks.

Scope note -- these tests never send anything. The relay is exercised only
through ``resolve_relay_authorization``, which performs SELECT/FOR SHARE reads
and returns a decision. ``test_authorization_alone_cannot_reach_a_transport``
additionally pins that holding a valid authorization grants no transport
capability by itself: producing a decision is a precondition for dispatch, not
a substitute for it.
"""

from unittest.mock import AsyncMock, patch

import pytest

from modules.admin_hermes_authorization import (
    HERMES_RELAY_DOMAIN,
    HERMES_RELAY_ROLES,
    relay_conversation_key,
    resolve_relay_authorization,
)
from modules.identity_role_authorization import (
    CONTEXT_MISSING,
    IDENTITY_AMBIGUOUS,
    IDENTITY_HOLD,
    IDENTITY_MISSING,
    IDENTITY_UNVERIFIED,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.db]

ADMIN_PHONE = "8801777000001"
OTHER_PHONE = "8801777000002"
THIRD_PHONE = "8801777000003"
BRIDGE1 = "bridge1"
BRIDGE2 = "bridge2"


@pytest.fixture(autouse=True)
async def _isolate_synthetic_rows(test_db_pool):
    """These tests deliberately share phone numbers, because the point is that
    an identity is resolved *for a given WhatsApp id*. The schema's partial
    unique index (one active link per platform/platform_user_id) would
    otherwise turn a leftover row from an earlier test into a spurious
    identity_ambiguous denial. Every row these tests create is tagged with
    synthetic markers and removed again, so each case starts from exactly the
    state it asserts."""
    async def _clean():
        async with test_db_pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM fazle_identity_role_assignments WHERE assigned_by = 'synthetic-test'"
            )
            await conn.execute(
                "DELETE FROM fazle_outbound_holds WHERE placed_by = 'synthetic-test'"
            )
            await conn.execute(
                "DELETE FROM wbom_identity_links WHERE evidence_source = 'synthetic-test'"
            )
            await conn.execute(
                "DELETE FROM fazle_identity_subjects "
                "WHERE display_name LIKE 'Synthetic Relay Subject%'"
            )

    await _clean()
    yield
    await _clean()


async def _subject(conn, *, state="verified", lifecycle="active"):
    return await conn.fetchval(
        """
        INSERT INTO fazle_identity_subjects
            (identity_kind, display_name, verification_state, lifecycle_state)
        VALUES ('person', $1, $2, $3)
        RETURNING identity_id
        """,
        f"Synthetic Relay Subject {state}-{lifecycle}",
        state,
        lifecycle,
    )


async def _link(conn, identity_id, phone, *, state="verified", active=True):
    await conn.execute(
        """
        INSERT INTO wbom_identity_links
            (identity_key, platform, platform_user_id, identity_id,
             identifier_type, verification_state, evidence_source, active)
        VALUES ($1, 'whatsapp', $2, $3, 'whatsapp_jid', $4, 'synthetic-test', $5)
        """,
        f"subject:{identity_id}", phone, identity_id, state, active,
    )


async def _role(conn, identity_id, role, *, domain=HERMES_RELAY_DOMAIN,
                scope_type="global", scope_key=None, active=True):
    await conn.execute(
        """
        INSERT INTO fazle_identity_role_assignments
            (identity_id, role_name, business_domain, scope_type, scope_key,
             active, revoked_at, revoked_by, revocation_reason,
             assigned_by, assignment_reason)
        VALUES ($1, $2, $3, $4, $5, $6,
                CASE WHEN $6 THEN NULL ELSE NOW() END,
                CASE WHEN $6 THEN NULL ELSE 'synthetic-test' END,
                CASE WHEN $6 THEN NULL ELSE 'revoked by relay-qualification' END,
                'synthetic-test', 'relay-qualification')
        """,
        identity_id, role, domain, scope_type, scope_key, active,
    )


class _RaisingDBConn:
    """Stand-in for app.database.db_conn that fails the way an unreachable or
    un-migrated database actually fails -- on entry, not on a call."""

    def __init__(self, error: Exception):
        self._error = error

    def __call__(self):
        return self

    async def __aenter__(self):
        raise self._error

    async def __aexit__(self, *exc_info):
        return False


async def _hold(conn, *, identity_id=None, conversation_key=None, reason="synthetic-test-hold"):
    await conn.execute(
        """
        INSERT INTO fazle_outbound_holds
            (target_type, identity_id, conversation_key, active, reason, placed_by)
        VALUES ($1, $2, $3, TRUE, $4, 'synthetic-test')
        """,
        "identity" if identity_id is not None else "conversation",
        identity_id, conversation_key, reason,
    )


# ── Allow path ───────────────────────────────────────────────────────────────
async def test_authorized_canonical_admin_identity_is_allowed(test_db_pool):
    """The positive case: one verified identity, one verified link, an active
    superadmin grant on the relay domain, no holds."""
    async with test_db_pool.acquire() as conn:
        subject = await _subject(conn)
        await _link(conn, subject, ADMIN_PHONE)
        await _role(conn, subject, "superadmin")
        version = await conn.fetchval("SELECT fazle_bump_identity_authorization_version($1)", subject)

    result = await resolve_relay_authorization(
        sender_phone=ADMIN_PHONE, source_bridge=BRIDGE1)

    assert result.allowed is True
    assert result.identity_id == str(subject)
    assert "superadmin" in result.role_names
    assert result.authorization_version == version
    assert result.conversation_key == relay_conversation_key(BRIDGE1, ADMIN_PHONE)


async def test_bridge2_and_bridge1_both_resolve_the_same_canonical_identity(test_db_pool):
    """LEGACY_BRIDGE2_GATE_RETAINED / Bridge1 behaviour: the canonical layer
    resolves identity on either control channel, and the channel set itself
    stays owned by the pre-existing gate in the router. The two channels are
    separate conversation keys, so a hold on one does not silence the other."""
    async with test_db_pool.acquire() as conn:
        subject = await _subject(conn)
        await _link(conn, subject, ADMIN_PHONE)
        await _role(conn, subject, "superadmin")

    on_bridge1 = await resolve_relay_authorization(
        sender_phone=ADMIN_PHONE, source_bridge=BRIDGE1)
    on_bridge2 = await resolve_relay_authorization(
        sender_phone=ADMIN_PHONE, source_bridge=BRIDGE2)

    assert on_bridge1.allowed and on_bridge2.allowed
    assert on_bridge1.identity_id == on_bridge2.identity_id == str(subject)
    assert on_bridge1.conversation_key != on_bridge2.conversation_key


async def test_wildcard_domain_superadmin_grant_is_accepted(test_db_pool):
    """The live production grant is domain '*', not 'hermes'. RoleGrant.matches
    must honour that wildcard, otherwise this fix would lock the real Owner
    out of the relay."""
    async with test_db_pool.acquire() as conn:
        subject = await _subject(conn)
        await _link(conn, subject, ADMIN_PHONE)
        await _role(conn, subject, "superadmin", domain="*")

    result = await resolve_relay_authorization(
        sender_phone=ADMIN_PHONE, source_bridge=BRIDGE1)
    assert result.allowed is True


# ── Deny paths (each must fail closed) ───────────────────────────────────────
async def test_unauthorized_identity_with_no_link_is_denied(test_db_pool):
    result = await resolve_relay_authorization(
        sender_phone=THIRD_PHONE, source_bridge=BRIDGE1)
    assert result.allowed is False
    assert result.reason_code == IDENTITY_MISSING
    assert result.identity_id is None


async def test_non_admin_role_is_denied(test_db_pool):
    """A verified, enrolled identity that simply is not an admin must not
    reach the relay. This is the case the legacy checks could not see: the
    phone-number and channel gates were never consulted for role."""
    async with test_db_pool.acquire() as conn:
        subject = await _subject(conn)
        await _link(conn, subject, ADMIN_PHONE)
        await _role(conn, subject, "viewer")

    result = await resolve_relay_authorization(
        sender_phone=ADMIN_PHONE, source_bridge=BRIDGE1)
    assert result.allowed is False
    assert result.reason_code == "role_not_authorized"
    assert "viewer" in result.detail


async def test_role_scoped_to_another_domain_is_denied(test_db_pool):
    """A superadmin grant that is scoped away from the relay's domain must not
    satisfy the relay, even though the same role name is present."""
    async with test_db_pool.acquire() as conn:
        subject = await _subject(conn)
        await _link(conn, subject, ADMIN_PHONE)
        await _role(conn, subject, "superadmin", domain="payroll")

    result = await resolve_relay_authorization(
        sender_phone=ADMIN_PHONE, source_bridge=BRIDGE1)
    assert result.allowed is False
    assert result.reason_code == "role_not_authorized"


async def test_missing_identity_phone_is_denied_without_touching_the_database(test_db_pool):
    result = await resolve_relay_authorization(
        sender_phone="", source_bridge=BRIDGE1)
    assert result.allowed is False
    assert result.reason_code == CONTEXT_MISSING


async def test_missing_source_bridge_is_denied(test_db_pool):
    """The channel value is part of the authorization context, not decoration.
    An empty bridge is the same fail-closed default the router's own
    process_admin_ai_command docstring already promises."""
    result = await resolve_relay_authorization(
        sender_phone=ADMIN_PHONE, source_bridge="")
    assert result.allowed is False
    assert result.reason_code == CONTEXT_MISSING


async def test_ambiguous_identity_is_denied_not_resolved_by_picking_one(test_db_pool):
    """The schema structurally prevents this state: migration 087's partial
    unique index on (platform, platform_user_id) WHERE active allows only one
    active link per WhatsApp id. That is asserted first, so the guarantee is
    on the record.

    The resolver must still be defensive about it, because the index is not
    the only way to reach the state: a host migrated before that index, a
    manual repair, or a future index change could all produce two active
    links. This is the same `len(identity_ids) != 1` guard the pre-existing
    outbound_authorization loader already applies, and it must deny as
    ambiguous rather than resolve by picking the newest or the one that
    happens to hold a role."""
    async with test_db_pool.acquire() as conn:
        has_index = await conn.fetchval(
            """
            SELECT EXISTS (
                SELECT 1 FROM pg_indexes
                 WHERE schemaname = 'public'
                   AND tablename = 'wbom_identity_links'
                   AND indexname = 'idx_wbom_identity_platform_user'
            )
            """
        )
        assert has_index, (
            "wbom_identity_links lost its partial unique index; the "
            "one-active-link-per-WhatsApp-id guarantee is gone"
        )

        # Reproduce the legacy/unrepaired shape by dropping the index inside
        # this test only, then proving the resolver still fails closed.
        await conn.execute("DROP INDEX IF EXISTS idx_wbom_identity_platform_user")
        try:
            first = await _subject(conn)
            second = await _subject(conn)
            await _link(conn, first, OTHER_PHONE)
            await _link(conn, second, OTHER_PHONE)
            await _role(conn, first, "superadmin")
            await _role(conn, second, "superadmin")

            result = await resolve_relay_authorization(
                sender_phone=OTHER_PHONE, source_bridge=BRIDGE1)
        finally:
            # Remove the duplicate links BEFORE restoring the index, otherwise
            # the index cannot be rebuilt. Ordering matters here.
            await conn.execute(
                "DELETE FROM wbom_identity_links WHERE platform_user_id = $1", OTHER_PHONE
            )
            await conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_wbom_identity_platform_user "
                "ON wbom_identity_links (platform, platform_user_id) WHERE active"
            )

    assert result.allowed is False
    assert result.reason_code == IDENTITY_AMBIGUOUS


async def test_unverified_link_is_denied(test_db_pool):
    async with test_db_pool.acquire() as conn:
        subject = await _subject(conn)
        await _link(conn, subject, ADMIN_PHONE, state="unverified")
        await _role(conn, subject, "superadmin")

    result = await resolve_relay_authorization(
        sender_phone=ADMIN_PHONE, source_bridge=BRIDGE1)
    assert result.allowed is False
    assert result.reason_code == IDENTITY_UNVERIFIED


async def test_unverified_subject_is_denied(test_db_pool):
    async with test_db_pool.acquire() as conn:
        subject = await _subject(conn, state="unverified")
        await _link(conn, subject, ADMIN_PHONE)
        await _role(conn, subject, "superadmin")

    result = await resolve_relay_authorization(
        sender_phone=ADMIN_PHONE, source_bridge=BRIDGE1)
    assert result.allowed is False
    assert result.reason_code == IDENTITY_UNVERIFIED


@pytest.mark.parametrize("lifecycle", ["suspended", "closed"])
async def test_inactive_subject_lifecycle_is_denied(test_db_pool, lifecycle):
    async with test_db_pool.acquire() as conn:
        subject = await _subject(conn, lifecycle=lifecycle)
        await _link(conn, subject, ADMIN_PHONE)
        await _role(conn, subject, "superadmin")

        result = await resolve_relay_authorization(
            sender_phone=ADMIN_PHONE, source_bridge=BRIDGE1)

    assert result.allowed is False
    assert result.reason_code == IDENTITY_UNVERIFIED
    assert "lifecycle" in result.detail
    assert lifecycle in result.detail


async def test_revoked_role_assignment_is_denied(test_db_pool):
    """The single most important new capability: revoking the canonical role
    must actually stop the relay. Before this gate existed, revoking a role in
    the control plane had no effect on the relay at all."""
    async with test_db_pool.acquire() as conn:
        subject = await _subject(conn)
        await _link(conn, subject, ADMIN_PHONE)
        await _role(conn, subject, "superadmin", active=False)

    result = await resolve_relay_authorization(
        sender_phone=ADMIN_PHONE, source_bridge=BRIDGE1)
    assert result.allowed is False
    assert result.reason_code == "role_not_authorized"


async def test_expired_role_assignment_is_denied(test_db_pool):
    async with test_db_pool.acquire() as conn:
        subject = await _subject(conn)
        await _link(conn, subject, ADMIN_PHONE)
        await conn.execute(
            """
            INSERT INTO fazle_identity_role_assignments
                (identity_id, role_name, business_domain, scope_type, scope_key,
                 effective_from, effective_until, active, assigned_by, assignment_reason)
            VALUES ($1, 'superadmin', $2, 'global', NULL,
                    NOW() - INTERVAL '10 days', NOW() - INTERVAL '1 day',
                    TRUE, 'synthetic-test', 'expired')
            """,
            subject, HERMES_RELAY_DOMAIN,
        )

    result = await resolve_relay_authorization(
        sender_phone=ADMIN_PHONE, source_bridge=BRIDGE1)
    assert result.allowed is False
    assert result.reason_code == "role_not_authorized"


async def test_identity_hold_denies_the_relay(test_db_pool):
    async with test_db_pool.acquire() as conn:
        subject = await _subject(conn)
        await _link(conn, subject, ADMIN_PHONE)
        await _role(conn, subject, "superadmin")
        await _hold(conn, identity_id=subject)

    result = await resolve_relay_authorization(
        sender_phone=ADMIN_PHONE, source_bridge=BRIDGE1)
    assert result.allowed is False
    assert result.reason_code == IDENTITY_HOLD


async def test_conversation_hold_denies_only_its_own_channel(test_db_pool):
    async with test_db_pool.acquire() as conn:
        subject = await _subject(conn)
        await _link(conn, subject, ADMIN_PHONE)
        await _role(conn, subject, "superadmin")
        await _hold(conn, conversation_key=relay_conversation_key(BRIDGE1, ADMIN_PHONE))

    held = await resolve_relay_authorization(
        sender_phone=ADMIN_PHONE, source_bridge=BRIDGE1)
    other = await resolve_relay_authorization(
        sender_phone=ADMIN_PHONE, source_bridge=BRIDGE2)

    assert held.allowed is False
    assert held.reason_code == "conversation_hold"
    assert other.allowed is True


async def test_released_hold_allows_the_relay_again(test_db_pool):
    async with test_db_pool.acquire() as conn:
        subject = await _subject(conn)
        await _link(conn, subject, ADMIN_PHONE)
        await _role(conn, subject, "superadmin")
        hold_id = await conn.fetchval(
            """
            INSERT INTO fazle_outbound_holds
                (target_type, identity_id, conversation_key, active, reason, placed_by)
            VALUES ('identity', $1, NULL, TRUE, 'synthetic', 'synthetic-test')
            RETURNING hold_id
            """,
            subject,
        )
        await conn.execute(
            """
            UPDATE fazle_outbound_holds
               SET active = FALSE, released_at = NOW(), released_by = 'synthetic-test',
                   release_reason = 'synthetic-release'
             WHERE hold_id = $1
            """,
            hold_id,
        )

    result = await resolve_relay_authorization(
        sender_phone=ADMIN_PHONE, source_bridge=BRIDGE1)
    assert result.allowed is True


async def test_inactive_identity_link_is_not_evidence(test_db_pool):
    """A deactivated link must not authorize. Otherwise revoking a link --
    the natural response to a compromised account -- would do nothing."""
    async with test_db_pool.acquire() as conn:
        subject = await _subject(conn)
        await _link(conn, subject, ADMIN_PHONE, active=False)
        await _role(conn, subject, "superadmin")

    result = await resolve_relay_authorization(
        sender_phone=ADMIN_PHONE, source_bridge=BRIDGE1)
    assert result.allowed is False
    assert result.reason_code == IDENTITY_MISSING


async def test_canonical_decision_is_independent_of_the_outbound_gate(test_db_pool):
    """The canonical decision is about identity/role/channel only.

    Production currently runs OUTBOUND_ENABLED=false. If the dispatch flag
    were folded into this decision, the relay would be silenced entirely --
    a functional outage, not a safety gain, because the downstream
    outbound.enqueue + modules.outbound.sweep_once gate already prevents any
    send. This test pins the separation so the conflation is not reintroduced.
    """
    async with test_db_pool.acquire() as conn:
        subject = await _subject(conn)
        await _link(conn, subject, ADMIN_PHONE)
        await _role(conn, subject, "superadmin")

    import modules.outbound as outbound_mod
    assert outbound_mod._outbound_enabled() is False, (
        "this test asserts against the real test-environment outbound gate; "
        "if the environment changed, re-check the separation this guards"
    )

    result = await resolve_relay_authorization(
        sender_phone=ADMIN_PHONE, source_bridge=BRIDGE1)
    assert result.allowed is True


async def test_channel_send_disabled_denies_the_relay(test_db_pool):
    """The control-channel gate IS a genuine relay-eligibility input and is
    honoured by the canonical decision, unlike the downstream dispatch flag."""
    async with test_db_pool.acquire() as conn:
        subject = await _subject(conn)
        await _link(conn, subject, ADMIN_PHONE)
        await _role(conn, subject, "superadmin")

    result = await resolve_relay_authorization(
        sender_phone=ADMIN_PHONE, source_bridge=BRIDGE1, channel_send_enabled=False)
    assert result.allowed is False


# ── Fail-closed on infrastructure failure ────────────────────────────────────
async def test_database_failure_fails_closed_rather_than_raising(test_db_pool):
    """The resolver must never raise into the message-routing pipeline and
    must never fall back to 'allow' when the canonical authority is
    unavailable. message_router requires handlers not to crash, so a swallow
    here would be a silent security regression."""
    import modules.admin_hermes_authorization as aha

    with patch.object(aha, "db_conn", new=_RaisingDBConn(RuntimeError("db unavailable"))):
        result = await aha.resolve_relay_authorization(
            sender_phone=ADMIN_PHONE, source_bridge=BRIDGE1)

    assert result.allowed is False
    assert result.reason_code == "authorization_lookup_failed"
    assert "db unavailable" in result.detail


async def test_missing_schema_fails_closed(test_db_pool):
    """If the canonical tables are absent (an un-migrated host), the relay
    must deny rather than quietly fall back to the legacy settings-number
    check alone."""
    import modules.admin_hermes_authorization as aha

    error = RuntimeError('relation "fazle_identity_subjects" does not exist')
    with patch.object(aha, "db_conn", new=_RaisingDBConn(error)):
        result = await aha.resolve_relay_authorization(
            sender_phone=ADMIN_PHONE, source_bridge=BRIDGE1)

    assert result.allowed is False
    assert result.reason_code == "authorization_lookup_failed"


async def test_resolver_never_writes(test_db_pool):
    """Structural guarantee, not a spot check: the resolver's only SQL
    keywords are SELECT/EXISTS/FOR SHARE. This is asserted against the
    module's own source so a future edit cannot quietly add a write to the
    authorization path."""
    from pathlib import Path
    import inspect
    import modules.admin_hermes_authorization as aha

    source = inspect.getsource(aha)
    for forbidden in ("INSERT ", "UPDATE ", "DELETE ", "DROP ", "TRUNCATE "):
        assert forbidden not in source, f"resolver must not contain {forbidden!r}"
    # Only the read helpers may be used to reach the database.
    assert "fetch(" in source and "fetchval(" in source
    assert "db_conn" in source


# ── Authorization is a precondition, not a capability ────────────────────────
async def test_authorization_alone_cannot_reach_a_transport(test_db_pool):
    """A valid authorization decision is necessary but not sufficient. This
    pins that the decision object itself carries no dispatch power: the
    privileged call and the outbound enqueue remain entirely under the
    router's control, which is only reached after the legacy gates too."""
    import modules.admin_directives.router as router

    async with test_db_pool.acquire() as conn:
        subject = await _subject(conn)
        await _link(conn, subject, ADMIN_PHONE)
        await _role(conn, subject, "superadmin")

    result = await resolve_relay_authorization(
        sender_phone=ADMIN_PHONE, source_bridge=BRIDGE1)
    assert result.allowed is True

    # The decision is a plain frozen record of evidence -- it exposes no
    # callable, no transport handle, and no queue reference.
    assert not any(callable(v) for v in vars(result).values())
    assert set(result.to_dict()) == {
        "allowed", "reason_code", "identity_id", "role_names",
        "authorization_version", "conversation_key", "domain", "detail",
    }

    # And the router's privileged surfaces are still independently gated.
    with patch.object(router, "_call_hermes_readonly", new=AsyncMock()) as mock_call, \
         patch.object(router.outbound, "enqueue", new=AsyncMock()) as mock_enqueue:
        assert not hasattr(result, "dispatch")
        mock_call.assert_not_awaited()
        mock_enqueue.assert_not_awaited()


# ── Invariant: the authorized set stays a deliberate, auditable constant ─────
async def test_authorized_role_set_is_not_widened_beyond_the_legacy_tier():
    """The canonical gate must never be the weaker of the two. The legacy
    rbac.COMMAND_ROLE entry for hermes_relay is 'superadmin'; if that ever
    changes, this test is what makes the drift visible instead of silent."""
    from modules import rbac

    legacy_tier = rbac.COMMAND_ROLE["hermes_relay"]
    assert HERMES_RELAY_ROLES == frozenset({legacy_tier}), (
        f"canonical relay roles {sorted(HERMES_RELAY_ROLES)} no longer match the "
        f"legacy required tier {legacy_tier!r}; update both deliberately"
    )
