"""
Unit tests for POST /admin/send-whatsapp (2026-08-10, Hermes Capability
Expansion Level 2). Covers:
  - non-superadmin caller -> 403, outbound.enqueue never called
  - missing recipient/body -> 400
  - happy path -> 200, enqueue called with the right args, audit logged
    pairing admin_instruction with the exact recipient+body sent
  - internal key (owner) bypasses the RBAC lookup entirely, same as every
    other require_command route
"""
import pytest
from unittest.mock import AsyncMock

pytestmark = pytest.mark.asyncio


async def test_non_superadmin_caller_denied(monkeypatch):
    from httpx import ASGITransport, AsyncClient
    from app.main import app
    from modules import rbac, outbound

    async def fake_get_admin_by_api_key(_key: str):
        return {"id": 5, "phone": "8801700000005", "status": "active", "name": "admin-tier-user"}

    async def fake_check_permission(*, api_key: str, command: str):
        assert command == "send_whatsapp_message"
        return {
            "allowed": False,
            "required_role": "superadmin",
            "reason": "Insufficient role",
            "admin": {"id": 5, "phone": "8801700000005", "status": "active", "name": "admin-tier-user"},
        }

    monkeypatch.setattr(rbac, "get_admin_by_api_key", fake_get_admin_by_api_key)
    monkeypatch.setattr(rbac, "check_permission", fake_check_permission)
    monkeypatch.setattr(rbac, "record_audit", AsyncMock())
    mock_enqueue = AsyncMock()
    monkeypatch.setattr(outbound, "enqueue", mock_enqueue)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        headers={"X-Internal-Key": "non-owner-test-key"},
    ) as ac:
        response = await ac.post(
            "/admin/send-whatsapp",
            json={"recipient": "8801999999999", "body": "hello"},
        )

    assert response.status_code == 403
    mock_enqueue.assert_not_called()


async def test_superadmin_missing_fields_rejected(monkeypatch):
    from httpx import ASGITransport, AsyncClient
    from app.main import app
    from app.config import get_settings

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
        response = await ac.post(
            "/admin/send-whatsapp",
            json={"recipient": "", "body": ""},
            headers={"X-Internal-Key": get_settings().internal_api_key},
        )

    assert response.status_code == 400


async def test_owner_internal_key_sends_and_audits_with_instruction_pairing(monkeypatch):
    from httpx import ASGITransport, AsyncClient
    from app.main import app
    from app.config import get_settings
    from modules import outbound, rbac

    mock_enqueue = AsyncMock(return_value=999)
    mock_audit = AsyncMock()
    monkeypatch.setattr(outbound, "enqueue", mock_enqueue)
    monkeypatch.setattr(rbac, "record_audit", mock_audit)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
        response = await ac.post(
            "/admin/send-whatsapp",
            json={
                "recipient": "8801999999999",
                "body": "Amar number e EKTA test message",
                "source_bridge": "bridge2",
                "admin_instruction": "Amar number e EKTA test message pathao",
            },
            headers={"X-Internal-Key": get_settings().internal_api_key},
        )

    assert response.status_code == 200
    body = response.json()
    assert body == {"ok": True, "queue_id": 999}

    mock_enqueue.assert_awaited_once_with(
        "8801999999999",
        "Amar number e EKTA test message",
        source_bridge="bridge2",
        purpose="hermes_admin_send",
    )

    mock_audit.assert_awaited_once()
    _, kwargs = mock_audit.await_args
    assert kwargs["command"] == "send_whatsapp_message"
    assert kwargs["allowed"] is True
    assert "Amar number e EKTA test message pathao" in kwargs["args"]
    assert "8801999999999" in kwargs["result_summary"]
    assert "Amar number e EKTA test message" in kwargs["result_summary"]


async def test_default_source_bridge_is_bridge2(monkeypatch):
    from httpx import ASGITransport, AsyncClient
    from app.main import app
    from app.config import get_settings
    from modules import outbound, rbac

    mock_enqueue = AsyncMock(return_value=1000)
    monkeypatch.setattr(outbound, "enqueue", mock_enqueue)
    monkeypatch.setattr(rbac, "record_audit", AsyncMock())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
        response = await ac.post(
            "/admin/send-whatsapp",
            json={"recipient": "8801999999998", "body": "hi"},
            headers={"X-Internal-Key": get_settings().internal_api_key},
        )

    assert response.status_code == 200
    _, kwargs = mock_enqueue.await_args
    assert kwargs["source_bridge"] == "bridge2"
