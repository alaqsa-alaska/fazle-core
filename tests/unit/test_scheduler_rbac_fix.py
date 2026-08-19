"""
Unit tests for POST /scheduler/run/{job_name} (2026-08-19, capability-audit
RBAC gap fix).

Found during the BUILD/RUN/business-action capability audit
(project_hermes_earth_rename_autonomy_20260819 memory): this route only
checked require_api_key -- any valid API key, even a low-privilege
viewer-tier admin's, could trigger any scheduled job. The identical action
via the WhatsApp `SCHEDULE RUN <job_name>` command is genuinely RBAC-gated
(COMMAND_ROLE["schedule_run"] = "admin", enforced centrally in
process_admin_command() before dispatch). This test proves the HTTP route
now enforces the same require_command("schedule_run") gate, mirroring
test_send_whatsapp_message.py's test_non_superadmin_caller_denied shape
exactly.
"""
import pytest
from unittest.mock import AsyncMock

pytestmark = pytest.mark.asyncio


async def test_insufficient_role_denied(monkeypatch):
    from httpx import ASGITransport, AsyncClient
    from app.main import app
    from modules import rbac

    async def fake_get_admin_by_api_key(_key: str):
        return {"id": 7, "phone": "8801700000007", "status": "active", "name": "viewer-tier-user"}

    async def fake_check_permission(*, api_key: str, command: str):
        assert command == "schedule_run"
        return {
            "allowed": False,
            "required_role": "admin",
            "reason": "Insufficient role",
            "admin": {"id": 7, "phone": "8801700000007", "status": "active", "name": "viewer-tier-user"},
        }

    monkeypatch.setattr(rbac, "get_admin_by_api_key", fake_get_admin_by_api_key)
    monkeypatch.setattr(rbac, "check_permission", fake_check_permission)
    monkeypatch.setattr(rbac, "record_audit", AsyncMock())

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        headers={"X-Internal-Key": "non-owner-test-key"},
    ) as ac:
        response = await ac.post("/scheduler/run/health_summary")

    assert response.status_code == 403


async def test_no_key_at_all_rejected():
    from httpx import ASGITransport, AsyncClient
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
        response = await ac.post("/scheduler/run/health_summary")

    assert response.status_code == 403


async def test_admin_role_allowed_reaches_trigger_job(monkeypatch):
    from httpx import ASGITransport, AsyncClient
    from app.main import app
    from modules import rbac
    from modules import scheduler as fazle_scheduler

    async def fake_get_admin_by_api_key(_key: str):
        return {"id": 8, "phone": "8801700000008", "status": "active", "name": "admin-tier-user"}

    async def fake_check_permission(*, api_key: str, command: str):
        assert command == "schedule_run"
        return {"allowed": True, "admin": {"id": 8, "phone": "8801700000008", "status": "active"}}

    monkeypatch.setattr(rbac, "get_admin_by_api_key", fake_get_admin_by_api_key)
    monkeypatch.setattr(rbac, "check_permission", fake_check_permission)
    monkeypatch.setattr(fazle_scheduler, "list_job_names", lambda: ["health_summary"])
    trigger_mock = AsyncMock(return_value={"status": "ok"})
    monkeypatch.setattr(fazle_scheduler, "trigger_job", trigger_mock)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        headers={"X-Internal-Key": "admin-test-key"},
    ) as ac:
        response = await ac.post("/scheduler/run/health_summary")

    assert response.status_code == 200
    trigger_mock.assert_awaited_once_with("health_summary")
