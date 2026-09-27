"""Pure Identity–Role authorization decisions for future dispatch integration.

This module deliberately has no database, settings, transport, or queue
dependencies.  The later dispatcher must supply both the current authorization
state and the queued snapshot, then enforce the returned decision atomically
enough for its transport boundary.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import FrozenSet


# Purpose values are intentionally separate from domain auto-reply state.  A
# protected admin/system alert is not a customer auto-reply merely because it
# uses the same transport.
CUSTOMER_AUTO_REPLY = "customer_auto_reply"
PROTECTED_ADMIN_ALERT = "protected_admin_alert"
SYSTEM_ALERT = "system_alert"

VERIFIED = "verified"

ALLOWED = "allowed"
ALLOWED_PROTECTED_PURPOSE = "allowed_protected_purpose"

GLOBAL_MAINTENANCE = "global_maintenance"
CHANNEL_RESTRICTED = "channel_restricted"
IDENTITY_HOLD = "identity_hold"
CONVERSATION_HOLD = "conversation_hold"
DOMAIN_RESTRICTED = "domain_restricted"
IDENTITY_MISSING = "identity_missing"
IDENTITY_AMBIGUOUS = "identity_ambiguous"
IDENTITY_UNVERIFIED = "identity_unverified"
CONTEXT_MISSING = "authorization_context_missing"
LEGACY_QUEUE_CONTEXT = "legacy_queue_context"
CONTEXT_MISMATCH = "authorization_context_mismatch"
VERSION_MISMATCH = "authorization_version_mismatch"
MANUAL_REVIEW_REQUIRED = "manual_review_required"
PROTECTED_PURPOSE_UNAUTHORIZED = "protected_purpose_unauthorized"
ROLE_NOT_AUTHORIZED = "role_not_authorized"
CURRENT_ROLE_NOT_REVALIDATED = "current_role_not_revalidated"

GLOBAL = "global"


@dataclass(frozen=True)
class IdentityEvidence:
    """Already-resolved identity evidence supplied by an upstream resolver.

    ``verification_state`` is intentionally explicit.  A display name or
    scalar legacy role must not be promoted to verified identity evidence by
    this evaluator.
    """

    identity_id: str | None
    verification_state: str = "unknown"
    ambiguous: bool = False


@dataclass(frozen=True)
class RoleGrant:
    """One current role assignment and its domain/scope boundary."""

    role: str
    domains: FrozenSet[str] = field(default_factory=frozenset)
    scope_type: str = GLOBAL
    scope_key: str | None = None
    active: bool = True

    def matches(self, *, domain: str, scope_type: str, scope_key: str | None) -> bool:
        if not self.active:
            return False
        if domain not in self.domains and "*" not in self.domains:
            return False
        if self.scope_type == GLOBAL:
            return True
        return (
            self.scope_type == scope_type
            and self.scope_key in (None, "*", scope_key)
        )


@dataclass(frozen=True)
class QueuedAuthorizationSnapshot:
    """Authorization context captured when a message was queued.

    ``present=False`` represents an old queue row with no authorization
    context.  Such a row is review-only and cannot become eligible merely
    because a role is later assigned.
    """

    present: bool = True
    identity_id: str | None = None
    authorization_version: str | int | None = None
    role_names: FrozenSet[str] = field(default_factory=frozenset)
    domain: str | None = None
    scope_type: str = GLOBAL
    scope_key: str | None = None
    requires_manual_review: bool = False

    @classmethod
    def legacy(cls) -> "QueuedAuthorizationSnapshot":
        return cls(present=False)


@dataclass(frozen=True)
class DispatchState:
    """Current gates supplied by the later dispatcher."""

    global_outbound_enabled: bool = True
    channel_send_enabled: bool = True
    channel_restriction_reason: str = CHANNEL_RESTRICTED
    identity_hold_active: bool = False
    conversation_hold_active: bool = False
    domain_auto_reply_enabled: bool = True
    manual_review_required: bool = False


@dataclass(frozen=True)
class AuthorizationContext:
    """All inputs needed for one deterministic authorization decision."""

    identity: IdentityEvidence | None
    channel_identifier: str | None
    conversation_key: str | None
    domain: str | None
    current_roles: tuple[RoleGrant, ...] = ()
    current_authorization_version: str | int | None = None
    queued: QueuedAuthorizationSnapshot = field(
        default_factory=QueuedAuthorizationSnapshot
    )
    dispatch: DispatchState = field(default_factory=DispatchState)
    purpose: str = CUSTOMER_AUTO_REPLY
    purpose_authorized: bool = False
    scope_type: str = GLOBAL
    scope_key: str | None = None


@dataclass(frozen=True)
class AuthorizationDecision:
    """Pure result; it contains no queue action and no personal data."""

    allowed: bool
    reason_code: str
    authorization_version: str | int | None
    manual_review_required: bool
    restriction_category: str | None = None


def _deny(
    reason_code: str,
    version: str | int | None,
    *,
    manual_review: bool = False,
    category: str | None = None,
) -> AuthorizationDecision:
    return AuthorizationDecision(
        allowed=False,
        reason_code=reason_code,
        authorization_version=version,
        manual_review_required=manual_review,
        restriction_category=category,
    )


def evaluate(context: AuthorizationContext) -> AuthorizationDecision:
    """Evaluate current authorization without performing any side effect.

    Precedence is global maintenance, channel restriction, active holds,
    purpose/domain restrictions, identity evidence, queue context/version,
    then current role and scope.  Hold release and role changes are outside
    this function; a later dispatcher must not treat an allowed result as an
    instruction to requeue or automatically release anything.
    """

    version = context.current_authorization_version
    state = context.dispatch

    if not state.global_outbound_enabled:
        return _deny(GLOBAL_MAINTENANCE, version, category="global")

    if not state.channel_send_enabled:
        return _deny(
            state.channel_restriction_reason or CHANNEL_RESTRICTED,
            version,
            category="channel",
        )

    if not context.channel_identifier:
        return _deny(CONTEXT_MISSING, version, manual_review=True, category="context")

    identity_id = context.identity.identity_id if context.identity else None
    if state.identity_hold_active and identity_id:
        return _deny(IDENTITY_HOLD, version, category="identity")

    if state.conversation_hold_active and context.conversation_key:
        return _deny(CONVERSATION_HOLD, version, category="conversation")

    is_protected_purpose = context.purpose in {
        PROTECTED_ADMIN_ALERT,
        SYSTEM_ALERT,
    }
    if is_protected_purpose:
        if not context.purpose_authorized:
            return _deny(PROTECTED_PURPOSE_UNAUTHORIZED, version, category="purpose")
        if state.manual_review_required:
            return _deny(MANUAL_REVIEW_REQUIRED, version, manual_review=True, category="review")
        return AuthorizationDecision(
            allowed=True,
            reason_code=ALLOWED_PROTECTED_PURPOSE,
            authorization_version=version,
            manual_review_required=False,
        )

    if not state.domain_auto_reply_enabled:
        return _deny(DOMAIN_RESTRICTED, version, category="domain")

    if context.identity is None or not context.identity.identity_id:
        return _deny(IDENTITY_MISSING, version, manual_review=True, category="identity")
    if context.identity.ambiguous:
        return _deny(IDENTITY_AMBIGUOUS, version, manual_review=True, category="identity")
    if context.identity.verification_state != VERIFIED:
        return _deny(IDENTITY_UNVERIFIED, version, manual_review=True, category="identity")
    if not context.channel_identifier or not context.conversation_key or not context.domain:
        return _deny(CONTEXT_MISSING, version, manual_review=True, category="context")
    if version is None:
        return _deny(CONTEXT_MISSING, version, manual_review=True, category="context")

    queued = context.queued
    if not queued.present:
        return _deny(LEGACY_QUEUE_CONTEXT, version, manual_review=True, category="review")
    if queued.requires_manual_review or state.manual_review_required:
        return _deny(MANUAL_REVIEW_REQUIRED, version, manual_review=True, category="review")
    if queued.identity_id != context.identity.identity_id:
        return _deny(CONTEXT_MISMATCH, version, manual_review=True, category="context")
    if queued.domain != context.domain or queued.scope_type != context.scope_type or queued.scope_key != context.scope_key:
        return _deny(CONTEXT_MISMATCH, version, manual_review=True, category="context")
    if queued.authorization_version != version:
        return _deny(VERSION_MISMATCH, version, manual_review=True, category="authorization")

    matching_roles = tuple(
        grant
        for grant in context.current_roles
        if grant.matches(
            domain=context.domain,
            scope_type=context.scope_type,
            scope_key=context.scope_key,
        )
    )
    if not matching_roles:
        return _deny(ROLE_NOT_AUTHORIZED, version, category="role")
    if queued.role_names and not any(grant.role in queued.role_names for grant in matching_roles):
        return _deny(CURRENT_ROLE_NOT_REVALIDATED, version, manual_review=True, category="authorization")

    return AuthorizationDecision(
        allowed=True,
        reason_code=ALLOWED,
        authorization_version=version,
        manual_review_required=False,
    )


__all__ = [
    "ALLOWED",
    "ALLOWED_PROTECTED_PURPOSE",
    "AuthorizationContext",
    "AuthorizationDecision",
    "CHANNEL_RESTRICTED",
    "CONTEXT_MISMATCH",
    "CONTEXT_MISSING",
    "CUSTOMER_AUTO_REPLY",
    "CONVERSATION_HOLD",
    "CURRENT_ROLE_NOT_REVALIDATED",
    "DOMAIN_RESTRICTED",
    "DispatchState",
    "GLOBAL_MAINTENANCE",
    "IDENTITY_AMBIGUOUS",
    "IDENTITY_HOLD",
    "IDENTITY_MISSING",
    "IDENTITY_UNVERIFIED",
    "IdentityEvidence",
    "LEGACY_QUEUE_CONTEXT",
    "MANUAL_REVIEW_REQUIRED",
    "PROTECTED_ADMIN_ALERT",
    "PROTECTED_PURPOSE_UNAUTHORIZED",
    "QueuedAuthorizationSnapshot",
    "ROLE_NOT_AUTHORIZED",
    "RoleGrant",
    "SYSTEM_ALERT",
    "VERSION_MISMATCH",
    "VERIFIED",
    "evaluate",
]
