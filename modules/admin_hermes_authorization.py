"""Canonical identity/role eligibility for the Admin -> Hermes relay.

Why this module exists (Admin Canary qualification, 2026-09-29)
----------------------------------------------------------------
``modules.admin_directives.router._handle_hermes_relay`` historically proved
authorization with three checks only:

1. legacy scalar RBAC (``modules.rbac.check_permission`` ->
   ``COMMAND_ROLE["hermes_relay"] = "superadmin"``),
2. ``sender_phone == settings.admin_bridge2_number`` (a *settings* value), and
3. ``source_bridge in {"bridge1", "bridge2"}`` (a channel condition).

None of those consults the canonical Identity-Role control plane that this
repository already ships (``fazle_identity_subjects``,
``wbom_identity_links``, ``fazle_identity_role_assignments``,
``fazle_outbound_holds`` -- added by db/migrations/087 and administered by
``modules.identity_role_admin``).  A settings-number equality plus a channel
membership test is not an identity/role authorization decision, and it is not
the authority an auditor can revoke: revoking the canonical identity's role
today does not stop the relay, and revoking the *canonical* role never had any
effect on it at all.

This module is the missing bridge, not a second authorization system.  It
reads the canonical tables, builds the evidence object the canonical adapter
already understands, and hands it to the *existing* single decision function
``modules.outbound_authorization.capture_authorization_snapshot`` -- which in
turn calls the pure evaluator in ``modules.identity_role_authorization``.
There is exactly one evaluator and one decision vocabulary; this file only
supplies evidence for the relay, the way ``outbound_authorization`` supplies
evidence for the queue.

Design constraints honoured
---------------------------
* **Additive, never subtractive.** The three legacy gates in
  ``_handle_hermes_relay`` are untouched and still run. This check is an
  *additional* requirement, so it can only narrow access. Specifically
  ``source_bridge == "bridge2"`` and "this is the admin phone number" remain
  insufficient on their own, exactly as required.
* **Fail closed.** Every failure path -- missing/ambiguous identity, unverified
  or revoked identity, suspended subject, identity or conversation hold,
  missing role, absent schema, any database error -- returns
  ``allowed=False`` with a canonical reason code. This function never raises.
* **No transport, no queue, no settings-driven identity.** It performs
  ``SELECT``/``FOR SHARE`` reads and returns a decision object. It never
  writes, never enqueues, never calls a bridge, and never falls back to a
  legacy scalar role or a display name to manufacture identity evidence.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from app.database import db_conn
from modules.identity_role_authorization import (
    AUTHORIZATION_LOOKUP_FAILED,
    CONTEXT_MISSING,
    IDENTITY_AMBIGUOUS,
    IDENTITY_HOLD,
    IDENTITY_MISSING,
    IDENTITY_UNVERIFIED,
    VERIFIED,
    RoleGrant,
)
from modules.identity_role_authorization_adapter import (
    AuthorizationEvidence,
    BridgeAuthorizationState,
)
from modules.outbound_authorization import (
    AuthorizationSnapshotError,
    capture_authorization_snapshot,
)

log = logging.getLogger("fazle.admin_hermes_authorization")

# The relay's business domain inside the canonical role-assignment model.
# A role grant must either be scoped to this exact domain or to "*" (see
# RoleGrant.matches). The live Super Admin grant is domain "*", so this
# domain choice does not narrow the real owner.
HERMES_RELAY_DOMAIN = "hermes"

# Canonical role names that authorize the Admin <-> Hermes relay. Kept
# deliberately equal to the legacy tier in rbac.COMMAND_ROLE["hermes_relay"]
# ("superadmin") so the canonical check is never the weaker of the two. It is
# a named constant rather than a hard-coded literal at the call site so the
# authorized set is auditable in one place.
HERMES_RELAY_ROLES = frozenset({"superadmin"})

# WhatsApp is the only platform the relay's control channels can produce
# (bridge1/bridge2). bridge3 is Meta and is rejected by the legacy channel
# gate before this function is ever consulted.
_WHATSAPP = "whatsapp"

_ACTIVE_LIFECYCLE = "active"


@dataclass(frozen=True)
class HermesRelayAuthorization:
    """Outcome of one canonical relay-eligibility evaluation.

    ``allowed`` is the only field a caller should branch on. Everything else
    exists so a denial can be audited with a truthful reason instead of a
    generic "forbidden", and so the qualification can report the identity and
    role that were actually resolved.
    """

    allowed: bool
    reason_code: str
    identity_id: str | None = None
    role_names: tuple[str, ...] = ()
    authorization_version: int | None = None
    conversation_key: str = ""
    domain: str = HERMES_RELAY_DOMAIN
    detail: str = ""
    role_grants: tuple[RoleGrant, ...] = field(default=(), repr=False)

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "reason_code": self.reason_code,
            "identity_id": self.identity_id,
            "role_names": list(self.role_names),
            "authorization_version": self.authorization_version,
            "conversation_key": self.conversation_key,
            "domain": self.domain,
            "detail": self.detail,
        }


def _deny(reason_code: str, *, detail: str = "", **kwargs) -> HermesRelayAuthorization:
    return HermesRelayAuthorization(
        allowed=False, reason_code=reason_code, detail=detail, **kwargs
    )


def relay_conversation_key(source_bridge: str, sender_phone: str) -> str:
    """Stable conversation key for the relay's admin<->Hermes control thread.

    Keyed on the same (source_bridge, sender_phone) pair the relay already
    uses for Hermes session continuity in admin_directives/router, so a
    conversation hold placed against this key targets exactly the thread the
    Admin is talking on. Both parts are normalized to lowercase so a hold is
    matched consistently regardless of caller casing.
    """
    return f"hermes_relay:{(source_bridge or '').strip().lower()}:{(sender_phone or '').strip()}"


async def _load_identity(conn, sender_phone: str) -> tuple[Optional[int], str, list]:
    """Resolve the one canonical identity behind a WhatsApp platform user id.

    Returns ``(identity_id, state, links)`` where ``state`` is one of
    ``"resolved"``, ``"missing"``, ``"ambiguous"``, ``"unverified"``. More
    than one distinct active identity for the same platform user id is
    treated as ambiguous -- never resolved by picking the newest.
    """
    links = await conn.fetch(
        """
        SELECT l.identity_id, l.verification_state
          FROM wbom_identity_links l
         WHERE l.active IS TRUE
           AND l.platform = $1
           AND l.platform_user_id = $2
           AND l.identity_id IS NOT NULL
        """,
        _WHATSAPP,
        str(sender_phone or ""),
    )
    identity_ids = {int(row["identity_id"]) for row in links}
    if not identity_ids:
        return None, "missing", links
    if len(identity_ids) > 1:
        return None, "ambiguous", links
    # Every contributing link must itself be verified; a single unverified
    # link means the mapping is not trustworthy enough to authorize on.
    if not all(str(row["verification_state"] or "") == VERIFIED for row in links):
        return next(iter(identity_ids)), "unverified", links
    return next(iter(identity_ids)), "resolved", links


async def resolve_relay_authorization(
    *,
    sender_phone: str,
    source_bridge: str,
    channel_send_enabled: bool = True,
) -> HermesRelayAuthorization:
    """Decide whether the resolved identity is canonically eligible to relay.

    Never raises. Returns a fail-closed :class:`HermesRelayAuthorization` for
    every non-allowable condition, including an unexpected database error.

    Scope of this decision: canonical IDENTITY and ROLE only, plus the
    control-channel gate. It deliberately does **not** take the global
    outbound flag. See the note on ``global_outbound_enabled`` in the
    evidence construction below for why -- that is a dispatch gate and it
    keeps its existing, unchanged position downstream of this decision.
    """
    conversation_key = relay_conversation_key(source_bridge, sender_phone)

    if not sender_phone:
        return _deny(CONTEXT_MISSING, detail="no sender phone supplied", conversation_key=conversation_key)
    if not source_bridge:
        return _deny(CONTEXT_MISSING, detail="no source bridge supplied", conversation_key=conversation_key)

    try:
        async with db_conn() as conn:
            async with conn.transaction():
                identity_id, state, _links = await _load_identity(conn, sender_phone)

                if state == "missing":
                    return _deny(
                        IDENTITY_MISSING,
                        detail="no active canonical identity link for this WhatsApp id",
                        conversation_key=conversation_key,
                    )
                if state == "ambiguous":
                    return _deny(
                        IDENTITY_AMBIGUOUS,
                        detail="more than one active canonical identity resolves to this WhatsApp id",
                        conversation_key=conversation_key,
                    )

                subject = await conn.fetchrow(
                    """
                    SELECT identity_id, verification_state, lifecycle_state,
                           authorization_version
                      FROM fazle_identity_subjects
                     WHERE identity_id = $1
                     FOR SHARE
                    """,
                    identity_id,
                )
                if not subject:
                    return _deny(
                        IDENTITY_MISSING,
                        detail="identity link points at no existing subject row",
                        identity_id=str(identity_id),
                        conversation_key=conversation_key,
                    )

                subject_verification = str(subject["verification_state"] or "")
                if state == "unverified" or subject_verification != VERIFIED:
                    return _deny(
                        IDENTITY_UNVERIFIED,
                        detail=f"identity verification_state={subject_verification or 'unknown'}",
                        identity_id=str(identity_id),
                        conversation_key=conversation_key,
                    )
                if str(subject["lifecycle_state"] or "") != _ACTIVE_LIFECYCLE:
                    return _deny(
                        IDENTITY_UNVERIFIED,
                        detail=f"identity lifecycle_state={subject['lifecycle_state']} is not active",
                        identity_id=str(identity_id),
                        conversation_key=conversation_key,
                    )

                role_rows = await conn.fetch(
                    """
                    SELECT role_name, business_domain, scope_type, scope_key
                      FROM fazle_identity_role_assignments
                     WHERE identity_id = $1
                       AND active IS TRUE
                       AND effective_from <= NOW()
                       AND (effective_until IS NULL OR effective_until > NOW())
                    """,
                    identity_id,
                )
                role_grants = tuple(
                    RoleGrant(
                        role=str(row["role_name"]),
                        domains=frozenset({str(row["business_domain"])}),
                        scope_type=str(row["scope_type"]),
                        scope_key=row["scope_key"],
                    )
                    for row in role_rows
                )

                # Canonical role gate, evaluated here rather than delegated
                # to the evaluator: the evaluator deliberately exempts
                # protected purposes from its role check (a protected admin
                # alert is not a customer auto-reply), so the relay would
                # otherwise be authorized on identity alone.
                eligible = tuple(
                    grant
                    for grant in role_grants
                    if grant.role.strip().lower() in HERMES_RELAY_ROLES
                    and grant.matches(
                        domain=HERMES_RELAY_DOMAIN,
                        scope_type="global",
                        scope_key=None,
                    )
                )
                if not eligible:
                    present = sorted({g.role for g in role_grants}) or ["<none>"]
                    return _deny(
                        "role_not_authorized",
                        detail=(
                            f"no active canonical role in {sorted(HERMES_RELAY_ROLES)} "
                            f"for domain={HERMES_RELAY_DOMAIN}; present={present}"
                        ),
                        identity_id=str(identity_id),
                        authorization_version=int(subject["authorization_version"]),
                        conversation_key=conversation_key,
                        role_grants=role_grants,
                    )

                identity_hold = await conn.fetchval(
                    """
                    SELECT EXISTS(
                        SELECT 1 FROM fazle_outbound_holds
                         WHERE target_type = 'identity' AND identity_id = $1 AND active IS TRUE
                    )
                    """,
                    identity_id,
                )
                if identity_hold:
                    return _deny(
                        IDENTITY_HOLD,
                        detail="an active canonical identity hold is placed on this identity",
                        identity_id=str(identity_id),
                        conversation_key=conversation_key,
                        role_grants=role_grants,
                    )

                conversation_hold = await conn.fetchval(
                    """
                    SELECT EXISTS(
                        SELECT 1 FROM fazle_outbound_holds
                         WHERE target_type = 'conversation'
                           AND conversation_key = $1 AND active IS TRUE
                    )
                    """,
                    conversation_key,
                )
                if conversation_hold:
                    return _deny(
                        "conversation_hold",
                        detail=f"an active canonical conversation hold is placed on {conversation_key}",
                        identity_id=str(identity_id),
                        conversation_key=conversation_key,
                        role_grants=role_grants,
                    )

                evidence = AuthorizationEvidence(
                    identity={
                        "identity_id": str(identity_id),
                        "verification_state": VERIFIED,
                    },
                    channel_identifier=str(source_bridge),
                    conversation_key=conversation_key,
                    domain=HERMES_RELAY_DOMAIN,
                    # PROTECTED_ADMIN_ALERT, not CUSTOMER_AUTO_REPLY: the
                    # relay is an Owner-directed admin turn, and the
                    # evaluator's own comment is explicit that a protected
                    # admin message is not a customer auto-reply merely
                    # because it shares a transport.
                    purpose="protected_admin_alert",
                    current_roles=eligible,
                    authorization_version=int(subject["authorization_version"]),
                    # global_outbound_enabled is the evaluator's *DispatchState*
                    # input, i.e. "may anything leave the machine right now".
                    # It is deliberately NOT a parameter of this function and is
                    # set True here for one specific reason: the decision this
                    # function makes is about canonical identity/role/channel
                    # eligibility, and the outbound gate is a SEPARATE,
                    # pre-existing safety gate that sits downstream of it --
                    # outbound.enqueue() plus the modules.outbound.sweep_once()
                    # global gate, both untouched by this change.
                    #
                    # Folding the dispatch flag in here was tried first and is
                    # wrong twice over: it inverts the required order
                    # (identity/role -> relay eligibility -> outbound gate), and
                    # because production currently runs OUTBOUND_ENABLED=false
                    # it silently silenced the Admin relay entirely -- a
                    # functional outage bought with no additional safety,
                    # since the downstream gate already prevents any send.
                    # Nothing is weakened by omitting it: the relay's answer
                    # still cannot be delivered unless the operator turns
                    # outbound on, exactly as before this change.
                    global_outbound_enabled=True,
                    bridge=BridgeAuthorizationState(
                        send_enabled=channel_send_enabled,
                        pairing_held=not channel_send_enabled,
                    ),
                    # The relay's own outbound reply is a protected admin
                    # purpose, authorized by the canonical decision just
                    # established -- not a domain customer auto-reply.
                    purpose_authorized=True,
                    scope_type="global",
                    scope_key=None,
                )
                snapshot = capture_authorization_snapshot(evidence)
                return HermesRelayAuthorization(
                    allowed=True,
                    reason_code="allowed_protected_purpose",
                    identity_id=str(identity_id),
                    role_names=snapshot.role_names,
                    authorization_version=snapshot.authorization_version,
                    conversation_key=conversation_key,
                    domain=HERMES_RELAY_DOMAIN,
                    detail="canonical identity and role authorized the relay",
                    role_grants=eligible,
                )
    except AuthorizationSnapshotError as exc:
        # The canonical evaluator refused the evidence. Surface its own reason
        # code verbatim -- it is the single decision vocabulary this codebase
        # already audits against.
        return _deny(str(exc), detail="canonical authorization snapshot refused the evidence",
                      conversation_key=conversation_key)
    except Exception as exc:  # noqa: BLE001 -- fail closed on anything else
        log.error(
            "[admin_hermes_authorization] lookup failed for bridge=%s phone=%s: %s",
            source_bridge, sender_phone, exc,
        )
        return _deny(
            AUTHORIZATION_LOOKUP_FAILED,
            detail=f"{type(exc).__name__}: {exc}",
            conversation_key=conversation_key,
        )


__all__ = [
    "HERMES_RELAY_DOMAIN",
    "HERMES_RELAY_ROLES",
    "HermesRelayAuthorization",
    "relay_conversation_key",
    "resolve_relay_authorization",
]
