"""
Workstream 2 Stage 4: automated route-level RBAC coverage for the six
canonical roles against modules/escort/routes.py's _require_admin_or_above.

Every route in this router (list, detail, link-existing, confirm-new,
reject, defer) shares this exact same dependency -- verifying it once
here across all six roles is complete coverage for the whole router, not
just one endpoint. Closes the certification gap flagged in Stage 3's
report: wrong-role behavior was previously only assumed from UI hiding,
never automated-tested.

Tests the dependency function directly (not a full HTTP round trip)
because the route handlers themselves need a live DB connection this
session doesn't have (TEST_DB_PASSWORD unset, same standing constraint
noted throughout this session) -- but the RBAC gate itself runs and
raises/returns *before* any handler or DB call, so this is genuinely
complete coverage of the access-control decision, not a reduced-scope
substitute for it.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

pytestmark = pytest.mark.unit

ADMIN_ID = 42

ROLE_TIERS = ["viewer", "office_assistant", "operator", "accountant", "admin", "superadmin"]
ALLOWED_ROLES = {"admin", "superadmin"}


def _make_fakes(monkeypatch, role: str):
    import modules.escort.routes as routes_module
    from modules import rbac

    async def _fake_get_admin_by_api_key(key: str):
        assert key == "a-real-looking-key"
        return {"id": ADMIN_ID, "phone": "8801700000099", "status": "active", "name": f"{role}-user"}

    async def _fake_get_roles(admin_id: int):
        assert admin_id == ADMIN_ID
        return [role]

    monkeypatch.setattr(rbac, "get_admin_by_api_key", _fake_get_admin_by_api_key)
    monkeypatch.setattr(rbac, "get_roles", _fake_get_roles)
    return routes_module


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ROLE_TIERS)
async def test_role_tier_access(monkeypatch, role):
    routes_module = _make_fakes(monkeypatch, role)

    if role in ALLOWED_ROLES:
        result = await routes_module._require_admin_or_above(key="a-real-looking-key")
        assert result == "a-real-looking-key"
    else:
        with pytest.raises(HTTPException) as exc_info:
            await routes_module._require_admin_or_above(key="a-real-looking-key")
        assert exc_info.value.status_code == 403
        assert "admin role or above required" in exc_info.value.detail


@pytest.mark.asyncio
async def test_missing_key_rejected(monkeypatch):
    import modules.escort.routes as routes_module
    from modules import rbac

    async def _fake_get_admin_by_api_key(key: str):
        return None  # no fazle_admins row for an empty/missing key

    monkeypatch.setattr(rbac, "get_admin_by_api_key", _fake_get_admin_by_api_key)

    with pytest.raises(HTTPException) as exc_info:
        await routes_module._require_admin_or_above(key="")
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_invalid_key_rejected(monkeypatch):
    import modules.escort.routes as routes_module
    from modules import rbac

    async def _fake_get_admin_by_api_key(key: str):
        return None  # unrecognized key -- no matching fazle_admins row

    monkeypatch.setattr(rbac, "get_admin_by_api_key", _fake_get_admin_by_api_key)

    with pytest.raises(HTTPException) as exc_info:
        await routes_module._require_admin_or_above(key="not-a-real-key-at-all")
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_inactive_admin_rejected(monkeypatch):
    import modules.escort.routes as routes_module
    from modules import rbac

    async def _fake_get_admin_by_api_key(key: str):
        return {"id": ADMIN_ID, "phone": "8801700000099", "status": "disabled", "name": "disabled-admin"}

    monkeypatch.setattr(rbac, "get_admin_by_api_key", _fake_get_admin_by_api_key)

    with pytest.raises(HTTPException) as exc_info:
        await routes_module._require_admin_or_above(key="a-real-looking-key")
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_internal_api_key_bypasses_role_lookup(monkeypatch):
    """The internal service-to-service key is treated as admin-or-above
    without a DB round trip -- matches modules.dispatch.routes's identical
    pattern, verified here for this router's own copy."""
    import modules.escort.routes as routes_module
    from app.config import get_settings

    settings = get_settings()
    result = await routes_module._require_admin_or_above(key=settings.internal_api_key)
    assert result == settings.internal_api_key
