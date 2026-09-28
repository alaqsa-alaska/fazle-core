"""Non-sending adapter from existing Fazle-Core evidence to IR-2A.

The adapter is deliberately synchronous and side-effect free.  Callers supply
already-resolved identity, queue, bridge, hold, and domain evidence.  It does
not query settings or PostgreSQL, invoke the bridge, or infer authorization
from a legacy scalar role or display name.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from modules.identity_role_authorization import (
    CHANNEL_RESTRICTED,
    CUSTOMER_AUTO_REPLY,
    AuthorizationContext,
    AuthorizationDecision,
    DispatchState,
    IdentityEvidence,
    QueuedAuthorizationSnapshot,
    RoleGrant,
    evaluate,
)


@dataclass(frozen=True)
class BridgeAuthorizationState:
    """Explicit bridge evidence collected by a future caller.

    ``pairing_held`` represents the existing bridge pairing gate.  The adapter
    never calls ``_bridge_outbound_held`` because that would make this package
    live-service dependent.
    """

    send_enabled: bool = True
    pairing_held: bool = False
    restriction_reason: str = CHANNEL_RESTRICTED


@dataclass(frozen=True)
class AuthorizationEvidence:
    """Current-state evidence for one prospective dispatch decision."""

    identity: Mapping[str, Any] | IdentityEvidence | None
    channel_identifier: str | None
    conversation_key: str | None
    domain: str | None
    purpose: str = CUSTOMER_AUTO_REPLY
    current_roles: tuple[RoleGrant, ...] = ()
    authorization_version: str | int | None = None
    queue: Mapping[str, Any] | QueuedAuthorizationSnapshot | None = None
    global_outbound_enabled: bool = True
    bridge: BridgeAuthorizationState = field(default_factory=BridgeAuthorizationState)
    phone_block_active: bool = False
    identity_hold_active: bool = False
    conversation_hold_active: bool = False
    domain_auto_reply_enabled: bool = True
    manual_review_required: bool = False
    purpose_authorized: bool = False
    authorization_state_available: bool = True
    scope_type: str = "global"
    scope_key: str | None = None


def _identity(value: Mapping[str, Any] | IdentityEvidence | None) -> IdentityEvidence | None:
    if isinstance(value, IdentityEvidence):
        return value
    if not isinstance(value, Mapping):
        return None

    # identity_phone, display_name, and identity_role are intentionally not
    # fallbacks for identity_id or verification state.
    identity_id = value.get("identity_id")
    if identity_id is not None:
        identity_id = str(identity_id)
    verification_state = value.get("verification_state")
    if verification_state is None:
        verification_state = value.get("identity_verification_state", "unknown")
    return IdentityEvidence(
        identity_id=identity_id,
        verification_state=str(verification_state),
        ambiguous=bool(value.get("ambiguous", value.get("identity_ambiguous", False))),
    )


def _roles(value: object) -> tuple[RoleGrant, ...]:
    """Keep only explicit RoleGrant values; scalar legacy roles are ignored."""
    if isinstance(value, (str, bytes, Mapping)) or value is None:
        return ()
    try:
        return tuple(item for item in value if isinstance(item, RoleGrant))
    except TypeError:
        return ()


def _mapping(value: object) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return None
        return parsed if isinstance(parsed, Mapping) else None
    return None


def _role_names(value: object) -> frozenset[str]:
    if isinstance(value, str) or value is None:
        return frozenset()
    try:
        return frozenset(str(item) for item in value)
    except TypeError:
        return frozenset()


def _queue_snapshot(
    value: Mapping[str, Any] | QueuedAuthorizationSnapshot | None,
) -> QueuedAuthorizationSnapshot:
    if isinstance(value, QueuedAuthorizationSnapshot):
        return value
    if not isinstance(value, Mapping):
        return QueuedAuthorizationSnapshot.legacy()

    # The live queue currently has meta_json but no authorization columns.
    # Only an explicit future authorization object is accepted as context.
    authorization = value.get("authorization_context") or value.get("authorization")
    if authorization is None:
        metadata = _mapping(value.get("meta_json"))
        if metadata:
            authorization = metadata.get("authorization_context") or metadata.get("authorization")
    authorization = _mapping(authorization)
    if authorization is None:
        return QueuedAuthorizationSnapshot.legacy()

    identity_id = authorization.get("identity_id")
    return QueuedAuthorizationSnapshot(
        present=bool(authorization.get("present", True)),
        identity_id=str(identity_id) if identity_id is not None else None,
        authorization_version=authorization.get("authorization_version"),
        role_names=_role_names(authorization.get("role_names")),
        domain=authorization.get("domain"),
        conversation_key=authorization.get("conversation_key"),
        scope_type=str(authorization.get("scope_type", "global")),
        scope_key=authorization.get("scope_key"),
        requires_manual_review=bool(authorization.get("requires_manual_review", False)),
    )


def build_authorization_context(evidence: AuthorizationEvidence) -> AuthorizationContext:
    """Map explicit existing-state evidence into the IR-2A context."""
    bridge = evidence.bridge
    channel_send_enabled = bridge.send_enabled and not bridge.pairing_held
    restriction_reason = (
        "bridge_pairing_hold" if bridge.pairing_held else bridge.restriction_reason
    )
    return AuthorizationContext(
        identity=_identity(evidence.identity),
        channel_identifier=evidence.channel_identifier,
        conversation_key=evidence.conversation_key,
        domain=evidence.domain,
        current_roles=_roles(evidence.current_roles),
        current_authorization_version=evidence.authorization_version,
        authorization_state_available=evidence.authorization_state_available,
        queued=_queue_snapshot(evidence.queue),
        dispatch=DispatchState(
            global_outbound_enabled=evidence.global_outbound_enabled,
            channel_send_enabled=channel_send_enabled,
            channel_restriction_reason=restriction_reason,
            identity_hold_active=(
                evidence.identity_hold_active or evidence.phone_block_active
            ),
            conversation_hold_active=evidence.conversation_hold_active,
            domain_auto_reply_enabled=evidence.domain_auto_reply_enabled,
            manual_review_required=evidence.manual_review_required,
        ),
        purpose=evidence.purpose,
        purpose_authorized=evidence.purpose_authorized,
        scope_type=evidence.scope_type,
        scope_key=evidence.scope_key,
    )


def authorize(evidence: AuthorizationEvidence) -> AuthorizationDecision:
    """Build and evaluate one decision without queue or transport effects."""
    return evaluate(build_authorization_context(evidence))


__all__ = [
    "AuthorizationEvidence",
    "BridgeAuthorizationState",
    "authorize",
    "build_authorization_context",
]
