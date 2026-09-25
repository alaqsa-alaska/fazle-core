"""Admin Control Plane slice for Recruitment authority.

This module deliberately keeps the Step 2 JSON register as the canonical
authority store.  Admin edits become drafts first; only an authorized approve
operation changes the current fact.  Writes are locked and atomically replaced
so a failed request cannot leave a partial register.
"""
from __future__ import annotations

import copy
import fcntl
import json
import logging
import os
import re
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.config import get_settings
from modules import rbac
from modules.recruitment_authority import _REGISTER_PATH, load_recruitment_authority, _load_register_payload

log = logging.getLogger("fazle.recruitment_authority_admin")
router = APIRouter(prefix="/api/admin/knowledge/recruitment", tags=["recruitment-authority"])
_LOCK_PATH = Path(str(_REGISTER_PATH) + ".lock")
_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{1,180}$")
_WRITE_COMMAND = "recruitment_authority_write"
_APPROVE_COMMAND = "recruitment_authority_approve"
_READ_COMMAND = "recruitment_authority_read"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _actor_from_key(key: str | None) -> dict[str, Any]:
    if key == get_settings().internal_api_key:
        return {"id": None, "phone": "", "name": "owner", "source": "internal_key"}
    return {"id": None, "phone": "", "name": "unknown", "source": "api_key"}


async def _authorize(key: str | None, command: str) -> dict[str, Any]:
    if not key:
        raise HTTPException(status_code=403, detail="Forbidden")
    if key == get_settings().internal_api_key:
        return _actor_from_key(key)
    permission = await rbac.check_permission(api_key=key, command=command)
    if not permission.get("allowed"):
        actor = permission.get("admin") or {}
        try:
            await rbac.record_audit(
                channel="http", command=command,
                actor_phone=actor.get("phone"), actor_admin=actor,
                allowed=False, required_role=permission.get("required_role"),
                denied_reason=permission.get("reason"),
            )
        except Exception:
            log.warning("could not record denied authority action", exc_info=True)
        raise HTTPException(status_code=403, detail=permission.get("reason") or "Forbidden")
    admin = permission.get("admin") or {}
    return {"id": admin.get("id"), "phone": admin.get("phone", ""),
            "name": admin.get("name", ""), "source": "admin_api_key"}


async def _read_actor(x_internal_key: str | None = Header(default=None)):
    return await _authorize(x_internal_key, _READ_COMMAND)


async def _write_actor(x_internal_key: str | None = Header(default=None)):
    return await _authorize(x_internal_key, _WRITE_COMMAND)


async def _approve_actor(x_internal_key: str | None = Header(default=None)):
    return await _authorize(x_internal_key, _APPROVE_COMMAND)


class DraftBody(BaseModel):
    key: str
    subject: str = ""
    value: str
    conditions: list[str] = Field(default_factory=list)
    source: str = "admin_control_plane"
    expected_revision: str | None = None

    @field_validator("key")
    @classmethod
    def valid_key(cls, value: str) -> str:
        value = value.strip()
        if not _KEY_RE.fullmatch(value):
            raise ValueError("invalid fact key")
        return value

    @field_validator("value")
    @classmethod
    def nonempty_value(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > 10000:
            raise ValueError("value must be 1-10000 characters")
        return value

    @field_validator("conditions", mode="before")
    @classmethod
    def clean_conditions(cls, value):
        if value is None:
            return []
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ValueError("conditions must be a list of strings")
        return [item.strip() for item in value if item.strip()]


class ApproveBody(BaseModel):
    draft_id: str
    expected_revision: str | None = None


@contextmanager
def _locked_register() -> Iterator[dict[str, Any]]:
    _REGISTER_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK_PATH.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            try:
                payload = json.loads(_REGISTER_PATH.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise RuntimeError("authority register unavailable or corrupt") from exc
            if not isinstance(payload, dict) or not isinstance(payload.get("facts"), list):
                raise RuntimeError("authority register has invalid schema")
            payload.setdefault("version", 1)
            payload.setdefault("drafts", [])
            payload.setdefault("history", [])
            yield payload
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _atomic_write(payload: dict[str, Any]) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    fd, temp_name = tempfile.mkstemp(prefix=".recruitment-authority.", suffix=".tmp", dir=_REGISTER_PATH.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as temp:
            temp.write(encoded)
            temp.flush()
            os.fsync(temp.fileno())
        os.replace(temp_name, _REGISTER_PATH)
        dir_fd = os.open(_REGISTER_PATH.parent, os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _find_fact(payload: dict[str, Any], key: str) -> dict[str, Any] | None:
    return next((fact for fact in payload["facts"] if fact.get("key") == key), None)


async def _audit(actor: dict[str, Any], command: str, allowed: bool, summary: str) -> None:
    # Audit is additive and never replaces file-backed provenance.
    import asyncio
    try:
        await asyncio.wait_for(rbac.record_audit(
            channel="http", command=command,
            actor_phone=actor.get("phone"), actor_admin=actor if actor.get("id") else None,
            allowed=allowed, required_role="admin", result_summary=summary,
        ), timeout=2.0)
    except Exception:
        log.warning("authority audit unavailable", exc_info=True)


def _invalidate_authority_cache() -> None:
    _load_register_payload.cache_clear()
    load_recruitment_authority.cache_clear()


def _public_fact(fact: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(fact)
    result["history_count"] = len(result.pop("history", []) or [])
    result["draft_count"] = 0
    return result


@router.get("")
async def list_recruitment_authority(actor: dict[str, Any] = Depends(_read_actor)):
    with _locked_register() as payload:
        drafts = payload.get("drafts", [])
        facts = []
        for fact in payload["facts"]:
            item = _public_fact(fact)
            item["draft_count"] = sum(1 for draft in drafts if draft.get("key") == fact.get("key") and draft.get("status") == "draft")
            facts.append(item)
        return {"facts": facts, "drafts": [copy.deepcopy(d) for d in drafts if d.get("status") == "draft"]}


@router.get("/{fact_key}/history")
async def recruitment_authority_history(fact_key: str, actor: dict[str, Any] = Depends(_read_actor)):
    with _locked_register() as payload:
        fact = _find_fact(payload, fact_key)
        if fact is None:
            raise HTTPException(status_code=404, detail="fact not found")
        return {"key": fact_key, "history": copy.deepcopy(fact.get("history", [])),
                "drafts": [copy.deepcopy(d) for d in payload.get("drafts", []) if d.get("key") == fact_key]}


@router.post("/draft")
async def save_recruitment_draft(body: DraftBody, actor: dict[str, Any] = Depends(_write_actor)):
    with _locked_register() as payload:
        fact = _find_fact(payload, body.key)
        current_revision = (fact or {}).get("revision_id")
        if body.expected_revision and body.expected_revision != current_revision:
            raise HTTPException(status_code=409, detail="stale authority revision")
        draft = {
            "draft_id": "draft_" + uuid.uuid4().hex,
            "key": body.key,
            "subject": body.subject.strip() or (fact or {}).get("subject", body.key),
            "value": body.value,
            "conditions": body.conditions,
            "source": body.source.strip()[:500] or "admin_control_plane",
            "status": "draft",
            "created_at": _now(),
            "created_by": actor,
            "base_revision": current_revision,
        }
        payload.setdefault("drafts", []).append(draft)
        _atomic_write(payload)
    await _audit(actor, _WRITE_COMMAND, True, f"draft saved: {body.key}")
    return {"ok": True, "draft": draft}


@router.post("/draft/{draft_id}/cancel")
async def cancel_recruitment_draft(draft_id: str, actor: dict[str, Any] = Depends(_write_actor)):
    with _locked_register() as payload:
        draft = next((item for item in payload.get("drafts", []) if item.get("draft_id") == draft_id and item.get("status") == "draft"), None)
        if draft is None:
            raise HTTPException(status_code=404, detail="draft not found")
        draft.update({"status": "cancelled", "cancelled_at": _now(), "cancelled_by": actor})
        _atomic_write(payload)
    await _audit(actor, _WRITE_COMMAND, True, f"draft cancelled: {draft.get('key')}")
    return {"ok": True, "draft": draft}


@router.post("/approve")
async def approve_recruitment_draft(body: ApproveBody, actor: dict[str, Any] = Depends(_approve_actor)):
    with _locked_register() as payload:
        draft = next((item for item in payload.get("drafts", []) if item.get("draft_id") == body.draft_id and item.get("status") == "draft"), None)
        if draft is None:
            raise HTTPException(status_code=404, detail="draft not found")
        fact = _find_fact(payload, draft["key"])
        current_revision = (fact or {}).get("revision_id")
        expected = body.expected_revision or draft.get("base_revision")
        if expected and expected != current_revision:
            raise HTTPException(status_code=409, detail="fact changed since draft was created")
        now = _now()
        if fact is None:
            fact = {"key": draft["key"], "subject": draft["subject"], "competing_values": [], "sources": [], "safe_action": ""}
            payload["facts"].append(fact)
        previous = copy.deepcopy(fact)
        previous_status = previous.get("status")
        if previous_status == "current_approved":
            previous["status"] = "superseded"
            previous["superseded_at"] = now
            previous["superseded_by"] = actor
            fact.setdefault("history", []).append(previous)
        revision_id = "rev_" + uuid.uuid4().hex
        fact.update({
            "subject": draft["subject"], "status": "current_approved", "value": draft["value"],
            "competing_values": [], "conditions": draft.get("conditions", []),
            "sources": [draft.get("source") or "admin_control_plane"],
            "safe_action": "answer_from_current_approved",
            "revision_id": revision_id, "approved_at": now, "updated_at": now,
            "approved_by": actor,
        })
        draft.update({"status": "approved", "approved_at": now, "approved_by": actor, "approved_revision": revision_id})
        payload.setdefault("history", []).append({"event": "approved", "draft_id": draft["draft_id"], "key": draft["key"], "revision_id": revision_id, "at": now, "actor": actor})
        _atomic_write(payload)
    _invalidate_authority_cache()
    await _audit(actor, _APPROVE_COMMAND, True, f"approved: {draft['key']} revision={revision_id}")
    return {"ok": True, "fact": _public_fact(fact), "previous_status": previous_status, "revision_id": revision_id}
