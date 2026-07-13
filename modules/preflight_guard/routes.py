from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, Query
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field

from app.database import fetch_all, fetch_one
from modules.preflight_guard import (
    activate_feature,
    kill_switch_feature,
    list_feature_flags,
    run_preflight,
)
from modules.location_adapter import (
    check_location_service_health,
    get_device_status,
    get_employee_location,
)
from shared.auth_deps import require_api_key as _require_api_key

log = logging.getLogger("fazle.preflight_guard")
router = APIRouter(tags=["preflight_guard"])


class PreflightRequest(BaseModel):
    release_id: str = Field(..., min_length=1)
    paths: list[str] = Field(default_factory=list)
    sql_migrations: list[str] = Field(default_factory=list)
    created_by: str = Field(default="system")


class GateUpdateRequest(BaseModel):
    release_id: str = Field(..., min_length=1)
    phase: int = Field(..., ge=1)
    feature_group: str = Field(..., min_length=1)
    status: str = Field(..., min_length=1)


class OpsActorRequest(BaseModel):
    updated_by: str = Field(default="system")


@router.post("/api/assistant/release/preflight")
async def create_preflight_report(req: PreflightRequest, _: None = Depends(_require_api_key)):
    report = run_preflight(
        paths=req.paths,
        sql_migrations=req.sql_migrations,
        release_id=req.release_id,
    )

    row = await fetch_one(
        """
        INSERT INTO assistant_preflight_reports
          (release_id, status, path_check, table_check, blocked_paths, blocked_tables, raw_report, created_by)
        VALUES
          ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7::jsonb, $8)
        RETURNING id, release_id, run_at, status, path_check, table_check, blocked_paths, blocked_tables, raw_report, created_by
        """,
        req.release_id,
        report["status"],
        report["path_check"],
        report["table_check"],
        json.dumps(report["blocked_paths"]),
        json.dumps(report["blocked_tables"]),
        json.dumps(report),
        req.created_by,
    )

    return {
        "ok": True,
        "report": jsonable_encoder(row) if row else jsonable_encoder(report),
    }


@router.get("/api/assistant/release/gates/{release_id}")
async def get_release_gates(release_id: str, _: None = Depends(_require_api_key)):
    rows = await fetch_all(
        """
        SELECT id, release_id, phase, feature_group, status, created_at, updated_at
        FROM assistant_release_gates
        WHERE release_id = $1
        ORDER BY phase ASC, updated_at DESC, id DESC
        """,
        release_id,
    )
    return {
        "ok": True,
        "release_id": release_id,
        "gates": jsonable_encoder(rows),
    }


@router.post("/api/assistant/release/gate/update")
async def upsert_release_gate(req: GateUpdateRequest, _: None = Depends(_require_api_key)):
    existing = await fetch_one(
        """
        SELECT id
        FROM assistant_release_gates
        WHERE release_id = $1 AND phase = $2 AND feature_group = $3
        ORDER BY id DESC
        LIMIT 1
        """,
        req.release_id,
        req.phase,
        req.feature_group,
    )

    if existing:
        row = await fetch_one(
            """
            UPDATE assistant_release_gates
            SET status = $1,
                updated_at = NOW()
            WHERE id = $2
            RETURNING id, release_id, phase, feature_group, status, created_at, updated_at
            """,
            req.status,
            existing["id"],
        )
        action = "updated"
    else:
        row = await fetch_one(
            """
            INSERT INTO assistant_release_gates
              (release_id, phase, feature_group, status)
            VALUES
              ($1, $2, $3, $4)
            RETURNING id, release_id, phase, feature_group, status, created_at, updated_at
            """,
            req.release_id,
            req.phase,
            req.feature_group,
            req.status,
        )
        action = "inserted"

    return {
        "ok": True,
        "action": action,
        "gate": jsonable_encoder(row),
    }


@router.get("/api/assistant/release/preflight/latest")
async def get_latest_preflight(
    release_id: str | None = Query(default=None),
    _: None = Depends(_require_api_key),
):
    if release_id:
        row = await fetch_one(
            """
            SELECT id, release_id, run_at, status, path_check, table_check, blocked_paths, blocked_tables, raw_report, created_by
            FROM assistant_preflight_reports
            WHERE release_id = $1
            ORDER BY run_at DESC, id DESC
            LIMIT 1
            """,
            release_id,
        )
    else:
        row = await fetch_one(
            """
            SELECT id, release_id, run_at, status, path_check, table_check, blocked_paths, blocked_tables, raw_report, created_by
            FROM assistant_preflight_reports
            ORDER BY run_at DESC, id DESC
            LIMIT 1
            """
        )

    return {
        "ok": True,
        "report": jsonable_encoder(row) if row else None,
    }


@router.post("/api/assistant/ops/kill-switch/{feature_name}")
async def ops_kill_switch(
    feature_name: str,
    req: OpsActorRequest | None = None,
    _: None = Depends(_require_api_key),
):
    actor = req.updated_by if req else "system"
    state = await kill_switch_feature(feature_name=feature_name, updated_by=actor)
    return {
        "ok": True,
        "action": "kill-switch",
        "flag": jsonable_encoder(state),
    }


@router.post("/api/assistant/ops/activate/{feature_name}")
async def ops_activate_feature(
    feature_name: str,
    req: OpsActorRequest | None = None,
    _: None = Depends(_require_api_key),
):
    actor = req.updated_by if req else "system"
    state = await activate_feature(feature_name=feature_name, updated_by=actor)
    return {
        "ok": True,
        "action": "activate",
        "flag": jsonable_encoder(state),
    }


@router.get("/api/assistant/ops/flags")
async def ops_list_flags(_: None = Depends(_require_api_key)):
    flags = await list_feature_flags()
    return {
        "ok": True,
        "flags": jsonable_encoder(flags),
    }


@router.get("/api/assistant/internal/location/employee/{employee_id}")
async def internal_get_employee_location(employee_id: str, _: None = Depends(_require_api_key)):
    result = await get_employee_location(employee_id)
    return {"ok": True, "result": jsonable_encoder(result)}


@router.get("/api/assistant/internal/location/device/{device_id}")
async def internal_get_device_status(device_id: str, _: None = Depends(_require_api_key)):
    result = await get_device_status(device_id)
    return {"ok": True, "result": jsonable_encoder(result)}


@router.get("/api/assistant/internal/location/health")
async def internal_check_location_health(_: None = Depends(_require_api_key)):
    result = await check_location_service_health()
    return {"ok": True, "result": jsonable_encoder(result)}
