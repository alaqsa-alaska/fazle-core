"""Offline qualification for the Step 4 Recruitment authority slice."""
from __future__ import annotations

import json

import pytest
from unittest.mock import AsyncMock
from fastapi import HTTPException

from modules import recruitment_authority_admin as admin


@pytest.fixture
def authority_file(tmp_path, monkeypatch):
    path = tmp_path / "recruitment_fact_authority.json"
    path.write_text(json.dumps({
        "version": 1,
        "status_vocabulary": ["current_approved", "historical", "unverified", "conflicting", "superseded"],
        "facts": [{
            "key": "salary.by_role",
            "subject": "salary",
            "status": "current_approved",
            "value": "old approved value",
            "revision_id": "rev_old",
            "sources": ["owner-approved"],
            "conditions": ["role=guard"],
            "competing_values": [],
            "safe_action": "answer_from_current_approved",
        }],
    }, ensure_ascii=False), encoding="utf-8")
    from modules import recruitment_authority
    monkeypatch.setattr(admin, "_REGISTER_PATH", path)
    monkeypatch.setattr(recruitment_authority, "_REGISTER_PATH", path)
    monkeypatch.setattr(admin, "_LOCK_PATH", tmp_path / "authority.lock")
    admin._invalidate_authority_cache()
    monkeypatch.setattr(admin, "_audit", AsyncMock())
    return path


@pytest.mark.asyncio
async def test_newer_draft_does_not_override_current_authority(authority_file):
    actor = {"id": 7, "name": "authorized admin", "phone": "8801", "source": "admin_api_key"}
    result = await admin.save_recruitment_draft(
        admin.DraftBody(key="salary.by_role", value="new pending value", conditions=["role=guard"]), actor
    )
    assert result["draft"]["status"] == "draft"
    payload = json.loads(authority_file.read_text(encoding="utf-8"))
    assert payload["facts"][0]["value"] == "old approved value"
    assert payload["facts"][0]["status"] == "current_approved"


@pytest.mark.asyncio
async def test_approve_makes_latest_value_current_and_supersedes_previous(authority_file):
    actor = {"id": 7, "name": "authorized admin", "phone": "8801", "source": "admin_api_key"}
    draft = await admin.save_recruitment_draft(
        admin.DraftBody(key="salary.by_role", value="new approved value"), actor
    )
    result = await admin.approve_recruitment_draft(
        admin.ApproveBody(draft_id=draft["draft"]["draft_id"]), actor
    )
    assert result["fact"]["status"] == "current_approved"
    assert result["fact"]["value"] == "new approved value"
    payload = json.loads(authority_file.read_text(encoding="utf-8"))
    fact = payload["facts"][0]
    assert fact["value"] == "new approved value"
    assert fact["status"] == "current_approved"
    assert fact["history"][0]["value"] == "old approved value"
    assert fact["history"][0]["status"] == "superseded"
    assert payload["drafts"][0]["status"] == "approved"
    from modules.recruitment_authority import load_recruitment_authority
    approved = {fact.key: fact for fact in load_recruitment_authority()}["salary.by_role"]
    assert approved.answerable is True
    assert approved.value == "new approved value"


@pytest.mark.asyncio
async def test_stale_approval_is_rejected(authority_file):
    actor = {"id": 7, "name": "authorized admin", "phone": "8801", "source": "admin_api_key"}
    first = await admin.save_recruitment_draft(admin.DraftBody(key="salary.by_role", value="first"), actor)
    second = await admin.save_recruitment_draft(
        admin.DraftBody(key="salary.by_role", value="stale"), actor
    )
    await admin.approve_recruitment_draft(admin.ApproveBody(draft_id=first["draft"]["draft_id"]), actor)
    with pytest.raises(HTTPException) as exc:
        await admin.approve_recruitment_draft(
            admin.ApproveBody(draft_id=second["draft"]["draft_id"], expected_revision=second["draft"].get("base_revision")), actor
        )
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_missing_or_fake_client_identity_cannot_authorize(monkeypatch):
    with pytest.raises(HTTPException) as exc:
        await admin._authorize(None, admin._WRITE_COMMAND)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_viewer_cannot_write_and_admin_identity_is_server_derived(monkeypatch):
    monkeypatch.setattr(admin, "_audit", AsyncMock())
    monkeypatch.setattr(admin.rbac, "check_permission", AsyncMock(return_value={
        "allowed": False, "admin": {"id": 3, "phone": "8802", "name": "viewer"},
        "required_role": "admin", "reason": "need admin",
    }))
    with pytest.raises(HTTPException) as denied:
        await admin._authorize("viewer-key", admin._WRITE_COMMAND)
    assert denied.value.status_code == 403

    monkeypatch.setattr(admin.rbac, "check_permission", AsyncMock(return_value={
        "allowed": True, "admin": {"id": 8, "phone": "8803", "name": "real admin"},
    }))
    actor = await admin._authorize("admin-key", admin._WRITE_COMMAND)
    assert actor["id"] == 8
    assert actor["name"] == "real admin"
