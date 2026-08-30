"""
Phase 2 (Owner directive 2026-08-30) — escort_worker vs escort_client.

  escort_worker  — our employee / security escort assigned to a vessel
  escort_client  — a customer buying Escort Service from us

Regression guarantees:
  * a contact NAME starting with "Escort" does NOT by itself imply
    escort_client — real employee evidence resolves it to the WORKER;
  * with no employee evidence AND no client/order record, the name prefix
    alone must NOT produce escort_client;
  * a genuine escort-service client (ESCORT_CLIENT_PHONES) is still a client;
  * the canonical vocabulary helpers behave.

Mocked — no DB.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch


# ── canonical vocabulary ────────────────────────────────────────────────────

def test_escort_terms_canonicalisation():
    from shared.escort_terms import (
        canonical_escort_role, is_escort_worker_role, is_escort_client_role,
        escort_domain_for_role, ESCORT_WORKER, ESCORT_CLIENT,
        ESCORT_ASSIGNMENT_DOMAIN, ESCORT_ORDER_DOMAIN,
    )
    assert canonical_escort_role("escort") == ESCORT_WORKER
    assert canonical_escort_role("escort_worker") == ESCORT_WORKER
    assert canonical_escort_role("escort_client") == ESCORT_CLIENT
    assert canonical_escort_role("admin") == "admin"
    assert canonical_escort_role(None) is None

    assert is_escort_worker_role("escort") is True
    assert is_escort_worker_role("escort_worker") is True
    assert is_escort_worker_role("escort_client") is False
    assert is_escort_worker_role("client_escort_buyer") is False

    assert is_escort_client_role("escort_client") is True
    assert is_escort_client_role("client_escort_buyer") is True
    assert is_escort_client_role("escort") is False

    assert escort_domain_for_role("escort") == ESCORT_ASSIGNMENT_DOMAIN
    assert escort_domain_for_role("escort_client") == ESCORT_ORDER_DOMAIN
    assert escort_domain_for_role("admin") is None


# ── name-evidence lookup helper ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_lookup_employee_by_name_strips_escort_prefix():
    from modules.identity_brain import _lookup_employee_by_name

    seen: list = []

    async def fake_fetch_one(query, *args, **kwargs):
        seen.append(args[0] if args else None)
        if args and args[0].lower() == "saimun":
            return {"employee_id": 11, "employee_name": "Saimun",
                    "designation": "Escort", "status": "Active"}
        return None

    with patch("modules.identity_brain.fetch_one", new=AsyncMock(side_effect=fake_fetch_one)):
        row = await _lookup_employee_by_name("Escort Saimun")

    assert row is not None and row["employee_id"] == 11
    assert "saimun" in [s.lower() for s in seen if s]


@pytest.mark.asyncio
async def test_lookup_employee_by_name_none_for_unknown():
    from modules.identity_brain import _lookup_employee_by_name
    with patch("modules.identity_brain.fetch_one", new=AsyncMock(return_value=None)):
        assert await _lookup_employee_by_name("Escort Nobody") is None


# ── detect_identity Step 4.5 disambiguation ─────────────────────────────────

def _no_evidence_patches():
    """Patch out every identity source ABOVE Step 4.5 so a phone reaches the
    CSV-pattern branch cleanly."""
    return [
        patch("modules.identity_brain._lookup_employee", new=AsyncMock(return_value=None)),
        patch("modules.identity_brain._lookup_cash_identity", new=AsyncMock(return_value=None)),
        patch("modules.identity_brain._lookup_attendance_identity", new=AsyncMock(return_value=None)),
        patch("modules.identity_brain._lookup_escort_roster", new=AsyncMock(return_value=None)),
        patch("modules.identity_brain._lookup_contact", new=AsyncMock(return_value=None)),
        patch("modules.identity_brain._matches_env_escort_phone", new=lambda p: False),
        patch("modules.identity_brain.fetch_one", new=AsyncMock(return_value=None)),
    ]


@pytest.mark.asyncio
async def test_escort_named_contact_with_employee_evidence_is_worker():
    from modules.identity_brain import detect_identity

    ctx = _no_evidence_patches()
    for p in ctx:
        p.start()
    try:
        with patch("modules.owner_contact_registry.is_owner_managed_contact", new=lambda p: True), \
             patch("modules.contact_sync.get_display_name", new=AsyncMock(return_value="Escort Saimun")), \
             patch("modules.identity_brain._lookup_employee_by_name",
                   new=AsyncMock(return_value={"employee_id": 11, "employee_name": "Saimun",
                                               "designation": "Escort", "status": "Active"})):
            result = await detect_identity("8801933333333", "boss kal duty ache?")
    finally:
        for p in ctx:
            p.stop()

    assert result["role"] == "escort", f"worker mis-classified: {result}"
    assert result["role"] != "escort_client"


@pytest.mark.asyncio
async def test_escort_named_contact_without_any_evidence_is_not_client_from_prefix():
    from modules.identity_brain import detect_identity

    ctx = _no_evidence_patches()
    for p in ctx:
        p.start()
    try:
        with patch("modules.owner_contact_registry.is_owner_managed_contact", new=lambda p: True), \
             patch("modules.contact_sync.get_display_name", new=AsyncMock(return_value="Escort Random Person")), \
             patch("modules.identity_brain._lookup_employee_by_name", new=AsyncMock(return_value=None)):
            result = await detect_identity("8801944444444", "hello")
    finally:
        for p in ctx:
            p.stop()

    # must NOT have guessed escort_client purely from the "Escort " prefix
    assert result["role"] != "escort_client", f"prefix alone produced a client: {result}"


@pytest.mark.asyncio
async def test_genuine_escort_client_phone_still_classified_client():
    from modules.identity_brain import detect_identity

    ctx = [
        patch("modules.identity_brain._lookup_employee", new=AsyncMock(return_value=None)),
        patch("modules.identity_brain._lookup_cash_identity", new=AsyncMock(return_value=None)),
        patch("modules.identity_brain._lookup_attendance_identity", new=AsyncMock(return_value=None)),
        patch("modules.identity_brain._lookup_escort_roster", new=AsyncMock(return_value=None)),
        patch("modules.identity_brain._lookup_contact", new=AsyncMock(return_value=None)),
        patch("modules.identity_brain.fetch_one", new=AsyncMock(return_value=None)),
        patch("modules.identity_brain._matches_env_escort_phone", new=lambda p: True),
    ]
    for p in ctx:
        p.start()
    try:
        result = await detect_identity("8801955555555", "M.V lagbe")
    finally:
        for p in ctx:
            p.stop()

    assert result["role"] == "client_escort_buyer"
