"""Offline adapter and mocked-dispatch boundary tests."""

from dataclasses import replace

from modules.identity_role_authorization import (
    ALLOWED,
    ALLOWED_PROTECTED_PURPOSE,
    AUTHORIZATION_LOOKUP_FAILED,
    CHANNEL_RESTRICTED,
    CONTEXT_MISMATCH,
    CONTEXT_MISSING,
    CONVERSATION_HOLD,
    CUSTOMER_AUTO_REPLY,
    DOMAIN_RESTRICTED,
    GLOBAL_MAINTENANCE,
    IDENTITY_HOLD,
    IDENTITY_MISSING,
    LEGACY_QUEUE_CONTEXT,
    PROTECTED_ADMIN_ALERT,
    ROLE_NOT_AUTHORIZED,
    VERSION_MISMATCH,
    IdentityEvidence,
    QueuedAuthorizationSnapshot,
    RoleGrant,
)
from modules.identity_role_authorization_adapter import (
    AuthorizationEvidence,
    BridgeAuthorizationState,
    authorize,
    build_authorization_context,
)


class MockTransport:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.calls = []

    def send(self, channel, recipient):
        self.calls.append((channel, recipient))
        if self.fail:
            raise RuntimeError("synthetic transport failure")


def _queue(*, identity_id="identity-1", version="v1", domain="recruitment", conversation="conversation-1", role_names=("candidate",), review=False):
    return {
        "id": 9001,
        "recipient": "synthetic-recipient",
        "source_bridge": "bridge1",
        "purpose": "customer-reply:recruitment",
        "meta_json": {
            "authorization": {
                "identity_id": identity_id,
                "authorization_version": version,
                "role_names": list(role_names),
                "domain": domain,
                "conversation_key": conversation,
                "scope_type": "global",
                "scope_key": None,
                "requires_manual_review": review,
            }
        },
    }


def _evidence(**changes):
    base = AuthorizationEvidence(
        identity={"identity_id": "identity-1", "verification_state": "verified"},
        channel_identifier="bridge1:synthetic",
        conversation_key="conversation-1",
        domain="recruitment",
        purpose=CUSTOMER_AUTO_REPLY,
        current_roles=(RoleGrant("candidate", frozenset({"recruitment"})),),
        authorization_version="v1",
        queue=_queue(),
    )
    return replace(base, **changes)


def _dispatch(evidence, transport, *, fallback=None):
    decision = authorize(evidence)
    if not decision.allowed:
        return decision
    try:
        transport.send(evidence.bridge, "synthetic-recipient")
    except RuntimeError:
        if fallback is None:
            raise
        fallback_decision = authorize(fallback)
        if fallback_decision.allowed:
            transport.send(fallback.bridge, "synthetic-recipient")
            return fallback_decision
        return fallback_decision
    return decision


def test_valid_current_identity_and_role_authorizes_one_mock_send():
    transport = MockTransport()
    decision = _dispatch(_evidence(), transport)
    assert decision.reason_code == ALLOWED
    assert transport.calls == [(BridgeAuthorizationState(), "synthetic-recipient")]


def test_multiple_roles_are_selected_by_domain():
    evidence = _evidence(
        current_roles=(
            RoleGrant("candidate", frozenset({"recruitment"})),
            RoleGrant("vendor", frozenset({"procurement"})),
        )
    )
    assert authorize(evidence).reason_code == ALLOWED

    procurement = replace(
        evidence,
        domain="procurement",
        queue=_queue(domain="procurement", role_names=("vendor",)),
    )
    assert authorize(procurement).reason_code == ALLOWED


def test_identity_mismatch_is_denied():
    decision = authorize(_evidence(identity={"identity_id": "identity-2", "verification_state": "verified"}))
    assert decision.reason_code == CONTEXT_MISMATCH


def test_conversation_mismatch_is_denied():
    decision = authorize(_evidence(conversation_key="conversation-2"))
    assert decision.reason_code == CONTEXT_MISMATCH


def test_domain_mismatch_is_denied():
    decision = authorize(_evidence(domain="escort"))
    assert decision.reason_code == CONTEXT_MISMATCH


def test_missing_and_stale_authorization_versions_fail_closed():
    missing = authorize(_evidence(authorization_version=None, queue=_queue(version=None)))
    assert missing.reason_code == CONTEXT_MISSING

    stale = authorize(_evidence(queue=_queue(version="old")))
    assert stale.reason_code == VERSION_MISMATCH
    assert stale.manual_review_required is True


def test_legacy_queue_without_authorization_context_requires_review():
    decision = authorize(_evidence(queue={"id": 9001, "meta_json": {}}))
    assert decision.reason_code == LEGACY_QUEUE_CONTEXT
    assert decision.manual_review_required is True


def test_identity_hold_and_legacy_phone_block_are_identity_restrictions():
    assert authorize(_evidence(identity_hold_active=True)).reason_code == IDENTITY_HOLD
    assert authorize(_evidence(phone_block_active=True)).reason_code == IDENTITY_HOLD


def test_conversation_hold_is_denied_only_for_the_selected_context():
    held = authorize(_evidence(conversation_hold_active=True))
    assert held.reason_code == CONVERSATION_HOLD

    other = _evidence(
        conversation_key="conversation-2",
        queue=_queue(conversation="conversation-2"),
    )
    assert authorize(other).reason_code == ALLOWED


def test_global_maintenance_precedes_role_and_hold_checks():
    decision = authorize(_evidence(global_outbound_enabled=False, identity_hold_active=True))
    assert decision.reason_code == GLOBAL_MAINTENANCE


def test_bridge_pairing_or_send_restriction_precedes_transport():
    pairing = authorize(
        _evidence(bridge=BridgeAuthorizationState(pairing_held=True))
    )
    assert pairing.reason_code == "bridge_pairing_hold"

    disabled = authorize(
        _evidence(bridge=BridgeAuthorizationState(send_enabled=False))
    )
    assert disabled.reason_code == CHANNEL_RESTRICTED


def test_domain_auto_reply_restriction_is_scoped_to_customer_purpose():
    customer = authorize(_evidence(domain_auto_reply_enabled=False))
    assert customer.reason_code == DOMAIN_RESTRICTED

    protected = _protected()
    protected = replace(protected, domain_auto_reply_enabled=False)
    assert authorize(protected).reason_code == ALLOWED_PROTECTED_PURPOSE


def test_missing_role_evidence_and_scalar_legacy_role_do_not_authorize():
    assert authorize(_evidence(current_roles=())).reason_code == ROLE_NOT_AUTHORIZED

    scalar = _evidence(
        identity={
            "identity_id": "identity-1",
            "verification_state": "verified",
            "identity_role": "candidate",
        },
        current_roles=("candidate",),
    )
    assert authorize(scalar).reason_code == ROLE_NOT_AUTHORIZED


def test_authorization_lookup_failure_fails_closed():
    decision = authorize(_evidence(authorization_state_available=False))
    assert decision.reason_code == AUTHORIZATION_LOOKUP_FAILED
    assert decision.manual_review_required is True


def _protected():
    return _evidence(
        identity={"identity_id": "identity-1", "verification_state": "verified"},
        conversation_key=None,
        domain=None,
        purpose=PROTECTED_ADMIN_ALERT,
        current_roles=(),
        purpose_authorized=True,
        queue=_queue(domain=None, conversation=None, role_names=()),
    )


def test_protected_alert_keeps_purpose_authorization_and_recipient_checks():
    decision = authorize(_protected())
    assert decision.reason_code == ALLOWED_PROTECTED_PURPOSE

    no_purpose_authorization = authorize(replace(_protected(), purpose_authorized=False))
    assert no_purpose_authorization.reason_code == "protected_purpose_unauthorized"

    missing_recipient = authorize(replace(_protected(), identity=None))
    assert missing_recipient.reason_code == IDENTITY_MISSING

    legacy_queue = authorize(replace(_protected(), queue={"meta_json": {}}))
    assert legacy_queue.reason_code == LEGACY_QUEUE_CONTEXT

    held = authorize(replace(_protected(), phone_block_active=True))
    assert held.reason_code == IDENTITY_HOLD


def test_fallback_channel_is_revalidated_before_second_mock_send():
    transport = MockTransport(fail=True)
    fallback = _evidence(
        bridge=BridgeAuthorizationState(pairing_held=True),
    )
    decision = _dispatch(_evidence(), transport, fallback=fallback)
    assert decision.reason_code == "bridge_pairing_hold"
    assert len(transport.calls) == 1


def test_denied_authorization_produces_zero_mock_transport_calls():
    transport = MockTransport()
    decision = _dispatch(_evidence(identity_hold_active=True), transport)
    assert decision.allowed is False
    assert transport.calls == []


def test_role_change_does_not_release_queued_message():
    changed = _evidence(
        authorization_version="v2",
        current_roles=(RoleGrant("employee", frozenset({"recruitment"})),),
    )
    decision = authorize(changed)
    assert decision.reason_code == VERSION_MISMATCH
    assert changed.queue["meta_json"]["authorization"]["authorization_version"] == "v1"


def test_hold_release_does_not_auto_dispatch_queued_message():
    released = _evidence(
        phone_block_active=False,
        queue=_queue(review=True),
    )
    transport = MockTransport()
    decision = _dispatch(released, transport)
    assert decision.reason_code == "manual_review_required"
    assert transport.calls == []


def test_adapter_maps_explicit_evidence_without_live_lookups():
    context = build_authorization_context(_evidence(phone_block_active=True))
    assert context.dispatch.identity_hold_active is True
    assert context.identity.identity_id == "identity-1"
    assert context.queued.authorization_version == "v1"
    assert context.queued.conversation_key == "conversation-1"
    assert context.current_roles[0].role == "candidate"
