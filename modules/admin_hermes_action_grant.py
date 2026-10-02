"""Scoped Admin authorization for Hermes-originated outbound actions.

Owner decision, 2026-09-30: Bridge2 -> Bridge1 is the trusted Admin <-> Hermes
control conversation. Hermes IS meant to perform admin-requested actions
(approve a draft, send/forward a message). The problem is not that Hermes can
act -- it is that Hermes must not be the one deciding it is allowed to.

This module supplies the missing evidence, using mechanisms the codebase
already ships:

* **Authority** comes from the existing canonical relay authorization,
  ``modules.admin_hermes_authorization.resolve_relay_authorization``, which
  already resolves the Admin's identity from ``wbom_identity_links``, verifies
  it, checks canonical role grants, and honours identity/conversation holds.
  This module never re-decides identity or role.

* **Scope** is recorded on the draft row itself, in the existing ``meta``
  jsonb column. No migration, no new table, no second authorization system --
  the same "evidence travels with the thing it authorizes" shape
  ``modules.outbound_authorization`` already uses for queue rows.

The grant
---------
An authenticated Admin message in the relay conversation is the evidence. It
authorizes exactly the Hermes-proposed drafts that are still ``pending`` in
that same conversation. Everything else stays denied:

* only ``hermes_suggested`` drafts (the drafts Hermes itself proposed) are
  grantable, so this can never authorize a human's own draft;
* the grant is bound to the conversation, so it cannot be replayed elsewhere;
* it expires, so "yes" is not permanent RUN authority;
* it is single-use -- approval flips the draft's status, and the grant is
  marked consumed at the same time;
* recipient and content are NOT stored in the grant. Approval always reads
  them from the stored draft row, so the thing that was authorized and the
  thing that is sent cannot drift apart.

Hermes cannot forge any of this: writing the grant requires the canonical
relay authorization to have passed, and the grant lives in a column Hermes
has no tool to write. Hermes can only carry the conversation forward.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional

log = logging.getLogger("fazle.admin_hermes_action_grant")

# meta key holding the grant. Namespaced so it can never collide with the
# existing meta keys (submission_protocol, core_message_id, role, ...).
GRANT_KEY = "admin_action_grant"

# meta key holding the conversation a draft was PROPOSED in. Written once, at
# creation, from the hermes-runner subprocess environment -- never by the
# model. It is what the grant is later matched against, so a proposal from a
# customer conversation can never be authorized by an Admin "yes".
DRAFT_CONVERSATION_KEY = "hermes_conversation_key"

# Only drafts Hermes itself proposed are grantable. draft_tools'
# draft_whatsapp_reply already stamps this intent, so it is an existing
# marker rather than a new one.
HERMES_INTENT = "hermes_suggested"

# How long an authenticated Admin "yes" remains usable. Deliberately short:
# the Owner's authorization is scoped to the action being confirmed, not a
# standing permission.
DEFAULT_GRANT_TTL_SECONDS = 300


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_meta(row_meta: Any) -> dict:
    if row_meta is None:
        return {}
    if isinstance(row_meta, str):
        import json

        try:
            parsed = json.loads(row_meta)
        except (ValueError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    if isinstance(row_meta, Mapping):
        return dict(row_meta)
    return {}


def is_hermes_proposed(draft: Mapping[str, Any]) -> bool:
    """True only for drafts Hermes itself proposed in the relay."""
    if str(draft.get("intent") or "") == HERMES_INTENT:
        return True
    return bool(_as_meta(draft.get("meta")).get("source_module") == "hermes")


def build_grant(
    *,
    identity_id: str,
    role_names: tuple[str, ...] = (),
    conversation_key: str,
    ttl_seconds: int = DEFAULT_GRANT_TTL_SECONDS,
) -> dict:
    """The grant payload stored in the draft's meta."""
    expires = _now() + timedelta(seconds=max(1, int(ttl_seconds)))
    return {
        "identity_id": str(identity_id),
        "role_names": list(role_names),
        "conversation_key": conversation_key,
        "granted_at": _now().isoformat(),
        "expires_at": expires.isoformat(),
        "consumed_at": None,
    }


async def authorize_pending_hermes_drafts(
    conn,
    *,
    conversation_key: str,
    identity_id: str,
    role_names: tuple[str, ...] = (),
    ttl_seconds: int = DEFAULT_GRANT_TTL_SECONDS,
) -> list[int]:
    """Stamp a scoped grant onto this conversation's pending Hermes drafts.

    Called by the relay immediately after the canonical relay authorization
    has allowed an Admin message. The Admin message is the evidence; this
    records it against the concrete actions it can now authorize.

    Returns the draft ids that were granted. Never raises: an authorization
    bookkeeping failure must not break the conversation, and a missing grant
    fails closed at execution time anyway.
    """
    try:
        rows = await conn.fetch(
            """
            SELECT id, meta
              FROM fazle_draft_replies
             WHERE intent = $1
               AND status IN ('pending', 'edited')
               AND COALESCE(meta->>'hermes_conversation_key', '') = $2
             ORDER BY id DESC
             LIMIT 20
            """,
            HERMES_INTENT,
            conversation_key,
        )
    except Exception as exc:  # noqa: BLE001 -- fail closed, never raise
        log.error(
            "[admin_hermes_action_grant] could not read pending hermes drafts "
            "for %s: %s",
            conversation_key,
            exc,
        )
        return []

    grant = build_grant(
        identity_id=identity_id,
        role_names=role_names,
        conversation_key=conversation_key,
        ttl_seconds=ttl_seconds,
    )

    granted: list[int] = []
    for row in rows:
        meta = _as_meta(row["meta"])
        existing = meta.get(GRANT_KEY)
        # A still-valid grant is left alone so two consecutive Admin messages
        # do not silently extend one another.
        if isinstance(existing, Mapping) and _grant_reason(existing) is None:
            continue
        try:
            await conn.execute(
                """
                UPDATE fazle_draft_replies
                   SET meta = COALESCE(meta, '{}'::jsonb) || $2::jsonb
                 WHERE id = $1
                """,
                int(row["id"]),
                _jsonb(grant),
            )
            granted.append(int(row["id"]))
        except Exception as exc:  # noqa: BLE001
            log.error(
                "[admin_hermes_action_grant] could not stamp draft %s: %s",
                row["id"],
                exc,
            )
    return granted


def _jsonb(value: Any) -> str:
    import json

    return json.dumps({GRANT_KEY: value})


def _grant_reason(grant: Mapping[str, Any]) -> Optional[str]:
    """None when the grant is usable, else a short reason code."""
    if not isinstance(grant, Mapping):
        return "grant_missing"
    if not str(grant.get("identity_id") or ""):
        return "grant_has_no_identity"
    if not str(grant.get("conversation_key") or ""):
        return "grant_has_no_conversation"
    consumed = grant.get("consumed_at")
    if consumed:
        return "grant_already_consumed"
    raw_expiry = str(grant.get("expires_at") or "")
    if not raw_expiry:
        return "grant_has_no_expiry"
    try:
        expires = datetime.fromisoformat(raw_expiry)
    except ValueError:
        return "grant_expiry_unparseable"
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if _now() >= expires:
        return "grant_expired"
    return None


def verify_draft_grant(
    draft: Mapping[str, Any],
    *,
    conversation_key: Optional[str] = None,
    now: Optional[datetime] = None,
) -> tuple[bool, str]:
    """Decide whether this draft may be approved as an Admin-authorized action.

    Returns ``(allowed, reason)``. Fails closed. Recipient and content are
    deliberately NOT part of this check: the approve path reads both from the
    stored row, so a grant can never authorize a substituted recipient or
    substituted text.
    """
    if not is_hermes_proposed(draft):
        # Not a Hermes proposal -- this is somebody else's draft and this
        # module has no opinion. The caller's own gate still applies.
        return True, "not_hermes_proposed"

    meta = _as_meta(draft.get("meta"))
    reason = _grant_reason(meta.get(GRANT_KEY))
    if reason is not None:
        return False, reason

    grant = meta[GRANT_KEY]

    # 2026-09-30 (Owner: the Admin<->Hermes relay and the customer/employee
    # conversation are never one authority). A draft and its grant must belong
    # to the SAME authorized conversation. Without this an Admin "yes" in the
    # control thread would authorize a proposal that originated in a customer
    # conversation -- the two authorities leaking into each other.
    #
    # This is the check that actually enforces the boundary: the draft's own
    # conversation was stamped when Hermes proposed it, from ambient process
    # context the model cannot set, and a customer conversation stamps none.
    draft_conversation = str(meta.get(DRAFT_CONVERSATION_KEY) or "")
    if not draft_conversation:
        return False, "draft_has_no_conversation"
    if str(grant.get("conversation_key") or "") != draft_conversation:
        return False, "grant_conversation_mismatch"

    if conversation_key and str(grant.get("conversation_key")) != conversation_key:
        return False, "grant_conversation_mismatch"
    if now is not None:
        raw = str(grant.get("expires_at") or "")
        try:
            expires = datetime.fromisoformat(raw)
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
        except ValueError:
            return False, "grant_expiry_unparseable"
        if now >= expires:
            return False, "grant_expired"
    return True, "admin_grant_valid"


async def consume_draft_grant(conn, draft_id: int) -> None:
    """Mark the grant spent. Called in the same transaction as approval."""
    try:
        await conn.execute(
            f"""
            UPDATE fazle_draft_replies
               SET meta = COALESCE(meta, '{{}}'::jsonb)
                             || jsonb_build_object(
                                    '{GRANT_KEY}',
                                    COALESCE(meta->'{GRANT_KEY}', '{{}}'::jsonb)
                                    || jsonb_build_object('consumed_at', $2)
                                )
             WHERE id = $1
            """,
            int(draft_id),
            _now().isoformat(),
        )
    except Exception as exc:  # noqa: BLE001 -- approval already gated on it
        log.error(
            "[admin_hermes_action_grant] could not consume grant for draft %s: %s",
            draft_id,
            exc,
        )


__all__ = [
    "DEFAULT_GRANT_TTL_SECONDS",
    "DRAFT_CONVERSATION_KEY",
    "GRANT_KEY",
    "HERMES_INTENT",
    "authorize_pending_hermes_drafts",
    "build_grant",
    "consume_draft_grant",
    "is_hermes_proposed",
    "verify_draft_grant",
]
