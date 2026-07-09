import pytest


pytestmark = pytest.mark.asyncio


async def test_t017_unauthorized_role_cannot_list_admin_users(monkeypatch):
    from httpx import ASGITransport, AsyncClient

    from app.main import app
    from modules import rbac

    async def fake_get_admin_by_api_key(_key: str):
        return {"id": 1, "phone": "8801700000003", "status": "active", "name": "viewer-user"}

    async def fake_check_permission(*, api_key: str, command: str):
        assert command == "user_list"
        return {
            "allowed": False,
            "required_role": "superadmin",
            "reason": "Insufficient role",
            "admin": {"id": 1, "phone": "8801700000003", "status": "active", "name": "viewer-user"},
        }

    async def fake_record_audit(**_kwargs):
        return None

    monkeypatch.setattr(rbac, "get_admin_by_api_key", fake_get_admin_by_api_key)
    monkeypatch.setattr(rbac, "check_permission", fake_check_permission)
    monkeypatch.setattr(rbac, "record_audit", fake_record_audit)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        headers={"X-Internal-Key": "non-owner-test-key"},
    ) as ac:
        response = await ac.get("/admin/users")

    assert response.status_code == 403
    assert response.json().get("detail") in {"Insufficient role", "Forbidden"}


async def test_t017_unauthorized_role_cannot_activate_user(monkeypatch):
    from httpx import ASGITransport, AsyncClient

    from app.main import app
    from modules import rbac

    async def fake_get_admin_by_api_key(_key: str):
        return {"id": 2, "phone": "8801700000004", "status": "active", "name": "operator-user"}

    async def fake_check_permission(*, api_key: str, command: str):
        assert command == "user_remove"
        return {
            "allowed": False,
            "required_role": "superadmin",
            "reason": "Insufficient role",
            "admin": {"id": 2, "phone": "8801700000004", "status": "active", "name": "operator-user"},
        }

    async def fake_record_audit(**_kwargs):
        return None

    monkeypatch.setattr(rbac, "get_admin_by_api_key", fake_get_admin_by_api_key)
    monkeypatch.setattr(rbac, "check_permission", fake_check_permission)
    monkeypatch.setattr(rbac, "record_audit", fake_record_audit)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        headers={"X-Internal-Key": "non-owner-test-key"},
    ) as ac:
        response = await ac.post("/admin/users/8801700000009/enable")

    assert response.status_code == 403
    assert response.json().get("detail") in {"Insufficient role", "Forbidden"}
