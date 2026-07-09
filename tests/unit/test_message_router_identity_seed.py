from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_unknown_phone_backed_operational_message_is_seeded(monkeypatch):
    import modules.message_router as router

    monkeypatch.setattr(router, "normalize_phone", lambda _sender: "8801712345678")
    monkeypatch.setattr(router, "normalize_bd_phone", lambda _sender: None)
    monkeypatch.setattr(router, "classify", lambda _text: "unknown")
    monkeypatch.setattr(router, "is_advance_request", lambda _text: True)
    monkeypatch.setattr(router, "is_release_intent", lambda _text: False)
    monkeypatch.setattr(router, "is_attendance_message", lambda _text: False)
    monkeypatch.setattr(router, "is_supervisor_attendance", lambda _text: False)
    monkeypatch.setattr(router, "ec_detect_trigger", lambda _text: False)
    monkeypatch.setattr(router, "_looks_like_escort_order", lambda _text: False)
    monkeypatch.setattr(router, "_looks_like_escort_buying_intent", lambda _text: False)
    monkeypatch.setattr(router, "is_completed_escort_draft", lambda _text: False)

    fetch_one = AsyncMock(side_effect=[None, {
        "employee_id": 9,
        "employee_name": "Rahim",
        "designation": "Staff",
        "basic_salary": 10000,
        "status": "Active",
    }])
    monkeypatch.setattr(router, "fetch_one", fetch_one)
    fetch_val_mock = AsyncMock(return_value=9)
    monkeypatch.setattr(router, "fetch_val", fetch_val_mock)
    execute = AsyncMock()
    monkeypatch.setattr(router, "execute", execute)
    monkeypatch.setattr(router, "match_or_create_employee", AsyncMock(return_value=SimpleNamespace(employee_id=77)))
    monkeypatch.setattr(
        router,
        "detect_identity",
        AsyncMock(return_value={
            "role": "employee",
            "identity_role": "employee",
            "identity_confidence": 60,
            "identity_source": "message_router_auto_create",
            "employee_id": 9,
            "employee_name": "Rahim",
            "designation": "Staff",
            "basic_salary": 10000,
        }),
    )

    updated_identity, seeded = await router._seed_unknown_employee_identity(
        "8801712345678",
        "অগ্রিম চাই",
        {"role": "unknown", "identity_confidence": 10, "identity_source": "phone"},
    )

    assert seeded is True
    assert updated_identity["role"] == "employee"
    assert updated_identity["employee_id"] == 9
    assert updated_identity["identity_source"] == "message_router_auto_create"
    assert execute.await_count == 1
    assert fetch_val_mock.await_count == 1
