"""Unit tests — modules/client_billing/profiles (Phase 2B, Client Billing Profile)"""
from __future__ import annotations

from unittest.mock import patch

import pytest

pytestmark = pytest.mark.unit

AUTHORIZED = "8801670535255"
UNAUTHORIZED = "8801999999999"


def _patch_authorized_list():
    """Patch the authorized-number list to a known, fixed set for these tests."""
    return patch(
        "modules.client_billing.profiles._escort_client_phone_list",
        return_value=[AUTHORIZED],
    )


class TestContactMobileValidation:
    @pytest.mark.asyncio
    async def test_rejects_unauthorized_number(self, test_db_pool):
        import app.database as db_module
        db_module._pool = test_db_pool
        from fastapi import HTTPException
        from modules.client_billing.profiles import create_client_billing_profile

        with _patch_authorized_list():
            with pytest.raises(HTTPException) as exc:
                await create_client_billing_profile("Test Co", UNAUTHORIZED)
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_accepts_authorized_number(self, test_db_pool):
        import app.database as db_module
        db_module._pool = test_db_pool
        from modules.client_billing.profiles import create_client_billing_profile

        with _patch_authorized_list():
            profile = await create_client_billing_profile("Test Co", AUTHORIZED)
        assert profile["contact_person_mobile"] == AUTHORIZED
        assert profile["client_name"] == "Test Co"


class TestCrudRoundTrip:
    @pytest.mark.asyncio
    async def test_create_list_get_update_delete(self, test_db_pool):
        import app.database as db_module
        db_module._pool = test_db_pool
        from modules.client_billing.profiles import (
            create_client_billing_profile,
            delete_client_billing_profile,
            get_client_billing_profile,
            list_client_billing_profiles,
            update_client_billing_profile,
        )

        with _patch_authorized_list():
            created = await create_client_billing_profile(
                "Round Trip Shipping", AUTHORIZED, general_rate=5000, employee_wage_general=3000,
            )
        client_id = created["client_id"]
        assert float(created["employee_wage_general"]) == 3000

        fetched = await get_client_billing_profile(client_id)
        assert fetched["client_name"] == "Round Trip Shipping"

        active_list = await list_client_billing_profiles(active_only=True)
        assert any(p["client_id"] == client_id for p in active_list)

        updated = await update_client_billing_profile(client_id, general_rate=6000)
        assert float(updated["general_rate"]) == 6000

        # Hard delete (Owner decision, 2026-07-10 — supersedes the original
        # soft-delete default) — row is fully removed, not just deactivated.
        deleted = await delete_client_billing_profile(client_id)
        assert deleted["client_id"] == client_id

        all_after = await list_client_billing_profiles(active_only=False)
        assert not any(p["client_id"] == client_id for p in all_after)

        assert await get_client_billing_profile(client_id) is None


class TestUniquenessConstraint:
    @pytest.mark.asyncio
    async def test_duplicate_mobile_rejected(self, test_db_pool):
        import app.database as db_module
        db_module._pool = test_db_pool
        from fastapi import HTTPException
        from modules.client_billing.profiles import create_client_billing_profile

        with _patch_authorized_list():
            await create_client_billing_profile("First Co", AUTHORIZED)
            with pytest.raises(HTTPException) as exc:
                await create_client_billing_profile("Second Co", AUTHORIZED)
        assert exc.value.status_code == 409


class TestForeignKeyValidation:
    @pytest.mark.asyncio
    async def test_nonexistent_contact_id_rejected(self, test_db_pool):
        import app.database as db_module
        db_module._pool = test_db_pool
        from fastapi import HTTPException
        from modules.client_billing.profiles import create_client_billing_profile

        with _patch_authorized_list():
            with pytest.raises(HTTPException) as exc:
                await create_client_billing_profile(
                    "Bad FK Co", AUTHORIZED, contact_id=999999999,
                )
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_valid_contact_id_accepted(self, test_db_pool):
        import app.database as db_module
        db_module._pool = test_db_pool
        from modules.client_billing.profiles import create_client_billing_profile

        async with test_db_pool.acquire() as conn:
            contact = await conn.fetchrow(
                "INSERT INTO wbom_contacts (whatsapp_number) VALUES ($1) RETURNING contact_id",
                AUTHORIZED,
            )

        with _patch_authorized_list():
            profile = await create_client_billing_profile(
                "Linked Co", AUTHORIZED, contact_id=contact["contact_id"],
            )
        assert profile["contact_id"] == contact["contact_id"]


class TestNonNegativeValidation:
    @pytest.mark.asyncio
    async def test_negative_rate_rejected(self, test_db_pool):
        import app.database as db_module
        db_module._pool = test_db_pool
        from fastapi import HTTPException
        from modules.client_billing.profiles import create_client_billing_profile

        with _patch_authorized_list():
            with pytest.raises(HTTPException) as exc:
                await create_client_billing_profile(
                    "Negative Co", AUTHORIZED, general_rate=-100,
                )
        assert exc.value.status_code == 400


class TestRoleGate:
    """Owner decision 2026-07-10: client rates/wages/profit visible to
    admin+superadmin only — accountant/operator/viewer must be rejected even
    with an otherwise-valid, active API key."""

    @pytest.mark.asyncio
    async def test_operator_rejected_by_admin_or_above(self):
        from fastapi import HTTPException
        from modules.client_billing.routes import _require_admin_or_above

        with patch(
            "modules.client_billing.routes._resolve_actor",
            return_value={"is_internal": False, "roles": ["operator"]},
        ):
            with pytest.raises(HTTPException) as exc:
                await _require_admin_or_above("fake-operator-key")
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_accountant_rejected_by_admin_or_above(self):
        from fastapi import HTTPException
        from modules.client_billing.routes import _require_admin_or_above

        with patch(
            "modules.client_billing.routes._resolve_actor",
            return_value={"is_internal": False, "roles": ["accountant"]},
        ):
            with pytest.raises(HTTPException) as exc:
                await _require_admin_or_above("fake-accountant-key")
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_admin_accepted_by_admin_or_above(self):
        from modules.client_billing.routes import _require_admin_or_above

        with patch(
            "modules.client_billing.routes._resolve_actor",
            return_value={"is_internal": False, "roles": ["admin"]},
        ):
            actor = await _require_admin_or_above("fake-admin-key")
        assert actor["roles"] == ["admin"]

    @pytest.mark.asyncio
    async def test_superadmin_accepted_by_admin_or_above(self):
        from modules.client_billing.routes import _require_admin_or_above

        with patch(
            "modules.client_billing.routes._resolve_actor",
            return_value={"is_internal": False, "roles": ["superadmin"]},
        ):
            actor = await _require_admin_or_above("fake-superadmin-key")
        assert actor["roles"] == ["superadmin"]

    @pytest.mark.asyncio
    async def test_admin_rejected_by_require_superadmin(self):
        from fastapi import HTTPException
        from modules.client_billing.routes import _require_superadmin

        with patch(
            "modules.client_billing.routes._resolve_actor",
            return_value={"is_internal": False, "roles": ["admin"]},
        ):
            with pytest.raises(HTTPException) as exc:
                await _require_superadmin("fake-admin-key")
        assert exc.value.status_code == 403
