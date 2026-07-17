from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_unknown_phone_backed_operational_message_is_not_seeded(monkeypatch):
    """_seed_unknown_employee_identity() is disabled (Owner directive, Session
    20 Phase 1, 2026-07-17): every sender reaching this function already
    failed identity_brain's admin/accountant/escort-client/employee/contact
    checks, so auto-creating a row here is exactly the "random/unapproved
    number" auto-create Owner Rule D prohibits. This test proves the guard is
    an unconditional no-op -- even input shaped to match the OLD trigger
    conditions (unknown role + an advance-request-shaped message) must
    return the identity dict unchanged with seeded=False, and must not touch
    the database at all (fetch_one/fetch_val/execute/match_or_create_employee
    all zero calls)."""
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

    original_identity = {"role": "unknown", "identity_confidence": 10, "identity_source": "phone"}

    updated_identity, seeded = await router._seed_unknown_employee_identity(
        "8801712345678",
        "অগ্রিম চাই",
        original_identity,
    )

    assert seeded is False
    assert updated_identity is original_identity
    assert fetch_one.await_count == 0
    assert fetch_val_mock.await_count == 0
    assert execute.await_count == 0
