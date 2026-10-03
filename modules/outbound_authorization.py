"""Non-sending outbound authorization snapshot and revalidation seam.

This module owns the small bridge between the IR-2A/IR-2B decision logic and
the existing outbound queue. It reads explicit current state, locks a queue
candidate for the duration of a database transaction, and returns a decision.
It never changes queue status and never calls a transport.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Mapping

from modules.identity_role_authorization import (
    AUTHORIZATION_LOOKUP_FAILED,
    CUSTOMER_AUTO_REPLY,
    GLOBAL,
    IdentityEvidence,
    AuthorizationDecision,
    QueuedAuthorizationSnapshot,
    RoleGrant,
)
from modules.identity_role_authorization_adapter import (
    AuthorizationEvidence,
    BridgeAuthorizationState,
    authorize,
)


QUEUE_NOT_CLAIMABLE = "queue_not_claimable"
_APPROVED_PAIRING_STATE = "OUTBOUND_OWNER_APPROVED"
_FAIL_CLOSED_PAIRING_BRIDGE = "bridge3"
_META_PLATFORMS = {"meta", "meta_whatsapp", "messenger", "instagram", "facebook_comment"}


class AuthorizationSnapshotError(ValueError):
    """Explicit authorization evidence cannot produce a queue snapshot."""

_TRANSPORT_MARKER = object()

@dataclass(frozen=True)
class TransportAuthorization:
    """Short-lived capability issued only after a successful final check."""

    decision: AuthorizationDecision
    _marker: object = field(default=_TRANSPORT_MARKER, repr=False, compare=False)


def issue_transport_authorization(decision: AuthorizationDecision) -> TransportAuthorization:
    if not decision.allowed:
        raise PermissionError(f"authorization denied: {decision.reason_code}")
    return TransportAuthorization(decision=decision)


def is_transport_authorized(value: object) -> bool:
    return isinstance(value, TransportAuthorization) and value._marker is _TRANSPORT_MARKER and value.decision.allowed


@dataclass(frozen=True)
class QueueAuthorizationSnapshotRecord:
    """Typed snapshot persisted on a newly authorized queue row."""

    identity_id: int
    authorization_version: int
    conversation_key: str
    domain: str
    scope_type: str
    scope_key: str | None
    role_names: tuple[str, ...]
    captured_at: datetime

    def queued(self) -> QueuedAuthorizationSnapshot:
        return QueuedAuthorizationSnapshot(
            identity_id=str(self.identity_id),
            authorization_version=self.authorization_version,
            role_names=frozenset(self.role_names),
            domain=self.domain,
            conversation_key=self.conversation_key,
            scope_type=self.scope_type,
            scope_key=self.scope_key,
        )

    def db_values(self) -> tuple[Any, ...]:
        # Stable sorted JSON keeps the evidence comparable and avoids role
        # order becoming an accidental authorization signal.
        return (
            self.identity_id,
            self.authorization_version,
            self.conversation_key,
            self.domain,
            self.scope_type,
            self.scope_key,
            json.dumps(list(self.role_names), ensure_ascii=False, separators=(",", ":")),
            self.captured_at,
        )


def _identity_values(value: object) -> tuple[str | None, str, bool]:
    if isinstance(value, IdentityEvidence):
        return value.identity_id, value.verification_state, value.ambiguous
    if isinstance(value, Mapping):
        identity_id = value.get("identity_id")
        state = value.get("verification_state", value.get("identity_verification_state", "unknown"))
        return (
            str(identity_id) if identity_id is not None else None,
            str(state),
            bool(value.get("ambiguous", value.get("identity_ambiguous", False))),
        )
    return None, "unknown", False


def _matching_role_names(evidence: AuthorizationEvidence) -> tuple[str, ...]:
    if not evidence.domain:
        return ()
    names = {
        grant.role.strip()
        for grant in evidence.current_roles
        if isinstance(grant, RoleGrant)
        and grant.role.strip()
        and grant.matches(
            domain=evidence.domain,
            scope_type=evidence.scope_type,
            scope_key=evidence.scope_key,
        )
    }
    return tuple(sorted(names))


def capture_authorization_snapshot(
    evidence: AuthorizationEvidence,
) -> QueueAuthorizationSnapshotRecord:
    """Validate explicit current evidence and create a deterministic snapshot.

    No legacy phone, display name, or scalar role is accepted. The evidence is
    evaluated again with the resulting snapshot, so a held or unauthorized
    context cannot be persisted as a newly authorized queue row.
    """
    identity_id, verification_state, ambiguous = _identity_values(evidence.identity)
    if identity_id is None or ambiguous or verification_state != "verified":
        raise AuthorizationSnapshotError("identity evidence is missing, ambiguous, or unverified")
    try:
        db_identity_id = int(identity_id)
        version = int(evidence.authorization_version)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise AuthorizationSnapshotError("identity authorization version is missing") from None
    if db_identity_id <= 0 or version <= 0:
        raise AuthorizationSnapshotError("identity authorization version is invalid")
    if not evidence.conversation_key or not evidence.domain:
        raise AuthorizationSnapshotError("conversation and domain evidence are required")
    if not evidence.scope_type:
        raise AuthorizationSnapshotError("scope evidence is required")

    role_names = _matching_role_names(evidence)
    if not role_names and evidence.purpose not in {"protected_admin_alert", "system_alert"}:
        raise AuthorizationSnapshotError("current scoped role evidence is missing")

    snapshot = QueueAuthorizationSnapshotRecord(
        identity_id=db_identity_id,
        authorization_version=version,
        conversation_key=str(evidence.conversation_key),
        domain=str(evidence.domain),
        scope_type=str(evidence.scope_type),
        scope_key=evidence.scope_key,
        role_names=role_names,
        captured_at=datetime.now(timezone.utc),
    )
    decision = authorize(replace(evidence, queue=snapshot.queued()))
    if not decision.allowed:
        raise AuthorizationSnapshotError(decision.reason_code)
    return snapshot


def _json_roles(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            raise AuthorizationSnapshotError("role snapshot is not valid JSON") from None
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise AuthorizationSnapshotError("role snapshot must be a JSON string array")
    roles = tuple(sorted({item.strip() for item in value}))
    if list(value) != list(roles):
        raise AuthorizationSnapshotError("role snapshot is not deterministic")
    return roles


def snapshot_from_queue_row(row: Mapping[str, Any]) -> QueueAuthorizationSnapshotRecord | None:
    """Read typed queue snapshot fields; all-null means a legacy row."""
    fields = (
        row.get("authorization_identity_id"),
        row.get("authorization_version"),
        row.get("authorization_conversation_key"),
        row.get("authorization_domain"),
        row.get("authorization_scope_type"),
        row.get("authorization_scope_key"),
        row.get("authorization_role_names"),
        row.get("authorization_captured_at"),
    )
    if all(value is None for value in fields):
        return None
    if any(fields[index] is None for index in (0, 1, 2, 3, 4, 6, 7)):
        raise AuthorizationSnapshotError("authorization snapshot is incomplete")
    try:
        identity_id = int(fields[0])
        version = int(fields[1])
        roles = _json_roles(fields[6])
    except (TypeError, ValueError):
        raise AuthorizationSnapshotError("authorization snapshot has invalid scalar fields") from None
    if identity_id <= 0 or version <= 0 or not str(fields[2]) or not str(fields[3]) or not str(fields[4]):
        raise AuthorizationSnapshotError("authorization snapshot has invalid context")
    captured_at = fields[7]
    if not isinstance(captured_at, datetime):
        raise AuthorizationSnapshotError("authorization snapshot timestamp is invalid")
    return QueueAuthorizationSnapshotRecord(
        identity_id=identity_id,
        authorization_version=version,
        conversation_key=str(fields[2]),
        domain=str(fields[3]),
        scope_type=str(fields[4]),
        scope_key=str(fields[5]) if fields[5] is not None else None,
        role_names=roles,
        captured_at=captured_at,
    )


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _platform_for_channel(channel: str | None) -> str:
    return channel if channel in _META_PLATFORMS else "whatsapp"


def _domain_setting_key(domain: str | None) -> str | None:
    if not domain:
        return None
    return {
        "escort": "auto_reply.escort_client",
        "escort_client": "auto_reply.escort_client",
        "recruitment": "auto_reply.recruitment",
        "client": "auto_reply.client",
    }.get(domain, f"auto_reply.{domain}")


def _failed_evidence(
    row: Mapping[str, Any],
    *,
    channel_identifier: str | None,
    conversation_key: str | None,
    domain: str | None,
    scope_type: str,
    scope_key: str | None,
    global_outbound_enabled: bool,
    purpose_authorized: bool,
    queue: QueuedAuthorizationSnapshot,
) -> AuthorizationEvidence:
    return AuthorizationEvidence(
        identity=None,
        channel_identifier=channel_identifier,
        conversation_key=conversation_key,
        domain=domain,
        purpose=str(row.get("purpose") or CUSTOMER_AUTO_REPLY),
        authorization_version=None,
        authorization_state_available=False,
        queue=queue,
        global_outbound_enabled=global_outbound_enabled,
        purpose_authorized=purpose_authorized,
        scope_type=scope_type,
        scope_key=scope_key,
    )


async def load_current_authorization_evidence(
    conn: Any,
    row: Mapping[str, Any],
    *,
    channel_identifier: str | None = None,
    conversation_key: str | None = None,
    domain: str | None = None,
    scope_type: str = GLOBAL,
    scope_key: str | None = None,
    global_outbound_enabled: bool = False,
    purpose_authorized: bool = False,
) -> AuthorizationEvidence:
    """Load current subject, roles, holds, legacy block, and runtime gates."""
    channel = channel_identifier or row.get("source_bridge")
    queue = QueuedAuthorizationSnapshot.legacy()
    try:
        record = snapshot_from_queue_row(row)
        queue = record.queued() if record else queue

        recipient = str(row.get("recipient") or "")
        if not recipient or not channel:
            return _failed_evidence(
                row, channel_identifier=channel, conversation_key=conversation_key,
                domain=domain, scope_type=scope_type, scope_key=scope_key,
                global_outbound_enabled=global_outbound_enabled,
                purpose_authorized=purpose_authorized, queue=queue,
            )

        platform = _platform_for_channel(str(channel))
        links = await conn.fetch(
            """
            SELECT identity_id, verification_state
              FROM wbom_identity_links
             WHERE active IS TRUE
               AND platform=$1 AND platform_user_id=$2
               AND identity_id IS NOT NULL
            """,
            platform, recipient,
        )
        identity_ids = {int(link["identity_id"]) for link in links}
        if len(identity_ids) != 1:
            identity = (
                IdentityEvidence("ambiguous", "unknown", ambiguous=True)
                if len(identity_ids) > 1 else None
            )
            return replace(
                _failed_evidence(
                    row, channel_identifier=channel, conversation_key=conversation_key,
                    domain=domain, scope_type=scope_type, scope_key=scope_key,
                    global_outbound_enabled=global_outbound_enabled,
                    purpose_authorized=purpose_authorized, queue=queue,
                ),
                identity=identity,
                authorization_state_available=bool(identity),
            )

        identity_id = next(iter(identity_ids))
        subject = await conn.fetchrow(
            """
            SELECT identity_id, verification_state, authorization_version
              FROM fazle_identity_subjects
             WHERE identity_id=$1
             FOR SHARE
            """,
            identity_id,
        )
        if not subject:
            return _failed_evidence(
                row, channel_identifier=channel, conversation_key=conversation_key,
                domain=domain, scope_type=scope_type, scope_key=scope_key,
                global_outbound_enabled=global_outbound_enabled,
                purpose_authorized=purpose_authorized, queue=queue,
            )
        link_verified = all(str(link["verification_state"] or "") == "verified" for link in links)
        verification_state = (
            str(subject["verification_state"])
            if link_verified else "unverified"
        )

        role_rows = await conn.fetch(
            """
            SELECT role_name, business_domain, scope_type, scope_key
              FROM fazle_identity_role_assignments
             WHERE identity_id=$1
               AND active IS TRUE
               AND effective_from <= NOW()
               AND (effective_until IS NULL OR effective_until > NOW())
            """,
            identity_id,
        )
        roles = tuple(
            RoleGrant(
                role=str(role["role_name"]),
                domains=frozenset({str(role["business_domain"])}),
                scope_type=str(role["scope_type"]),
                scope_key=role["scope_key"],
            )
            for role in role_rows
        )
        identity_hold = await conn.fetchval(
            """
            SELECT EXISTS(
                SELECT 1 FROM fazle_outbound_holds
                 WHERE target_type='identity' AND identity_id=$1 AND active IS TRUE
            )
            """,
            identity_id,
        )
        conversation_hold = bool(conversation_key) and await conn.fetchval(
            """
            SELECT EXISTS(
                SELECT 1 FROM fazle_outbound_holds
                 WHERE target_type='conversation' AND conversation_key=$1 AND active IS TRUE
            )
            """,
            conversation_key,
        )
        setting_keys = [f"phone_block.{recipient}", "auto_reply.all"]
        domain_key = _domain_setting_key(domain)
        if domain_key:
            setting_keys.append(domain_key)
        setting_keys.append(f"{channel}.pairing_state")
        settings = {
            str(setting["key"]): str(setting["value"])
            for setting in await conn.fetch(
                "SELECT key, value FROM fazle_runtime_settings WHERE key=ANY($1::text[])",
                setting_keys,
            )
        }
        pairing_state = settings.get(f"{channel}.pairing_state", "").strip()
        pairing_held = (
            pairing_state != _APPROVED_PAIRING_STATE
            if channel == _FAIL_CLOSED_PAIRING_BRIDGE
            else bool(pairing_state and pairing_state != _APPROVED_PAIRING_STATE)
        )
        bridge = BridgeAuthorizationState(
            send_enabled=not pairing_held,
            pairing_held=pairing_held,
            restriction_reason="bridge_pairing_hold",
        )
        domain_enabled = bool(domain_key) and _truthy(settings.get("auto_reply.all")) and _truthy(settings.get(domain_key))
        return AuthorizationEvidence(
            identity={
                "identity_id": str(subject["identity_id"]),
                "verification_state": verification_state,
            },
            channel_identifier=str(channel),
            conversation_key=conversation_key,
            domain=domain,
            purpose=str(row.get("purpose") or CUSTOMER_AUTO_REPLY),
            current_roles=roles,
            authorization_version=subject["authorization_version"],
            queue=queue,
            global_outbound_enabled=global_outbound_enabled,
            bridge=bridge,
            phone_block_active=_truthy(settings.get(f"phone_block.{recipient}")),
            identity_hold_active=bool(identity_hold),
            conversation_hold_active=bool(conversation_hold),
            domain_auto_reply_enabled=domain_enabled,
            purpose_authorized=purpose_authorized,
            scope_type=scope_type,
            scope_key=scope_key,
        )
    except Exception:
        return _failed_evidence(
            row, channel_identifier=channel, conversation_key=conversation_key,
            domain=domain, scope_type=scope_type, scope_key=scope_key,
            global_outbound_enabled=global_outbound_enabled,
            purpose_authorized=purpose_authorized, queue=queue,
        )


@dataclass(frozen=True)
class QueueRevalidationResult:
    queue_id: int | None
    queue_status: str | None
    decision: AuthorizationDecision
    evidence: AuthorizationEvidence | None
    queue_locked: bool = False

    @property
    def transport_allowed(self) -> bool:
        return self.decision.allowed


async def revalidate_queued_outbound(
    conn: Any,
    queue_id: int,
    *,
    conversation_key: str | None,
    domain: str | None,
    scope_type: str = GLOBAL,
    scope_key: str | None = None,
    channel_identifier: str | None = None,
    global_outbound_enabled: bool = False,
    purpose_authorized: bool = False,
    claimed: bool = False,
) -> QueueRevalidationResult:
    """Lock one due candidate, re-read current state, and return only a decision.

    The lock ends before any later transport call. A future dispatcher must
    perform this final check immediately before transport and cannot claim
    external delivery is atomic with PostgreSQL authorization state.
    """
    async with conn.transaction():
        row = await conn.fetchrow(
            """
            SELECT id, status, next_retry_at, recipient, source_bridge, fallback_channel, purpose, meta_json,
                   authorization_identity_id, authorization_version,
                   authorization_conversation_key, authorization_domain,
                   authorization_scope_type, authorization_scope_key,
                   authorization_role_names, authorization_captured_at
              FROM fazle_outbound_queue
             WHERE id=$1
             FOR UPDATE
            """,
            queue_id,
        )
        if not row:
            decision = AuthorizationDecision(
                allowed=False, reason_code=AUTHORIZATION_LOOKUP_FAILED,
                authorization_version=None, manual_review_required=True,
                restriction_category="queue",
            )
            return QueueRevalidationResult(queue_id, None, decision, None, False)
        claimable_status = row["status"] in {"pending", "failed"}
        claimed_status = claimed and row["status"] == "sending"
        if ((not claimable_status and not claimed_status) or
                (claimable_status and row["next_retry_at"] is not None and row["next_retry_at"] > datetime.now(timezone.utc))):
            decision = AuthorizationDecision(
                allowed=False, reason_code=QUEUE_NOT_CLAIMABLE,
                authorization_version=None, manual_review_required=True,
                restriction_category="queue",
            )
            return QueueRevalidationResult(queue_id, row["status"], decision, None, True)
        try:
            evidence = await load_current_authorization_evidence(
                conn, row, channel_identifier=channel_identifier,
                conversation_key=conversation_key, domain=domain,
                scope_type=scope_type, scope_key=scope_key,
                global_outbound_enabled=global_outbound_enabled,
                purpose_authorized=purpose_authorized,
            )
        except Exception:
            evidence = _failed_evidence(
                row, channel_identifier=channel_identifier or row["source_bridge"],
                conversation_key=conversation_key, domain=domain,
                scope_type=scope_type, scope_key=scope_key,
                global_outbound_enabled=global_outbound_enabled,
                purpose_authorized=purpose_authorized,
                queue=QueuedAuthorizationSnapshot.legacy(),
            )
        decision = authorize(evidence)
        return QueueRevalidationResult(queue_id, row["status"], decision, evidence, True)


__all__ = [
    "AuthorizationSnapshotError",
    "QUEUE_NOT_CLAIMABLE",
    "QueueAuthorizationSnapshotRecord",
    "QueueRevalidationResult",
    "TransportAuthorization",
    "capture_authorization_snapshot",
    "issue_transport_authorization",
    "is_transport_authorized",
    "load_current_authorization_evidence",
    "revalidate_queued_outbound",
    "snapshot_from_queue_row",
]
