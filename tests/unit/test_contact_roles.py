"""
Unit tests — modules/contact_roles routes.

Covers the API-boundary role whitelist added 2026-07-17 (Phase 1B): only
descriptive/seeded contact roles may be assigned through this endpoint,
never functional-authorization or identity roles (admin/accountant/
employee/family) or the removed 'blocked' value — those come from .env or
their own canonical domain, per CANONICAL_BUSINESS_RULES.md's .env-authority
Structural Rule. See knowledge_base/00_governance/management_decisions.md
for the approved policy this test file asserts.
"""
from __future__ import annotations

import pytest

from tests.conftest import TEST_API_KEY

pytestmark = pytest.mark.unit

_ALLOWED_ROLES = [
    "vip_client",
    "client_escort_buyer",
    "repeat_client",
    "vendor",
    "supervisor",
    "candidate",
]

_REJECTED_ROLES = [
    "admin",
    "accountant",
    "employee",
    "family",
    "blocked",
    "client",
    "unknown",
    "ADMIN",
    "Vip_Client",
    "",
    "   ",
]


def _headers():
    return {"X-Internal-Key": TEST_API_KEY}


class TestPostAllowedRoles:
    @pytest.mark.parametrize("role", _ALLOWED_ROLES)
    async def test_allowed_role_accepted(self, client, role):
        phone = f"88017{hash(role) % 100000000:08d}"
        r = await client.post(
            "/admin/contact-roles/",
            json={"phone": phone, "role": role},
            headers=_headers(),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["role"] == role
        assert body["phone"] == phone

        # Confirm the canonical value actually landed in storage, not just
        # in the response echo.
        listed = await client.get(
            "/admin/contact-roles/", params={"phone": phone}, headers=_headers()
        )
        assert listed.status_code == 200
        items = listed.json()["items"]
        assert len(items) == 1
        assert items[0]["role"] == role


class TestPostRejectedRoles:
    @pytest.mark.parametrize("role", _REJECTED_ROLES)
    async def test_rejected_role_returns_422(self, client, role):
        r = await client.post(
            "/admin/contact-roles/",
            json={"phone": "8801799999999", "role": role},
            headers=_headers(),
        )
        assert r.status_code == 422, f"role={role!r} should be rejected, got {r.status_code}: {r.text}"

        # Confirm nothing was inserted.
        listed = await client.get(
            "/admin/contact-roles/",
            params={"phone": "8801799999999"},
            headers=_headers(),
        )
        assert listed.json()["items"] == []

    async def test_whitespace_padded_allowed_role_is_accepted(self, client):
        """Outer whitespace is stripped before validation; the stored value
        is the canonical trimmed form, not the raw padded input."""
        r = await client.post(
            "/admin/contact-roles/",
            json={"phone": "8801788888888", "role": " vendor "},
            headers=_headers(),
        )
        assert r.status_code == 200, r.text
        assert r.json()["role"] == "vendor"

    async def test_whitespace_padded_wrong_case_role_is_rejected(self, client):
        """Whitespace stripping must not become a backdoor for case drift —
        ' Vendor ' strips to 'Vendor', which still isn't the canonical
        'vendor' and must 422."""
        r = await client.post(
            "/admin/contact-roles/",
            json={"phone": "8801788888888", "role": " Vendor "},
            headers=_headers(),
        )
        assert r.status_code == 422, r.text


class TestPutBehavior:
    async def _seed(self, client, phone: str, role: str = "vendor"):
        r = await client.post(
            "/admin/contact-roles/",
            json={"phone": phone, "role": role},
            headers=_headers(),
        )
        assert r.status_code == 200, r.text

    async def test_allowed_role_update(self, client):
        phone = "8801766666601"
        await self._seed(client, phone, "vendor")
        r = await client.put(
            f"/admin/contact-roles/{phone}",
            json={"role": "candidate"},
            headers=_headers(),
        )
        assert r.status_code == 200, r.text
        assert r.json()["role"] == "candidate"

    async def test_forbidden_role_update_rejected(self, client):
        phone = "8801766666602"
        await self._seed(client, phone, "vendor")
        r = await client.put(
            f"/admin/contact-roles/{phone}",
            json={"role": "admin"},
            headers=_headers(),
        )
        assert r.status_code == 422, r.text

        # Role must be unchanged.
        listed = await client.get(
            "/admin/contact-roles/", params={"phone": phone}, headers=_headers()
        )
        assert listed.json()["items"][0]["role"] == "vendor"

    async def test_unknown_role_update_rejected(self, client):
        phone = "8801766666603"
        await self._seed(client, phone, "vendor")
        r = await client.put(
            f"/admin/contact-roles/{phone}",
            json={"role": "not_a_real_role"},
            headers=_headers(),
        )
        assert r.status_code == 422, r.text

    async def test_wrong_case_role_update_rejected(self, client):
        phone = "8801766666604"
        await self._seed(client, phone, "vendor")
        r = await client.put(
            f"/admin/contact-roles/{phone}",
            json={"role": "Candidate"},
            headers=_headers(),
        )
        assert r.status_code == 422, r.text

    async def test_blank_role_update_rejected(self, client):
        phone = "8801766666605"
        await self._seed(client, phone, "vendor")
        r = await client.put(
            f"/admin/contact-roles/{phone}",
            json={"role": ""},
            headers=_headers(),
        )
        assert r.status_code == 422, r.text

    async def test_role_omitted_preserves_existing_role_while_updating_other_field(self, client):
        """The mandatory legacy-compatibility case: updating priority (or any
        other field) without sending `role` at all must not touch role,
        and must not be rejected just because a stored legacy role exists
        outside the new enum (see next test)."""
        phone = "8801766666606"
        await self._seed(client, phone, "vendor")
        r = await client.put(
            f"/admin/contact-roles/{phone}",
            json={"priority": 42},
            headers=_headers(),
        )
        assert r.status_code == 200, r.text
        assert r.json()["role"] == "vendor"

        listed = await client.get(
            "/admin/contact-roles/", params={"phone": phone}, headers=_headers()
        )
        assert listed.json()["items"][0]["priority"] == 42

    async def test_role_omitted_preserves_preexisting_legacy_forbidden_role(self, client, test_db_pool):
        """Rows with role='accountant'/'employee'/'family' already exist in
        production (seeded outside this API, e.g. directly against the
        table). A PUT that omits `role` must be able to update such a row
        without being rejected merely because the *stored* value is now
        outside the API-assignable enum — the enum only gates newly
        *supplied* values, per the approved Phase 1B policy."""
        phone = "8801766666607"
        # Seed a legacy forbidden role directly at the DB layer, bypassing
        # this API entirely — exactly how the real production rows arose.
        async with test_db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO fazle_contact_roles
                  (phone, platform, role, priority, source, is_active, confidence)
                VALUES ($1, 'whatsapp', 'family', 100, 'legacy_seed', true, 90)
                """,
                phone,
            )

        r = await client.put(
            f"/admin/contact-roles/{phone}",
            json={"notes": "reviewed, no role change"},
            headers=_headers(),
        )
        assert r.status_code == 200, r.text
        assert r.json()["role"] == "family"

    async def test_nonexistent_phone_returns_404(self, client):
        r = await client.put(
            "/admin/contact-roles/8801700000000",
            json={"priority": 5},
            headers=_headers(),
        )
        assert r.status_code == 404


class TestAuthorizationUnchanged:
    async def test_missing_key_rejected(self, client):
        r = await client.post(
            "/admin/contact-roles/",
            json={"phone": "8801755555555", "role": "vendor"},
            headers={"X-Internal-Key": ""},
        )
        assert r.status_code == 403

    async def test_invalid_key_rejected(self, client):
        r = await client.post(
            "/admin/contact-roles/",
            json={"phone": "8801755555555", "role": "vendor"},
            headers={"X-Internal-Key": "not-a-real-key"},
        )
        assert r.status_code == 403

    async def test_valid_key_accepted(self, client):
        r = await client.post(
            "/admin/contact-roles/",
            json={"phone": "8801755555556", "role": "vendor"},
            headers=_headers(),
        )
        assert r.status_code == 200
