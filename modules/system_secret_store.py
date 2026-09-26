"""Encrypted system credentials and narrow workload-scoped route-policy API."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
from dataclasses import asdict
from typing import Literal

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.config import get_settings
from app.database import db_conn
from app.unified_routing import RouteCandidate
from modules.ai_route_registry import _require_internal_key

router = APIRouter(prefix="/api/assistant/ops", tags=["system_secret_store"])
_SAFE_REF = re.compile(r"^[a-z][a-z0-9_]{1,79}$")
_CONSUMER_WORKLOADS = {
    "core": frozenset({
        "simple_intent_classification", "conversational_reply",
        "recruitment_reply", "recruitment_decision",
    }),
    "runner": frozenset({"administrative_reasoning"}),
    "observer": frozenset({"conversational_reply"}),
}
_ROUTE_SELECT = """route_id, display_name, priority, transport, provider, model,
 base_url, credential_type, credential_ref, allowed_workloads, capabilities,
 modalities, privacy_classes, cost_class, latency_classes, context_limit,
 structured_output, enabled, environments, max_attempts, retry_backoff_seconds"""


def _master_key() -> bytes:
    raw = get_settings().system_secret_master_key
    if len(raw) < 32:
        raise RuntimeError("system secret store is not configured")
    return hashlib.sha256(raw.encode("utf-8")).digest()


def encrypt_secret(secret_ref: str, credential_type: str, version: int, plaintext: str) -> str:
    nonce = os.urandom(12)
    aad = f"{secret_ref}:{credential_type}:v{version}".encode()
    ciphertext = AESGCM(_master_key()).encrypt(nonce, plaintext.encode(), aad)
    return base64.b64encode(nonce + ciphertext).decode("ascii")


def decrypt_secret(secret_ref: str, credential_type: str, version: int, encoded: str) -> str:
    raw = base64.b64decode(encoded, validate=True)
    if len(raw) < 29:
        raise ValueError("invalid encrypted secret")
    aad = f"{secret_ref}:{credential_type}:v{version}".encode()
    return AESGCM(_master_key()).decrypt(raw[:12], raw[12:], aad).decode()


class SecretWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    secret_ref: str = Field(min_length=2, max_length=80)
    credential_type: Literal["api_key", "bearer_token", "session_cookie"]
    secret_value: str = Field(min_length=1, max_length=20000)
    allowed_providers: list[str] = Field(min_length=1)
    allowed_consumers: list[Literal["core", "runner", "observer"]] = Field(min_length=1)
    allowed_workloads: list[str] = Field(min_length=1)
    enabled: bool = True

    @field_validator("secret_ref")
    @classmethod
    def safe_ref(cls, value):
        if not _SAFE_REF.fullmatch(value):
            raise ValueError("invalid secret reference")
        return value

    @field_validator("secret_value")
    @classmethod
    def nonblank_value(cls, value):
        if not value.strip():
            raise ValueError("secret must not be blank")
        return value

    def validate_scope(self):
        allowed = set().union(*(_CONSUMER_WORKLOADS[c] for c in self.allowed_consumers))
        if not set(self.allowed_workloads).issubset(allowed):
            raise ValueError("workload is not owned by an allowed consumer")
        return self


class SecretUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    credential_type: Literal["api_key", "bearer_token", "session_cookie"]
    secret_value: str | None = Field(default=None, min_length=1, max_length=20000)
    allowed_providers: list[str] = Field(min_length=1)
    allowed_consumers: list[Literal["core", "runner", "observer"]] = Field(min_length=1)
    allowed_workloads: list[str] = Field(min_length=1)
    enabled: bool = True


class SecretResolve(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consumer: Literal["runner", "observer"]
    workload: str
    credential_ref: str


def _public(row):
    return {
        "secret_ref": row["secret_ref"],
        "credential_type": row["credential_type"],
        "allowed_providers": list(row["allowed_providers"]),
        "allowed_consumers": list(row["allowed_consumers"]),
        "allowed_workloads": list(row["allowed_workloads"]),
        "enabled": row["enabled"],
        "version": row["version"],
        "configured": bool(row["encrypted_value"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "updated_by": row["updated_by"],
    }


async def _audit(conn, row, action: str, actor: str):
    await conn.execute(
        """INSERT INTO phase3_system_secret_audit
           (secret_ref, action, actor_ref, credential_type, allowed_providers, allowed_consumers,
            allowed_workloads, version)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8)""",
        row["secret_ref"], action, actor[:200], row["credential_type"],
        row["allowed_providers"], row["allowed_consumers"], row["allowed_workloads"], row["version"],
    )


async def configured_secret_refs(*, consumer: str, workload: str | None = None) -> frozenset[str]:
    clauses = ["enabled", "$1=ANY(allowed_consumers)"]
    args = [consumer]
    if workload is not None:
        clauses.append("$2=ANY(allowed_workloads)")
        args.append(workload)
    async with db_conn() as conn:
        rows = await conn.fetch(
            f"SELECT secret_ref FROM phase3_system_secrets WHERE {' AND '.join(clauses)}",
            *args,
        )
    return frozenset(row["secret_ref"] for row in rows)


async def resolve_secret_internal(secret_ref: str, *, consumer: str, workload: str, provider: str) -> str:
    async with db_conn() as conn:
        row = await conn.fetchrow(
            """SELECT * FROM phase3_system_secrets WHERE secret_ref=$1 AND enabled
                 AND $2=ANY(allowed_consumers) AND $3=ANY(allowed_workloads)""",
            secret_ref, consumer, workload,
        )
    if not row or provider not in row["allowed_providers"]:
        raise ValueError("credential unavailable")
    return decrypt_secret(row["secret_ref"], row["credential_type"], row["version"], row["encrypted_value"])


def _validate_update(secret_ref: str, body: SecretUpdate) -> SecretWrite:
    return SecretWrite(
        secret_ref=secret_ref, credential_type=body.credential_type,
        secret_value=body.secret_value or "unchanged-placeholder",
        allowed_providers=body.allowed_providers,
        allowed_consumers=body.allowed_consumers,
        allowed_workloads=body.allowed_workloads, enabled=body.enabled,
    ).validate_scope()


def _validated_secret_write(body: dict) -> SecretWrite:
    try:
        return SecretWrite.model_validate(body).validate_scope()
    except (ValidationError, ValueError):
        raise HTTPException(status_code=400, detail="invalid system credential")


def _validated_secret_update(body: dict) -> SecretUpdate:
    try:
        value = SecretUpdate.model_validate(body)
        _validate_update("validation_ref", value)
        return value
    except (ValidationError, ValueError):
        raise HTTPException(status_code=400, detail="invalid system credential")


@router.get("/system-secrets", dependencies=[Depends(_require_internal_key)])
async def list_system_secrets():
    async with db_conn() as conn:
        rows = await conn.fetch("SELECT * FROM phase3_system_secrets ORDER BY secret_ref")
    return {"ok": True, "secrets": [_public(row) for row in rows]}


@router.post("/system-secrets", status_code=201, dependencies=[Depends(_require_internal_key)])
async def create_system_secret(body: dict, x_assistant_actor: str = Header(default="assistant-platform")):
    body = _validated_secret_write(body)
    encrypted = encrypt_secret(body.secret_ref, body.credential_type, 1, body.secret_value)
    async with db_conn() as conn, conn.transaction():
        row = await conn.fetchrow(
            """INSERT INTO phase3_system_secrets
               (secret_ref, credential_type, encrypted_value, allowed_providers, allowed_consumers,
                allowed_workloads, enabled, version, updated_by)
               VALUES ($1,$2,$3,$4,$5,$6,$7,1,$8) RETURNING *""",
            body.secret_ref, body.credential_type, encrypted,
            body.allowed_providers, body.allowed_consumers, body.allowed_workloads, body.enabled,
            x_assistant_actor[:200],
        )
        await _audit(conn, row, "create", x_assistant_actor)
    return _public(row)


@router.put("/system-secrets/{secret_ref}", dependencies=[Depends(_require_internal_key)])
async def update_system_secret(secret_ref: str, body: dict, x_assistant_actor: str = Header(default="assistant-platform")):
    body = _validated_secret_update(body)
    validated = _validate_update(secret_ref, body)
    async with db_conn() as conn, conn.transaction():
        before = await conn.fetchrow("SELECT * FROM phase3_system_secrets WHERE secret_ref=$1 FOR UPDATE", secret_ref)
        if not before:
            raise HTTPException(status_code=404, detail="secret not found")
        version = before["version"] + (1 if body.secret_value is not None else 0)
        encrypted = before["encrypted_value"]
        if body.secret_value is not None:
            encrypted = encrypt_secret(secret_ref, body.credential_type, version, body.secret_value)
        elif body.credential_type != before["credential_type"]:
            raise HTTPException(status_code=400, detail="credential type change requires rotation")
        row = await conn.fetchrow(
            """UPDATE phase3_system_secrets SET credential_type=$2,
               encrypted_value=$3, allowed_providers=$4, allowed_consumers=$5, allowed_workloads=$6,
               enabled=$7, version=$8, updated_at=now(), updated_by=$9
               WHERE secret_ref=$1 RETURNING *""",
            secret_ref, body.credential_type, encrypted,
            validated.allowed_providers, validated.allowed_consumers, validated.allowed_workloads,
            body.enabled, version, x_assistant_actor[:200],
        )
        await _audit(conn, row, "rotate" if body.secret_value is not None else "update", x_assistant_actor)
    return _public(row)


@router.delete("/system-secrets/{secret_ref}", dependencies=[Depends(_require_internal_key)])
async def delete_system_secret(secret_ref: str, x_assistant_actor: str = Header(default="assistant-platform")):
    async with db_conn() as conn, conn.transaction():
        used = await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM phase3_ai_routes WHERE credential_ref=$1 AND enabled)", secret_ref,
        )
        if used:
            raise HTTPException(status_code=409, detail="disable referencing routes before deleting credential")
        row = await conn.fetchrow("DELETE FROM phase3_system_secrets WHERE secret_ref=$1 RETURNING *", secret_ref)
        if not row:
            raise HTTPException(status_code=404, detail="secret not found")
        await _audit(conn, row, "delete", x_assistant_actor)
    return {"ok": True}


def _service_actor(consumer: str, authorization: str | None):
    supplied = authorization.removeprefix("Bearer ") if authorization and authorization.startswith("Bearer ") else ""
    expected = getattr(get_settings(), f"ai_policy_{consumer}_bearer", "")
    if not expected or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=403, detail="Forbidden")


@router.get("/ai-policy/{consumer}/{workload}")
async def read_workload_policy(consumer: str, workload: str, authorization: str | None = Header(default=None)):
    if consumer not in ("runner", "observer") or workload not in _CONSUMER_WORKLOADS[consumer]:
        raise HTTPException(status_code=404, detail="unsupported policy scope")
    _service_actor(consumer, authorization)
    async with db_conn() as conn:
        rows = await conn.fetch(
            f"""SELECT {_ROUTE_SELECT} FROM phase3_ai_routes
                 WHERE enabled AND 'production'=ANY(environments)
                   AND $2=ANY(eligible_consumers)
                   AND $1=ANY(allowed_workloads)
                   AND (credential_ref='local_runtime' OR EXISTS(
                       SELECT 1 FROM phase3_system_secrets s
                        WHERE s.secret_ref=phase3_ai_routes.credential_ref
                          AND s.enabled AND phase3_ai_routes.provider=ANY(s.allowed_providers)
                          AND $2=ANY(s.allowed_consumers)
                          AND $1=ANY(s.allowed_workloads)))
                 ORDER BY priority, route_id""", workload, consumer,
        )
    routes = []
    for row in rows:
        values = dict(row)
        for name in ("allowed_workloads", "capabilities", "modalities", "privacy_classes", "latency_classes", "environments"):
            values[name] = frozenset(values[name] or ())
        candidate = RouteCandidate(**values)
        routes.append(asdict(candidate))
    if not routes:
        raise HTTPException(status_code=503, detail="no eligible routes")
    return {"version": 1, "source": "database", "consumer": consumer, "workload": workload, "routes": routes}


@router.post("/ai-policy/resolve-secret")
async def resolve_workload_secret(body: SecretResolve, authorization: str | None = Header(default=None)):
    if body.workload not in _CONSUMER_WORKLOADS[body.consumer]:
        raise HTTPException(status_code=403, detail="Forbidden")
    _service_actor(body.consumer, authorization)
    async with db_conn() as conn:
        route_ok = await conn.fetchval(
            """SELECT EXISTS(SELECT 1 FROM phase3_ai_routes
                 WHERE enabled AND credential_ref=$1 AND $2=ANY(allowed_workloads)
                   AND $3=ANY(eligible_consumers)
                   AND 'production'=ANY(environments))""",
            body.credential_ref, body.workload, body.consumer,
        )
        row = await conn.fetchrow(
            """SELECT s.* FROM phase3_system_secrets s
                 WHERE s.secret_ref=$1 AND s.enabled
                 AND $2=ANY(s.allowed_consumers) AND $3=ANY(s.allowed_workloads)
                 AND EXISTS(SELECT 1 FROM phase3_ai_routes r
                     WHERE r.credential_ref=s.secret_ref AND r.enabled
                       AND r.provider=ANY(s.allowed_providers)
                       AND $3=ANY(r.allowed_workloads) AND $2=ANY(r.eligible_consumers)
                       AND 'production'=ANY(r.environments))""",
            body.credential_ref, body.consumer, body.workload,
        )
    if not route_ok or not row:
        raise HTTPException(status_code=404, detail="credential unavailable")
    try:
        value = decrypt_secret(row["secret_ref"], row["credential_type"], row["version"], row["encrypted_value"])
    except Exception:
        raise HTTPException(status_code=503, detail="credential unavailable")
    return {"credential_ref": row["secret_ref"], "credential_type": row["credential_type"], "secret_value": value}
