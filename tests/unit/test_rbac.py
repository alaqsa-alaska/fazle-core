"""Unit tests — modules/rbac"""
from __future__ import annotations

import hashlib
import pytest
from unittest.mock import AsyncMock, patch

pytestmark = pytest.mark.unit


class TestCommandRoleMapping:
    """Every command in COMMAND_ROLE maps to a valid role."""

    def test_command_roles_are_valid(self):
        from modules.rbac import COMMAND_ROLE

        valid_roles = {"viewer", "operator", "accountant", "admin", "superadmin"}
        for cmd, role in COMMAND_ROLE.items():
            assert role in valid_roles, f"Command '{cmd}' has invalid role '{role}'"

    def test_approve_requires_operator(self):
        from modules.rbac import COMMAND_ROLE
        assert COMMAND_ROLE["approve"] == "operator"

    def test_paid_requires_operator(self):
        from modules.rbac import COMMAND_ROLE
        assert COMMAND_ROLE["paid"] == "operator"

    def test_payroll_compute_requires_accountant(self):
        from modules.rbac import COMMAND_ROLE
        assert COMMAND_ROLE.get("payroll_compute") in ("accountant", "admin", "superadmin")

    def test_user_add_requires_superadmin(self):
        from modules.rbac import COMMAND_ROLE
        assert COMMAND_ROLE.get("user_add") == "superadmin"


class TestApiKeyHashing:
    """API keys are stored as SHA-256 hashes."""

    def test_hash_is_sha256(self):
        raw_key = "test-api-key-12345"
        expected = hashlib.sha256(raw_key.encode()).hexdigest()

        from modules.rbac import hash_api_key
        assert hash_api_key(raw_key) == expected

    def test_different_keys_different_hashes(self):
        from modules.rbac import hash_api_key
        assert hash_api_key("key1") != hash_api_key("key2")


class TestPasswordHashing:
    """Passwords use salted PBKDF2 hashes."""

    def test_password_roundtrip(self):
        from modules.rbac import hash_password, verify_password

        hashed = hash_password("S3cure-Pass!")
        assert hashed.startswith("pbkdf2_sha256$")
        assert verify_password("S3cure-Pass!", hashed) is True
        assert verify_password("wrong-pass", hashed) is False


class TestCheckPermission:
    """check_permission() returns True/False based on admin's highest role."""

    async def test_superadmin_can_do_anything(self, test_db_pool):
        import app.database as db_module
        db_module._pool = test_db_pool

        # Seed a superadmin
        async with test_db_pool.acquire() as conn:
            admin = await conn.fetchrow(
                "INSERT INTO fazle_admins (phone, name) VALUES ('8801700000001','SAdmin') RETURNING *"
            )
            await conn.execute(
                "INSERT INTO fazle_admin_roles (admin_id, role_name, granted_by) VALUES ($1,'superadmin','system')",
                admin["id"],
            )

        from modules.rbac import check_permission

        assert (await check_permission(phone="8801700000001", command="user_add"))["allowed"] is True
        assert (await check_permission(phone="8801700000001", command="approve"))["allowed"] is True
        assert (await check_permission(phone="8801700000001", command="status"))["allowed"] is True

    async def test_viewer_cannot_approve(self, test_db_pool):
        import app.database as db_module
        db_module._pool = test_db_pool

        async with test_db_pool.acquire() as conn:
            admin = await conn.fetchrow(
                "INSERT INTO fazle_admins (phone, name) VALUES ('8801700000003','Viewer') RETURNING *"
            )
            await conn.execute(
                "INSERT INTO fazle_admin_roles (admin_id, role_name, granted_by) VALUES ($1,'viewer','system')",
                admin["id"],
            )

        from modules.rbac import check_permission

        assert (await check_permission(phone="8801700000003", command="approve"))["allowed"] is False

    async def test_viewer_can_read_status(self, test_db_pool):
        import app.database as db_module
        db_module._pool = test_db_pool

        async with test_db_pool.acquire() as conn:
            admin = await conn.fetchrow(
                "INSERT INTO fazle_admins (phone, name) VALUES ('8801700000004','ViewerB') RETURNING *"
            )
            await conn.execute(
                "INSERT INTO fazle_admin_roles (admin_id, role_name, granted_by) VALUES ($1,'viewer','system')",
                admin["id"],
            )

        from modules.rbac import check_permission

        assert (await check_permission(phone="8801700000004", command="status"))["allowed"] is True

    async def test_unknown_phone_denied(self, test_db_pool):
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.rbac import check_permission

        assert (await check_permission(phone="8801999999999", command="approve"))["allowed"] is False


class TestRecordAudit:
    """record_audit() writes to fazle_admin_audit."""

    async def test_audit_row_written(self, test_db_pool):
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.rbac import record_audit

        await record_audit(
            channel="test",
            actor_phone="8801700000001",
            command="approve",
            allowed=True,
            required_role="operator",
        )

        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM fazle_admin_audit WHERE actor_phone='8801700000001' LIMIT 1"
            )
        assert row is not None
        assert row["command"] == "approve"
        assert row["allowed"] is True

    async def test_denied_audit_stored(self, test_db_pool):
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.rbac import record_audit

        await record_audit(
            channel="test",
            actor_phone="8801700000005",
            command="user_add",
            allowed=False,
            denied_reason="insufficient role: viewer < superadmin",
        )

        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT allowed FROM fazle_admin_audit WHERE actor_phone='8801700000005' LIMIT 1"
            )
        assert row["allowed"] is False


class TestGetAdminByApiKey:
    """get_admin_by_api_key() looks up by SHA-256 hash."""

    async def test_valid_key_returns_admin(self, test_db_pool):
        import app.database as db_module
        db_module._pool = test_db_pool

        raw_key = "test-api-key-9876"
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

        async with test_db_pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO fazle_admins (phone, name, api_key_hash) VALUES ('8801700000006','KeyAdmin',$1)",
                key_hash,
            )

        from modules.rbac import get_admin_by_api_key

        admin = await get_admin_by_api_key(raw_key)
        assert admin is not None
        assert admin["phone"] == "8801700000006"

    async def test_invalid_key_returns_none(self, test_db_pool):
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.rbac import get_admin_by_api_key

        admin = await get_admin_by_api_key("totally-wrong-key")
        assert admin is None


class TestGetAdminByUsername:
    """get_admin_by_username() resolves active admins by their login name."""

    async def test_valid_username_returns_admin(self, test_db_pool):
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.rbac import get_admin_by_username, hash_password

        async with test_db_pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO fazle_admins (phone, name, username, password_hash) VALUES ('8801700000007','UserAdmin','testuser',$1)",
                hash_password("Password123!"),
            )

        admin = await get_admin_by_username("testuser")
        assert admin is not None
        assert admin["phone"] == "8801700000007"
        assert admin["username"] == "testuser"

    async def test_unknown_username_returns_none(self, test_db_pool):
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.rbac import get_admin_by_username

        admin = await get_admin_by_username("does-not-exist")
        assert admin is None


class TestAddAdminCredentialAttach:
    """add_admin can attach username/password to an existing phone-based admin."""

    async def test_attach_credentials_to_existing_phone_user(self, test_db_pool):
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.rbac import add_admin, get_admin_by_username

        async with test_db_pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO fazle_admins (phone, name, status) VALUES ('8801700000099','Legacy User','active')"
            )

        result = await add_admin(
            "8801700000099",
            "Legacy User",
            role="viewer",
            granted_by="test",
            username="legacyuser",
            password="Legacy#Password1",
        )
        assert result["status"] == "updated_credentials"

        user = await get_admin_by_username("legacyuser")
        assert user is not None
        assert user["phone"] == "8801700000099"
