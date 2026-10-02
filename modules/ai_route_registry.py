"""Admin route registry that compiles into the existing Phase 3B executor."""
from __future__ import annotations

import json
import re
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.config import get_settings
from app.database import db_conn
from app.unified_routing import RouteCandidate, load_core_route_policy
from shared.auth_deps import API_KEY_HEADER

router = APIRouter(prefix="/api/assistant/ops/ai-routes", tags=["ai_route_registry"])
_SAFE_REF = re.compile(r"^[a-z][a-z0-9_]{1,79}$")
_PROVIDER_TRANSPORT = {
    "omniroute": "omniroute_gateway",
    "groq": "direct_provider",
    "ollama-local": "local_ollama",
}
_CORE_ADAPTER_WORKLOADS = frozenset({
    "simple_intent_classification",
    "conversational_reply",
    "recruitment_reply",
    "recruitment_decision",
})


async def _require_internal_key(key: str = Depends(API_KEY_HEADER)) -> None:
    expected = get_settings().internal_api_key
    if not expected or key != expected:
        raise HTTPException(status_code=403, detail="Forbidden")


class RouteConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    route_id: str = Field(min_length=2, max_length=100, pattern=r"^[a-z0-9][a-z0-9._-]+$")
    display_name: str = Field(min_length=1, max_length=120)
    priority: int = Field(ge=0, le=10000)
    transport: Literal["omniroute_gateway", "direct_provider", "local_ollama", "hermes_runner_adapter"]
    provider: str = Field(min_length=2, max_length=80, pattern=r"^[a-z0-9][a-z0-9._-]+$")
    model: str = Field(min_length=1, max_length=200)
    base_url: str = Field(default="", max_length=500)
    credential_type: Literal["api_key", "bearer_token", "session_cookie", "local_runtime", "none"]
    credential_ref: str = Field(min_length=2, max_length=80)
    allowed_workloads: list[str] = Field(min_length=1)
    capabilities: list[str] = Field(min_length=1)
    modalities: list[str] = Field(default_factory=lambda: ["text"], min_length=1)
    privacy_classes: list[str] = Field(min_length=1)
    cost_class: Literal["low", "standard", "high"]
    latency_classes: list[str] = Field(min_length=1)
    context_limit: int = Field(gt=0, le=10_000_000)
    structured_output: bool = False
    enabled: bool = False
    environments: list[str] = Field(default_factory=lambda: ["development", "test"], min_length=1)
    max_attempts: int = Field(default=1, ge=1, le=3)
    retry_backoff_seconds: float = Field(default=0, ge=0, le=30)
    eligible_consumers: list[Literal["core", "runner", "observer"]] = Field(default_factory=lambda: ["core"], min_length=1)

    @field_validator("credential_ref")
    @classmethod
    def safe_credential_ref(cls, value):
        if not _SAFE_REF.fullmatch(value):
            raise ValueError("credential_ref must be a non-secret setting reference")
        return value

    @field_validator("base_url")
    @classmethod
    def safe_base_url(cls, value):
        if value and not value.startswith(("http://", "https://")):
            raise ValueError("base_url must be http(s)")
        parsed = urlsplit(value) if value else None
        if parsed and (parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ValueError("base_url must not contain credentials, query, or fragment")
        return value.rstrip("/")

    def validate_contract(self):
        known_transport = _PROVIDER_TRANSPORT.get(self.provider)
        if known_transport and known_transport != self.transport:
            raise ValueError("provider and transport are incompatible")
        if self.transport == "omniroute_gateway" and self.provider != "omniroute":
            raise ValueError("gateway transport requires omniroute provider")
        if self.transport == "local_ollama" and self.provider != "ollama-local":
            raise ValueError("local transport requires ollama-local provider")
        if self.transport == "local_ollama" and (self.credential_type, self.credential_ref) != ("local_runtime", "local_runtime"):
            raise ValueError("local route requires local_runtime credential")
        conventional_owner = {
            "omniroute_api_key": "omniroute",
            "groq_api_key": "groq",
        }.get(self.credential_ref)
        if conventional_owner and conventional_owner != self.provider:
            raise ValueError("credential reference is not owned by this provider adapter")
        known = set(load_core_route_policy().workloads)
        if not set(self.allowed_workloads).issubset(known):
            raise ValueError("unknown workload")
        if "core" in self.eligible_consumers:
            if not set(self.allowed_workloads).issubset(_CORE_ADAPTER_WORKLOADS):
                raise ValueError("workload has no Core runtime adapter")
            if self.provider not in _PROVIDER_TRANSPORT:
                raise ValueError("provider has no Core runtime adapter")
        candidate_values = self.model_dump(exclude={"eligible_consumers"})
        RouteCandidate(**candidate_values)
        return self


class ReorderRequest(BaseModel):
    route_ids: list[str] = Field(min_length=1)


_SELECT = """route_id, display_name, priority, transport, provider, model,
 base_url, credential_type, credential_ref, allowed_workloads, capabilities,
 modalities, privacy_classes, cost_class, latency_classes, context_limit,
 structured_output, enabled, environments, max_attempts,
 retry_backoff_seconds, eligible_consumers, qualification_status, qualified_at,
 last_success_at, last_failure_at, last_failure_class, created_at, updated_at, updated_by"""


def _validated_route(body: dict) -> RouteConfig:
    forbidden = {"api_key", "token", "secret", "cookie", "password", "session_cookie"}
    if not isinstance(body, dict) or forbidden.intersection(key.lower() for key in body):
        raise HTTPException(status_code=400, detail="secret fields are not accepted; use credential_ref")
    try:
        config = RouteConfig.model_validate(body)
        return config.validate_contract()
    except (ValidationError, ValueError):
        # Do not return Pydantic's input echo: an accidentally submitted secret
        # must not be reflected into HTTP responses or proxy logs.
        raise HTTPException(status_code=400, detail="invalid route configuration")


def _public(row, configured_refs=frozenset()):
    value = dict(row)
    ref = value.get("credential_ref")
    settings = get_settings()
    value["credential_configured"] = ref == "local_runtime" or ref in configured_refs or bool(getattr(settings, ref or "", ""))
    return value


async def _audit(conn, route_id, action, actor, before, after):
    await conn.execute(
        """INSERT INTO phase3_ai_route_audit
           (route_id, action, actor_ref, before_state, after_state)
           VALUES ($1, $2, $3, $4::jsonb, $5::jsonb)""",
        route_id, action, actor[:200], json.dumps(before, default=str) if before else None,
        json.dumps(after, default=str) if after else None,
    )


@router.get("", dependencies=[Depends(_require_internal_key)])
async def list_routes():
    async with db_conn() as conn:
        rows = await conn.fetch(f"SELECT {_SELECT} FROM phase3_ai_routes ORDER BY priority, route_id")
        refs = frozenset(row["secret_ref"] for row in await conn.fetch("SELECT secret_ref FROM phase3_system_secrets WHERE enabled"))
    return {"ok": True, "policy_source": get_settings().unified_ai_routing_policy_source, "routes": [_public(r, refs) for r in rows]}


@router.post("", status_code=201, dependencies=[Depends(_require_internal_key)])
async def create_route(body: dict, x_assistant_actor: str = Header(default="assistant-platform")):
    body = _validated_route(body)
    values = body.model_dump()
    async with db_conn() as conn, conn.transaction():
        if body.enabled and body.credential_ref != "local_runtime":
            valid_secret = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM phase3_system_secrets WHERE secret_ref=$1 AND credential_type=$2 AND $3=ANY(allowed_providers) AND enabled)",
                body.credential_ref, body.credential_type, body.provider,
            )
            if not valid_secret:
                raise HTTPException(status_code=400, detail="enabled route credential is unavailable")
        row = await conn.fetchrow(
            f"""INSERT INTO phase3_ai_routes ({', '.join(values) }, updated_by)
                VALUES ({', '.join(f'${i}' for i in range(1, len(values)+1))}, ${len(values)+1})
                RETURNING {_SELECT}""",
            *values.values(), x_assistant_actor[:200],
        )
        await _audit(conn, body.route_id, "create", x_assistant_actor, None, dict(row))
        refs = frozenset(row["secret_ref"] for row in await conn.fetch("SELECT secret_ref FROM phase3_system_secrets WHERE enabled"))
    return _public(row, refs)


@router.put("/{route_id}", dependencies=[Depends(_require_internal_key)])
async def update_route(route_id: str, body: dict, x_assistant_actor: str = Header(default="assistant-platform")):
    body = _validated_route(body)
    if route_id != body.route_id:
        raise HTTPException(status_code=400, detail="route_id is immutable")
    values = body.model_dump(exclude={"route_id"})
    async with db_conn() as conn, conn.transaction():
        before = await conn.fetchrow(f"SELECT {_SELECT} FROM phase3_ai_routes WHERE route_id=$1", route_id)
        if not before:
            raise HTTPException(status_code=404, detail="route not found")
        if body.enabled and body.credential_ref != "local_runtime":
            valid_secret = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM phase3_system_secrets WHERE secret_ref=$1 AND credential_type=$2 AND $3=ANY(allowed_providers) AND enabled)",
                body.credential_ref, body.credential_type, body.provider,
            )
            if not valid_secret:
                raise HTTPException(status_code=400, detail="enabled route credential is unavailable")
        assignments = ", ".join(f"{name}=${i}" for i, name in enumerate(values, 2))
        row = await conn.fetchrow(
            f"UPDATE phase3_ai_routes SET {assignments}, updated_at=now(), updated_by=${len(values)+2} WHERE route_id=$1 RETURNING {_SELECT}",
            route_id, *values.values(), x_assistant_actor[:200],
        )
        await _audit(conn, route_id, "update", x_assistant_actor, dict(before), dict(row))
        refs = frozenset(row["secret_ref"] for row in await conn.fetch("SELECT secret_ref FROM phase3_system_secrets WHERE enabled"))
    return _public(row, refs)


@router.delete("/{route_id}", dependencies=[Depends(_require_internal_key)])
async def delete_route(route_id: str, x_assistant_actor: str = Header(default="assistant-platform")):
    async with db_conn() as conn, conn.transaction():
        before = await conn.fetchrow(f"DELETE FROM phase3_ai_routes WHERE route_id=$1 RETURNING {_SELECT}", route_id)
        if not before:
            raise HTTPException(status_code=404, detail="route not found")
        await _audit(conn, route_id, "delete", x_assistant_actor, dict(before), None)
    return {"ok": True}


@router.post("/reorder", dependencies=[Depends(_require_internal_key)])
async def reorder_routes(body: ReorderRequest, x_assistant_actor: str = Header(default="assistant-platform")):
    if len(set(body.route_ids)) != len(body.route_ids):
        raise HTTPException(status_code=400, detail="duplicate route_id")
    async with db_conn() as conn, conn.transaction():
        existing = {r["route_id"] for r in await conn.fetch("SELECT route_id FROM phase3_ai_routes")}
        if set(body.route_ids) != existing:
            raise HTTPException(status_code=400, detail="reorder must contain every route exactly once")
        for priority, route_id in enumerate(body.route_ids, 1):
            await conn.execute(
                "UPDATE phase3_ai_routes SET priority=$2, updated_at=now(), updated_by=$3 WHERE route_id=$1",
                route_id, priority, x_assistant_actor[:200],
            )
            await _audit(conn, route_id, "reorder", x_assistant_actor, None, {"priority": priority})
    return {"ok": True, "route_ids": body.route_ids}
