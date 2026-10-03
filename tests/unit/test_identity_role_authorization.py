"""Offline contract tests for the pure Identity–Role evaluator."""

from dataclasses import replace

import pytest

from modules.identity_role_authorization import (
    ALLOWED,
    ALLOWED_PROTECTED_PURPOSE,
    AuthorizationContext,
    CHANNEL_RESTRICTED,
    CONTEXT_MISSING,
    CONTEXT_MISMATCH,
    CUSTOMER_AUTO_REPLY,
    CONVERSATION_HOLD,
    CURRENT_ROLE_NOT_REVALIDATED,
    DOMAIN_RESTRICTED,
    DispatchState,
    GLOBAL_MAINTENANCE,
    IDENTITY_AMBIGUOUS,
    IDENTITY_HOLD,
    IDENTITY_MISSING,
    IDENTITY_UNVERIFIED,
    IdentityEvidence,
    LEGACY_QUEUE_CONTEXT,
    MANUAL_REVIEW_REQUIRED,
    PROTECTED_ADMIN_ALERT,
    PROTECTED_PURPOSE_UNAUTHORIZED,
    QueuedAuthorizationSnapshot,
    ROLE_NOT_AUTHORIZED,
    RoleGrant,
    VERSION_MISMATCH,
    evaluate,
)


def grant(role="candidate", domain="recruitment", *, scope_type="global", scope_key=None, active=True):
    return RoleGrant(
        role=role,
        domains=frozenset({domain}),
        scope_type=scope_type,
        scope_key=scope_key,
        active=active,
    )


def context(*roles, **changes):
    current_roles = roles or (grant(),)
    base = AuthorizationContext(
        identity=IdentityEvidence("identity-1", "verified"),
        channel_identifier="channel-1",
        conversation_key="conversation-1",
        domain="recruitment",
        current_roles=tuple(current_roles),
        current_authorization_version="v1",
        queued=QueuedAuthorizationSnapshot(
            identity_id="identity-1",
            authorization_version="v1",
            role_names=frozenset({"candidate"}),
            domain="recruitment",
            conversation_key="conversation-1",
        ),
    )
    result = replace(base, **changes)
    if "queued" not in changes:
        result = replace(
            result,
            queued=replace(
                result.queued,
                identity_id=(result.identity.identity_id if result.identity else None),
                authorization_version=result.current_authorization_version,
                domain=result.domain,
                scope_type=result.scope_type,
                scope_key=result.scope_key,
            ),
        )
    return result


def test_multiple_roles_and_domains_are_evaluated_independently():
    recruitment = context(
        grant("candidate", "recruitment"),
        grant("vendor", "procurement"),
    )
    assert evaluate(recruitment).reason_code == ALLOWED

    procurement = replace(
        recruitment,
        domain="procurement",
        queued=replace(recruitment.queued, domain="procurement", role_names=frozenset({"vendor"})),
    )
    assert evaluate(procurement).reason_code == ALLOWED

    revoked_candidate = replace(recruitment, current_roles=(grant("candidate", "recruitment", active=False), grant("vendor", "procurement")))
    assert evaluate(revoked_candidate).reason_code == ROLE_NOT_AUTHORIZED


def test_scoped_role_must_match_domain_and_scope():
    scoped = context(grant("candidate", scope_type="organization", scope_key="org-1"), scope_type="organization", scope_key="org-1")
    assert evaluate(scoped).reason_code == ALLOWED

    other_scope = replace(scoped, scope_key="org-2", queued=replace(scoped.queued, scope_key="org-2"))
    assert evaluate(other_scope).reason_code == ROLE_NOT_AUTHORIZED


def test_identity_and_conversation_holds_are_isolated():
    held_identity = context(dispatch=DispatchState(identity_hold_active=True))
    assert evaluate(held_identity).reason_code == IDENTITY_HOLD

    other_identity = replace(
        held_identity,
        identity=IdentityEvidence("identity-2", "verified"),
        queued=replace(held_identity.queued, identity_id="identity-2"),
        dispatch=DispatchState(),
    )
    assert evaluate(other_identity).reason_code == ALLOWED

    held_conversation = context(dispatch=DispatchState(conversation_hold_active=True))
    assert evaluate(held_conversation).reason_code == CONVERSATION_HOLD

    other_conversation = replace(
        held_conversation,
        conversation_key="conversation-2",
        queued=replace(held_conversation.queued, conversation_key="conversation-2"),
        dispatch=DispatchState(),
    )
    assert evaluate(other_conversation).reason_code == ALLOWED


def test_role_change_does_not_release_hold_and_release_needs_review():
    changed_role = context(
        grant("employee", "recruitment"),
        dispatch=DispatchState(identity_hold_active=True),
    )
    assert evaluate(changed_role).reason_code == IDENTITY_HOLD

    released_hold = replace(
        changed_role,
        dispatch=DispatchState(),
        queued=replace(changed_role.queued, requires_manual_review=True),
    )
    assert evaluate(released_hold).reason_code == MANUAL_REVIEW_REQUIRED


def test_global_maintenance_and_channel_restriction_precede_other_rules():
    maintenance = context(dispatch=DispatchState(global_outbound_enabled=False, identity_hold_active=True))
    assert evaluate(maintenance).reason_code == GLOBAL_MAINTENANCE

    restricted = context(dispatch=DispatchState(channel_send_enabled=False, identity_hold_active=True))
    assert evaluate(restricted).reason_code == CHANNEL_RESTRICTED


def test_domain_restriction_is_scoped_to_customer_auto_reply():
    restricted = context(dispatch=DispatchState(domain_auto_reply_enabled=False))
    assert evaluate(restricted).reason_code == DOMAIN_RESTRICTED

    other_domain = context(grant("employee", "attendance"), domain="attendance")
    other_domain = replace(other_domain, queued=replace(other_domain.queued, role_names=frozenset({"employee"})))
    assert evaluate(other_domain).reason_code == ALLOWED

    protected = replace(
        restricted,
        identity=IdentityEvidence("identity-1", "verified"),
        conversation_key=None,
        domain=None,
        current_roles=(),
        current_authorization_version="v1",
        queued=QueuedAuthorizationSnapshot(
            identity_id="identity-1",
            authorization_version="v1",
            domain=None,
        ),
        purpose=PROTECTED_ADMIN_ALERT,
        purpose_authorized=True,
    )
    decision = evaluate(protected)
    assert decision.allowed is True
    assert decision.reason_code == ALLOWED_PROTECTED_PURPOSE


@pytest.mark.parametrize(
    ("identity", "reason"),
    [
        (None, IDENTITY_MISSING),
        (IdentityEvidence("identity-1", "verified", ambiguous=True), IDENTITY_AMBIGUOUS),
        (IdentityEvidence("identity-1", "display_name"), IDENTITY_UNVERIFIED),
    ],
)
def test_identity_evidence_fails_closed(identity, reason):
    assert evaluate(context(identity=identity)).reason_code == reason


def test_missing_context_and_legacy_queue_require_review():
    missing = context(channel_identifier=None)
    assert evaluate(missing).reason_code == CONTEXT_MISSING

    legacy = context(queued=QueuedAuthorizationSnapshot.legacy())
    decision = evaluate(legacy)
    assert decision.reason_code == LEGACY_QUEUE_CONTEXT
    assert decision.manual_review_required is True


def test_snapshot_identity_and_version_must_match_current_state():
    mismatched_identity = context(queued=replace(context().queued, identity_id="identity-2"))
    assert evaluate(mismatched_identity).reason_code == CONTEXT_MISMATCH

    stale = context(queued=replace(context().queued, authorization_version="old"))
    decision = evaluate(stale)
    assert decision.reason_code == VERSION_MISMATCH
    assert decision.manual_review_required is True


def test_old_role_cannot_authorize_current_dispatch_even_with_same_version():
    stale_role = context(
        grant("employee", "recruitment"),
        queued=replace(context().queued, role_names=frozenset({"candidate"})),
    )
    assert evaluate(stale_role).reason_code == CURRENT_ROLE_NOT_REVALIDATED


def test_protected_purpose_requires_its_own_authorization():
    unauthorized = context(
        identity=IdentityEvidence("identity-1", "verified"),
        conversation_key=None,
        domain=None,
        current_roles=(),
        current_authorization_version="v1",
        queued=QueuedAuthorizationSnapshot(
            identity_id="identity-1",
            authorization_version="v1",
            domain=None,
        ),
        purpose=PROTECTED_ADMIN_ALERT,
    )
    assert evaluate(unauthorized).reason_code == PROTECTED_PURPOSE_UNAUTHORIZED


def test_decisions_are_deterministic_and_do_not_include_personal_data():
    first = evaluate(context())
    second = evaluate(context())
    assert first == second
    assert "identity-1" not in repr(first)
    assert "channel-1" not in repr(first)
    assert "conversation-1" not in repr(first)
