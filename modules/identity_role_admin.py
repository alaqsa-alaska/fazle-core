"""Identity–Role administrative control plane.

Extends the existing admin application and reuses its RBAC, identity-link,
role, hold, conversation, and audit stores.  It is separate from the legacy
contact_roles router because that compatibility table is scalar and phone-keyed
and cannot represent stable identities or scoped multi-role grants safely.
"""
from __future__ import annotations

import json
from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.security import APIKeyHeader

from app.config import get_settings
from app.database import db_conn, fetch_all
from modules import rbac

router = APIRouter(prefix="/admin/identity-role", tags=["identity-role"])
_API_KEY = APIKeyHeader(name="X-Internal-Key", auto_error=False)
_READ, _IDENTITY_WRITE, _ROLE_WRITE, _HOLD_WRITE = "user_list", "user_add", "user_role", "user_remove"


def _text(value: Any, field: str, required: bool = False, limit: int = 400):
    if value is None:
        if required:
            raise HTTPException(400, f"{field} is required")
        return None
    value = str(value).strip()
    if required and not value:
        raise HTTPException(400, f"{field} is required")
    if len(value) > limit:
        raise HTTPException(400, f"{field} is too long")
    return value or None


def _actor_label(actor: dict) -> str:
    return str(actor.get("name") or actor.get("phone") or "owner")[:120]


async def _actor(key: str) -> dict:
    if key == get_settings().internal_api_key:
        return {"id": None, "phone": "", "name": "owner", "status": "active"}
    return await rbac.get_admin_by_api_key(key) or {"id": None, "phone": "", "name": "", "status": "unknown"}


def _permission(command: str):
    async def dependency(key: str = Depends(_API_KEY)) -> str:
        if key and key == get_settings().internal_api_key:
            return key
        result = await rbac.check_permission(api_key=key, command=command)
        if result.get("allowed"):
            return key
        admin = result.get("admin")
        if admin:
            await rbac.record_audit(channel="http", command=command,
                                    actor_phone=admin.get("phone"), actor_admin=admin,
                                    allowed=False, required_role=result.get("required_role"),
                                    denied_reason=result.get("reason"))
        raise HTTPException(403, result.get("reason") or "Forbidden")
    return dependency


def _audit_args(identity_id: int, reason: str, **extra: Any) -> str:
    value = {"identity_id": int(identity_id), "reason": reason or ""}
    value.update({k: v for k, v in extra.items() if v is not None})
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


async def _audit(conn: Any, actor: dict, command: str, identity_id: int,
                 reason: str, result: dict, **extra: Any) -> None:
    required = {"identity_identifier": _IDENTITY_WRITE,
                "identity_correct": _IDENTITY_WRITE,
                "identity_role_assign": _ROLE_WRITE,
                "identity_role_revoke": _ROLE_WRITE,
                "identity_hold": _HOLD_WRITE,
                "identity_hold_release": _HOLD_WRITE}.get(command, _READ)
    await rbac.record_audit_conn(
        conn, channel="http", command=command, actor_phone=actor.get("phone"),
        actor_admin=actor, args=_audit_args(identity_id, reason, **extra), allowed=True,
        required_role=rbac.COMMAND_ROLE.get(required, "admin"),
        result_summary=json.dumps({"identity_id": identity_id, "status": result.get("status", "ok")}, sort_keys=True),
    )


async def _subject(conn: Any, identity_id: int, lock: bool = False) -> dict:
    suffix = " FOR UPDATE" if lock else ""
    row = await conn.fetchrow(
        "SELECT identity_id, identity_kind, display_name, verified_name, verification_state, "
        "lifecycle_state, authorization_version, version_updated_at, verified_by, verified_at, "
        "verification_reason, created_at, updated_at FROM fazle_identity_subjects "
        "WHERE identity_id=$1" + suffix, identity_id)
    if not row:
        raise HTTPException(404, "identity subject not found")
    return dict(row)


def _identifier_view(row: dict) -> dict:
    raw = str(row.get("platform_user_id") or "")
    return {"identity_link_id": row.get("identity_link_id"), "identity_id": row.get("identity_id"),
            "identifier_type": row.get("identifier_type"), "platform": row.get("platform"),
            "identifier_preview": (raw[:3] + "…" + raw[-3:]) if len(raw) > 8 else ("…" if raw else ""),
            "verification_state": row.get("verification_state"), "evidence_source": row.get("evidence_source"),
            "verified_by": row.get("verified_by"), "verified_at": row.get("verified_at"),
            "active": row.get("active"), "revoked_at": row.get("revoked_at"),
            "revoked_by": row.get("revoked_by"), "revocation_reason": row.get("revocation_reason"),
            "authorization_version_at_change": row.get("authorization_version_at_change")}


@router.get("/search", dependencies=[Depends(_permission(_READ))])
async def search_identities(q: str = Query("", max_length=120), limit: int = Query(50, ge=1, le=100)):
    rows = await fetch_all(
        "SELECT identity_id, identity_kind, display_name, verified_name, verification_state, "
        "lifecycle_state, authorization_version FROM fazle_identity_subjects "
        "WHERE ($1='' OR display_name ILIKE '%'||$1||'%' OR verified_name ILIKE '%'||$1||'%') "
        "ORDER BY identity_id DESC LIMIT $2", q.strip(), limit)
    return {"items": rows, "count": len(rows)}


@router.get("/{identity_id}", dependencies=[Depends(_permission(_READ))])
async def inspect_identity(identity_id: int):
    async with db_conn() as conn:
        subject = await _subject(conn, identity_id)
        links = await conn.fetch("SELECT * FROM wbom_identity_links WHERE identity_id=$1 ORDER BY identity_link_id DESC", identity_id)
        phone_values = [r["platform_user_id"] for r in links if r["active"] and r["identifier_type"] in ("mobile", "phone", "verified_mobile")]
        block_rows = await conn.fetch(
            "SELECT key, value FROM fazle_runtime_settings WHERE key = ANY($1::text[])",
            [f"phone_block.{value}" for value in phone_values]) if phone_values else []
        blocked = {r["key"]: str(r["value"]).lower() == "true" for r in block_rows}
        legacy_blocks = [
            {"identity_link_id": r["identity_link_id"], "active": blocked.get(f"phone_block.{r['platform_user_id']}", False)}
            for r in links if r["active"] and r["identifier_type"] in ("mobile", "phone", "verified_mobile")
        ]
        roles = await conn.fetch(
            "SELECT assignment_id, role_name, business_domain, scope_type, scope_key, effective_from, effective_until, "
            "active, assigned_by, assigned_by_admin_id, assignment_reason, revoked_at, revoked_by, revoked_by_admin_id, "
            "revocation_reason, authorization_version_at_change, created_at FROM fazle_identity_role_assignments "
            "WHERE identity_id=$1 ORDER BY created_at DESC", identity_id)
        phone_rows = await conn.fetch(
            "SELECT platform_user_id FROM wbom_identity_links WHERE identity_id=$1 AND active "
            "AND identifier_type IN ('mobile','phone','verified_mobile')", identity_id)
        phones = [r["platform_user_id"] for r in phone_rows]
        conversation_keys = []
        if phones:
            thread_rows = await conn.fetch(
                "SELECT bridge_id||':'||platform||':'||counterparty_phone AS conversation_key "
                "FROM fazle_conversation_threads WHERE counterparty_phone = ANY($1)", phones)
            conversation_keys = [r["conversation_key"] for r in thread_rows]
        holds = await conn.fetch(
            "SELECT hold_id, target_type, conversation_key, active, reason, placed_by, placed_by_admin_id, placed_at, "
            "released_by, released_by_admin_id, release_reason, released_at, state_version FROM fazle_outbound_holds "
            "WHERE identity_id=$1 OR conversation_key = ANY($2::text[]) ORDER BY created_at DESC", identity_id, conversation_keys)
    return {"subject": subject, "identifiers": [_identifier_view(dict(r)) for r in links],
            "roles": [dict(r) for r in roles], "holds": [dict(r) for r in holds],
            "legacy_phone_blocks": legacy_blocks,
            "legacy_phone_block": "evaluated at dispatch; not cleared by this control plane"}


@router.get("/{identity_id}/conversations", dependencies=[Depends(_permission(_READ))])
async def inspect_conversations(identity_id: int, limit: int = Query(50, ge=1, le=200)):
    async with db_conn() as conn:
        await _subject(conn, identity_id)
        rows = await conn.fetch(
            "SELECT t.id, t.platform, t.bridge_id, t.counterparty_phone, t.business_domain, t.state, "
            "t.first_message_at, t.last_message_at, t.updated_at FROM fazle_conversation_threads t "
            "JOIN wbom_identity_links l ON l.platform_user_id=t.counterparty_phone "
            "WHERE l.identity_id=$1 AND l.active AND l.identifier_type IN ('mobile','phone','verified_mobile') "
            "ORDER BY t.updated_at DESC LIMIT $2", identity_id, limit)
    return {"items": [dict(r) for r in rows], "count": len(rows)}


@router.get("/{identity_id}/audit", dependencies=[Depends(_permission(_READ))])
async def identity_audit(identity_id: int, limit: int = Query(50, ge=1, le=200)):
    rows = await fetch_all(
        "SELECT id, actor_user_id, actor_label, command, allowed, required_role, denied_reason, result_summary, created_at "
        "FROM fazle_admin_audit WHERE args LIKE $1 ORDER BY created_at DESC LIMIT $2",
        '%"identity_id":' + str(identity_id) + '%', limit)
    return {"items": rows, "count": len(rows)}


@router.post("/{identity_id}/metadata", dependencies=[Depends(_permission(_IDENTITY_WRITE))])
async def correct_identity(identity_id: int, payload: dict, key: str = Depends(_API_KEY)):
    body = payload or {}
    reason = _text(body.get("reason"), "reason", True)
    fields = [f for f in ("display_name", "verified_name", "verification_state", "verification_reason") if f in body]
    if not fields:
        raise HTTPException(400, "at least one identity field is required")
    if body.get("verification_state") not in {None, "unverified", "pending", "verified", "revoked"}:
        raise HTTPException(400, "invalid verification_state")
    actor = await _actor(key)
    async with db_conn() as conn:
        async with conn.transaction():
            before = await _subject(conn, identity_id, True)
            values, args = [], []
            for field in fields:
                values.append(f"{field}=${len(args)+1}")
                args.append(_text(body.get(field), field, limit=1000))
            args.append(identity_id)
            await conn.execute("UPDATE fazle_identity_subjects SET " + ", ".join(values) + ", updated_at=NOW() WHERE identity_id=$" + str(len(args)), *args)
            version = await conn.fetchval("SELECT fazle_bump_identity_authorization_version($1)", identity_id)
            result = {"status": "updated", "identity_id": identity_id, "authorization_version": version}
            await _audit(conn, actor, "identity_correct", identity_id, reason, result,
                          changed_fields=sorted(fields), previous_version=before["authorization_version"])
    return result


@router.post("/{identity_id}/identifiers", dependencies=[Depends(_permission(_IDENTITY_WRITE))])
async def mutate_identifier(identity_id: int, payload: dict, key: str = Depends(_API_KEY)):
    body, action = payload or {}, _text((payload or {}).get("action"), "action", True)
    reason, actor = _text(body.get("reason"), "reason", True), await _actor(key)
    link_id = body.get("identity_link_id")
    async with db_conn() as conn:
        async with conn.transaction():
            await _subject(conn, identity_id, True)
            version = await conn.fetchval("SELECT fazle_bump_identity_authorization_version($1)", identity_id)
            if action == "link":
                platform = _text(body.get("platform"), "platform", True, 40)
                value = _text(body.get("platform_user_id"), "platform_user_id", True, 255)
                itype = _text(body.get("identifier_type"), "identifier_type", True, 40)
                state = _text(body.get("verification_state") or "unverified", "verification_state", limit=20)
                if state not in {"unverified", "pending", "verified", "revoked"}:
                    raise HTTPException(400, "invalid verification_state")
                try:
                    row = await conn.fetchrow(
                        "INSERT INTO wbom_identity_links (identity_key, platform, platform_user_id, identity_id, identifier_type, verification_state, evidence_source, verified_by, verified_by_admin_id, verified_at, authorization_version_at_change) "
                        "VALUES ($1,$2,$3,$4,$5,$6::varchar,$7,$8,$9,CASE WHEN $6::varchar='verified' THEN NOW() ELSE NULL END,$10) RETURNING identity_link_id",
                        body.get("identity_key") or f"{platform}:{value}", platform, value, identity_id, itype, state,
                        _text(body.get("evidence_source"), "evidence_source", limit=400), _actor_label(actor), actor.get("id"), version)
                except asyncpg.exceptions.UniqueViolationError:
                    raise HTTPException(409, "active identifier already linked")
            elif action in {"verify", "revoke"}:
                if not link_id:
                    raise HTTPException(400, "identity_link_id is required")
                if action == "verify":
                    row = await conn.fetchrow(
                        "UPDATE wbom_identity_links SET verification_state='verified', active=true, verified_by=$1, verified_by_admin_id=$2, verified_at=NOW(), authorization_version_at_change=$3, updated_at=NOW() WHERE identity_link_id=$4 AND identity_id=$5 RETURNING identity_link_id",
                        _actor_label(actor), actor.get("id"), version, int(link_id), identity_id)
                else:
                    row = await conn.fetchrow(
                        "UPDATE wbom_identity_links SET verification_state='revoked', active=false, revoked_at=NOW(), revoked_by=$1, revoked_by_admin_id=$2, revocation_reason=$3, authorization_version_at_change=$4, updated_at=NOW() WHERE identity_link_id=$5 AND identity_id=$6 RETURNING identity_link_id",
                        _actor_label(actor), actor.get("id"), reason, version, int(link_id), identity_id)
                if not row:
                    raise HTTPException(404, "identity link not found")
            else:
                raise HTTPException(400, "action must be link, verify, or revoke")
            result = {"status": action, "identity_id": identity_id, "identity_link_id": row["identity_link_id"], "authorization_version": version}
            await _audit(conn, actor, "identity_identifier", identity_id, reason, result, operation=action, link_id=row["identity_link_id"])
    return result


@router.post("/{identity_id}/roles", dependencies=[Depends(_permission(_ROLE_WRITE))])
async def assign_identity_role(identity_id: int, payload: dict, key: str = Depends(_API_KEY)):
    body = payload or {}
    role = _text(body.get("role_name"), "role_name", True, 80)
    domain = _text(body.get("business_domain"), "business_domain", True, 80)
    scope_type, scope_key = _text(body.get("scope_type") or "global", "scope_type", limit=40), _text(body.get("scope_key"), "scope_key", limit=255)
    reason = _text(body.get("reason"), "reason", True)
    if scope_type == "global": scope_key = None
    elif not scope_key: raise HTTPException(400, "scope_key is required for non-global scope")
    actor = await _actor(key)
    async with db_conn() as conn:
        async with conn.transaction():
            await _subject(conn, identity_id, True)
            version = await conn.fetchval("SELECT fazle_bump_identity_authorization_version($1)", identity_id)
            try:
                row = await conn.fetchrow(
                    "INSERT INTO fazle_identity_role_assignments (identity_id, role_name, business_domain, scope_type, scope_key, assigned_by, assigned_by_admin_id, assignment_reason, authorization_version_at_change) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING assignment_id",
                    identity_id, role, domain, scope_type, scope_key, _actor_label(actor), actor.get("id"), reason, version)
            except asyncpg.exceptions.UniqueViolationError:
                raise HTTPException(409, "equivalent active role already exists")
            result = {"status": "assigned", "identity_id": identity_id, "assignment_id": row["assignment_id"], "authorization_version": version}
            await _audit(conn, actor, "identity_role_assign", identity_id, reason, result, assignment_id=row["assignment_id"], domain=domain, scope_type=scope_type)
    return result


@router.post("/{identity_id}/roles/{assignment_id}/revoke", dependencies=[Depends(_permission(_ROLE_WRITE))])
async def revoke_identity_role(identity_id: int, assignment_id: int, payload: dict, key: str = Depends(_API_KEY)):
    reason, actor = _text((payload or {}).get("reason"), "reason", True), await _actor(key)
    async with db_conn() as conn:
        async with conn.transaction():
            await _subject(conn, identity_id, True)
            version = await conn.fetchval("SELECT fazle_bump_identity_authorization_version($1)", identity_id)
            row = await conn.fetchrow(
                "UPDATE fazle_identity_role_assignments SET active=false, effective_until=NOW(), revoked_at=NOW(), revoked_by=$1, revoked_by_admin_id=$2, revocation_reason=$3, authorization_version_at_change=$4, updated_at=NOW() WHERE assignment_id=$5 AND identity_id=$6 AND active=true RETURNING assignment_id",
                _actor_label(actor), actor.get("id"), reason, version, assignment_id, identity_id)
            if not row: raise HTTPException(404, "active role assignment not found")
            result = {"status": "revoked", "identity_id": identity_id, "assignment_id": assignment_id, "authorization_version": version}
            await _audit(conn, actor, "identity_role_revoke", identity_id, reason, result, assignment_id=assignment_id)
    return result


@router.post("/{identity_id}/holds", dependencies=[Depends(_permission(_HOLD_WRITE))])
async def place_hold(identity_id: int, payload: dict, key: str = Depends(_API_KEY)):
    body = payload or {}
    target = _text(body.get("target_type") or "identity", "target_type", limit=20)
    reason, conversation_key = _text(body.get("reason"), "reason", True), _text(body.get("conversation_key"), "conversation_key", limit=500)
    if target not in {"identity", "conversation"}: raise HTTPException(400, "target_type must be identity or conversation")
    if target == "conversation" and not conversation_key: raise HTTPException(400, "conversation_key is required")
    actor = await _actor(key)
    async with db_conn() as conn:
        async with conn.transaction():
            await _subject(conn, identity_id, True)
            version = await conn.fetchval("SELECT fazle_bump_identity_authorization_version($1)", identity_id) if target == "identity" else None
            try:
                row = await conn.fetchrow(
                    "INSERT INTO fazle_outbound_holds (target_type, identity_id, conversation_key, reason, placed_by, placed_by_admin_id) VALUES ($1::varchar, CASE WHEN $1::varchar='identity' THEN $2::bigint ELSE NULL END, CASE WHEN $1::varchar='conversation' THEN $3::text ELSE NULL END, $4, $5, $6) RETURNING hold_id, state_version",
                    target, identity_id, conversation_key, reason, _actor_label(actor), actor.get("id"))
            except asyncpg.exceptions.UniqueViolationError: raise HTTPException(409, "active hold already exists")
            result = {"status": "held", "identity_id": identity_id, "hold_id": row["hold_id"], "target_type": target, "authorization_version": version, "state_version": row["state_version"]}
            await _audit(conn, actor, "identity_hold", identity_id, reason, result, target_type=target, hold_id=row["hold_id"])
    return result


@router.post("/{identity_id}/holds/{hold_id}/release", dependencies=[Depends(_permission(_HOLD_WRITE))])
async def release_hold(identity_id: int, hold_id: int, payload: dict, key: str = Depends(_API_KEY)):
    reason, actor = _text((payload or {}).get("reason"), "reason", True), await _actor(key)
    async with db_conn() as conn:
        async with conn.transaction():
            await _subject(conn, identity_id, True)
            requested_key = _text((payload or {}).get("conversation_key"), "conversation_key", limit=500)
            hold = await conn.fetchrow("SELECT hold_id, target_type, identity_id, conversation_key, active FROM fazle_outbound_holds WHERE hold_id=$1 AND (identity_id=$2 OR (target_type='conversation' AND identity_id IS NULL)) FOR UPDATE", hold_id, identity_id)
            if not hold: raise HTTPException(404, "hold not found")
            if not hold["active"]: raise HTTPException(409, "hold already released")
            if hold["target_type"] == "conversation" and requested_key != hold["conversation_key"]:
                raise HTTPException(403, "conversation hold target mismatch")
            version = await conn.fetchval("SELECT fazle_bump_identity_authorization_version($1)", identity_id) if hold["target_type"] == "identity" else None
            row = await conn.fetchrow("UPDATE fazle_outbound_holds SET active=false, released_by=$1, released_by_admin_id=$2, release_reason=$3, released_at=NOW(), state_version=state_version+1, updated_at=NOW() WHERE hold_id=$4 RETURNING state_version", _actor_label(actor), actor.get("id"), reason, hold_id)
            result = {"status": "released", "identity_id": identity_id, "hold_id": hold_id, "target_type": hold["target_type"], "authorization_version": version, "state_version": row["state_version"], "dispatch": "none"}
            await _audit(conn, actor, "identity_hold_release", identity_id, reason, result, target_type=hold["target_type"], hold_id=hold_id)
    return result
